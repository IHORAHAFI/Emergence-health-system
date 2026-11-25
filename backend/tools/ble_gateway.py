"""Python Bluetooth Low Energy gateway: physical sensor -> this PC -> Django API.

Use this when the browser cannot reach a device (for example Firefox, a PC without Chrome,
a kiosk/Raspberry Pi next to the patient, or a device you want to read continuously).
It reads the STANDARD Bluetooth health profiles and posts every measurement to
POST /api/monitoring/<reference>/readings/ with source="bluetooth", exactly like the web page.

Install (once):   pip install bleak requests
Find devices:     python tools/ble_gateway.py scan
Read a device:    python tools/ble_gateway.py read --address AA:BB:CC:DD:EE:FF --kind heart_rate \
                      --emergency EMG-20261122-ABC123 --username paramedic1 --password paramedic123

The account must be allowed to submit for that emergency (the patient with consent, or the
assigned ambulance crew) - the server checks this, the gateway cannot bypass it.

Proprietary devices: if `scan --services` shows no standard health service (0x1809, 0x1810,
0x180D, 0x1822), the device uses its manufacturer's own protocol. You then need the vendor's
service/characteristic UUIDs and byte format (from its documentation or SDK) and must add a
parser to PROFILES below. Do not guess byte formats for medical data.
"""
import argparse
import asyncio
import datetime as dt
import struct
import sys

UUID = "0000{:04x}-0000-1000-8000-00805f9b34fb".format


def sfloat(data, o):
    raw = struct.unpack_from("<H", data, o)[0]
    m, e = raw & 0x0FFF, raw >> 12
    if m in (0x07FF, 0x0800, 0x07FE, 0x0802, 0x0801):
        return None
    if m >= 0x0800:
        m -= 0x1000
    if e >= 0x8:
        e -= 0x10
    return m * 10 ** e


def float32(data, o):
    raw = struct.unpack_from("<I", data, o)[0]
    m, e = raw & 0xFFFFFF, raw >> 24
    if m in (0x7FFFFF, 0x800000, 0x7FFFFE, 0x800002, 0x800001):
        return None
    if m >= 0x800000:
        m -= 0x1000000
    if e >= 0x80:
        e -= 0x100
    return m * 10 ** e


def parse_thermometer(d):
    t = float32(d, 1)
    if t is not None and d[0] & 1:
        t = (t - 32) * 5 / 9
    return {"temperature_c": None if t is None else round(t, 1)}


def parse_bp(d):
    flags = d[0]
    conv = (lambda v: None if v is None else round(v * 7.50062 if flags & 1 else v))
    out = {"systolic_mmhg": conv(sfloat(d, 1)), "diastolic_mmhg": conv(sfloat(d, 3))}
    o = 7 + (7 if flags & 2 else 0)
    if flags & 4:
        pr = sfloat(d, o)
        out["heart_rate_bpm"] = None if pr is None else round(pr)
    return out


def parse_hr(d):
    return {"heart_rate_bpm": struct.unpack_from("<H", d, 1)[0] if d[0] & 1 else d[1]}


def parse_plx(d):
    s, p = sfloat(d, 1), sfloat(d, 3)
    return {"spo2_pct": None if s is None else round(s), "heart_rate_bpm": None if p is None else round(p)}


PROFILES = {  # kind: (service, [measurement characteristics], parser)
    "thermometer": (0x1809, [0x2A1C], parse_thermometer),
    "blood_pressure": (0x1810, [0x2A35], parse_bp),
    "heart_rate": (0x180D, [0x2A37], parse_hr),
    "pulse_oximeter": (0x1822, [0x2A5E, 0x2A5F], parse_plx),
}
NAMES = {0x1809: "Health Thermometer", 0x1810: "Blood Pressure", 0x180D: "Heart Rate", 0x1822: "Pulse Oximeter"}


async def scan(show_services):
    from bleak import BleakScanner
    print("Scanning for 8 seconds...")
    found = await BleakScanner.discover(timeout=8, return_adv=True)
    for dev, adv in sorted(found.values(), key=lambda x: -(x[1].rssi or -999)):
        health = [NAMES[int(u[4:8], 16)] for u in adv.service_uuids if u.endswith("-0000-1000-8000-00805f9b34fb")
                  and int(u[4:8], 16) in NAMES]
        print(f"{dev.address}  rssi={adv.rssi:>4}  {dev.name or '(no name)':<28} "
              f"{'HEALTH: ' + ', '.join(health) if health else ''}")
        if show_services:
            print("        advertised services:", ", ".join(adv.service_uuids) or "-")


class Api:
    def __init__(self, base, username, password, reference):
        import requests
        self.s, self.base, self.ref = requests.Session(), base.rstrip("/"), reference
        r = self.s.post(f"{self.base}/auth/login/", json={"username": username, "password": password}, timeout=15)
        r.raise_for_status()
        self.s.headers["Authorization"] = "Bearer " + r.json()["access"]

    def send(self, values, address, name, kind):
        body = {"source": "bluetooth", "measured_at": dt.datetime.now(dt.timezone.utc).isoformat(), **values,
                "device": {"device_identifier": address, "name": name or kind, "kind": kind}}
        r = self.s.post(f"{self.base}/monitoring/{self.ref}/readings/", json=body, timeout=15)
        return r.status_code, r.json()


async def read(args):
    from bleak import BleakClient
    service, chars, parser = PROFILES[args.kind]
    api = Api(args.api, args.username, args.password, args.emergency)
    stop = asyncio.Event()

    def on_data(_char, data: bytearray, client_name):
        try:
            values = {k: v for k, v in parser(bytes(data)).items() if v is not None}
        except (struct.error, IndexError) as e:
            print("  bad data from device:", e, data.hex())
            return
        if not values:
            print("  reading had no valid value - re-measure")
            return
        code, body = api.send(values, args.address, client_name, args.kind)
        flag = "ALERT " if code == 201 and body.get("risk") in ("low_medium", "medium", "high") else ""
        print(f"  {dt.datetime.now():%H:%M:%S} {values} -> server {code} {flag}{body.get('detail', '')}")

    attempt = 0
    while not stop.is_set() and attempt <= args.retries:
        try:
            async with BleakClient(args.address, timeout=20,
                                   disconnected_callback=lambda c: print("  device disconnected")) as client:
                attempt = 0
                svc_uuids = [s.uuid for s in client.services]
                if UUID(service) not in svc_uuids:
                    print(f"Device does not offer the standard {args.kind} service. Services: {svc_uuids}")
                    return 2
                n = 0
                for c in chars:
                    if client.services.get_characteristic(UUID(c)):
                        await client.start_notify(UUID(c), lambda ch, d: on_data(ch, d, client.address))
                        n += 1
                print(f"Connected to {args.address}; subscribed to {n} characteristic(s). Take a measurement. Ctrl+C to stop.")
                while client.is_connected:
                    await asyncio.sleep(1)
        except Exception as e:  # bleak raises several error types; Ctrl+C is not an Exception
            attempt += 1
            wait = min(30, 2 ** attempt)
            print(f"  connection problem ({e.__class__.__name__}: {e}); retry {attempt}/{args.retries} in {wait}s")
            await asyncio.sleep(wait)
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--services", action="store_true", help="also list every advertised service UUID")
    r = sub.add_parser("read")
    r.add_argument("--address", required=True)
    r.add_argument("--kind", required=True, choices=PROFILES)
    r.add_argument("--emergency", required=True, help="emergency reference, e.g. EMG-20261122-ABC123")
    r.add_argument("--username", required=True)
    r.add_argument("--password", required=True)
    r.add_argument("--api", default="http://127.0.0.1:8000/api")
    r.add_argument("--retries", type=int, default=5)
    a = p.parse_args()
    try:
        import bleak  # noqa: F401
    except ImportError:
        sys.exit("Install the gateway requirements first:  pip install bleak requests")
    try:
        sys.exit(asyncio.run(scan(a.services) if a.cmd == "scan" else read(a)) or 0)
    except KeyboardInterrupt:
        print("Stopped.")


if __name__ == "__main__":
    main()
