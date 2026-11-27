"""Arduino / USB-serial sensor readings -> database -> operator + patient dashboards.

    Arduino --JSON Lines--> serial bridge (run_arduino_bridge) --POST /api/iot/ingest/ (X-Bridge-Token)-->
        ingest(): device registered + approved?  mode matches source?  value plausible?  not a duplicate?
                  -> SensorReading saved -> "iot" nudge to the operators group (+ the assigned patient's group)
    dashboards --GET /dashboard/iot.json  |  GET /api/iot/my-readings/--> always read from the database

The bridge runs in its own process, so it cannot reach the Django server's in-memory channel layer: it posts to
the HTTP endpoint and the server process does the broadcast. A failed broadcast never loses a reading - the
dashboards also poll.
"""
import logging
from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import IoTDevice, SensorReading as SR

log = logging.getLogger(__name__)

RULES = {  # sensor_type: (unit, min, max, step) - must agree with the sketch
    "temperature": ("C", 30, 43, Decimal("0.1")),
    "heart_rate": ("bpm", 30, 220, Decimal("1")),
    "spo2": ("%", 70, 100, Decimal("1")),
}
STORED_STATUSES = {"VALID", "INVALID", "SENSOR_ERROR"}
STATUSES = STORED_STATUSES | {"NOT_CONNECTED"}
MAX_AGE = timedelta(days=7)        # the bridge may replay readings it spooled while Django was down
STALE_AFTER = timedelta(seconds=30)  # a device silent for longer than this is shown as offline


class Rejected(Exception):
    pass


def _device(device_id, pending_ok=False):
    if not isinstance(device_id, str) or not device_id.strip() or len(device_id) > 64:
        raise Rejected("Missing or invalid device_id.")
    dev = IoTDevice.objects.filter(device_id=device_id).first()
    if dev is None:  # remember it so an administrator can review and approve it; accept nothing yet
        IoTDevice.objects.get_or_create(device_id=device_id, defaults={"name": device_id, "approved": False})
        raise Rejected(f"Unknown device {device_id}: registered as pending - approve it in Django admin.")
    if not dev.approved and not pending_ok:
        raise Rejected(f"Device {device_id} is waiting for administrator approval.")
    return dev


def _when(raw):
    now = timezone.now()
    at = parse_datetime(raw) if isinstance(raw, str) else None
    if at is None:
        raise Rejected("received_at (bridge timestamp) is missing or not ISO 8601.")
    if timezone.is_naive(at):
        raise Rejected("received_at must include a timezone.")
    if at > now + timedelta(minutes=2):
        raise Rejected("Timestamp is in the future - check the computer clock.")
    if at < now - MAX_AGE:
        raise Rejected("Reading is too old.")
    return at


def _touch(dev, msg, at, port):
    dev.last_seen = max(filter(None, [dev.last_seen, at]))
    dev.connected = True
    if port:
        dev.port = str(port)[:64]
    if msg.get("boot_id"):
        dev.boot_id = str(msg["boot_id"])[:16]
    if msg.get("type") == "hello":
        dev.firmware = str(msg.get("fw") or "")[:20]
        if isinstance(msg.get("interval_ms"), int):
            dev.interval_ms = msg["interval_ms"]


def _check(msg, dev, port):
    """Validate one message for an already-loaded device. Updates `dev` in memory (saved once per batch).
    Returns None for status-only messages or an unsaved SensorReading; raises Rejected."""
    kind = msg.get("type")
    at = _when(msg.get("received_at"))
    mode = msg.get("source") if kind == "reading" else msg.get("mode")
    if mode not in ("REAL", "SIMULATED"):
        raise Rejected("source/mode must be REAL or SIMULATED.")
    if mode != dev.mode:
        raise Rejected(f"Device {dev.device_id} is registered as {dev.mode} but sent {mode} data - refused.")
    if kind != "reading":
        _touch(dev, msg, at, port)
        return None

    sensor, status, value = msg.get("sensor_type"), msg.get("status"), msg.get("value")
    if sensor not in RULES:
        raise Rejected(f"Unknown sensor_type {sensor!r}.")
    unit, lo, hi, step = RULES[sensor]
    if msg.get("unit") != unit:
        raise Rejected(f"{sensor} must be sent in {unit}.")
    if status not in STATUSES:
        raise Rejected(f"Unknown status {status!r}.")
    boot_id, seq = msg.get("boot_id"), msg.get("seq")
    if not isinstance(boot_id, str) or not boot_id or len(boot_id) > 16:
        raise Rejected("boot_id is required.")
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
        raise Rejected("seq must be a non-negative integer.")
    if status == "VALID":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise Rejected("A VALID reading needs a numeric value.")
        if not lo <= value <= hi:
            raise Rejected(f"{value} {unit} is outside the plausible {sensor} range {lo}-{hi}.")
        stored = Decimal(str(value)).quantize(step)
    else:
        if value is not None:
            raise Rejected(f"A {status} reading must not carry a value.")
        stored = None

    _touch(dev, msg, at, port)
    dev.sensor_state = {**dev.sensor_state, sensor: status}
    if status == "NOT_CONNECTED":  # nothing measured: device status only
        return None
    uptime = msg.get("uptime_ms")
    return SR(device=dev, patient_id=dev.patient_id, sensor_type=sensor, value=stored, unit=unit, source=mode,
              status=status, boot_id=boot_id, seq=seq, measured_at=at,
              uptime_ms=uptime if isinstance(uptime, int) and not isinstance(uptime, bool) and uptime >= 0 else None)


def ingest_batch(msgs, port=""):
    """Messages from the bridge -> [{"result": "saved"|"duplicate"|"status"|"rejected", "detail"?}], same order.
    Few queries per batch whatever its size (the database may be remote): one device lookup per device,
    one duplicate lookup, one bulk insert, one device update."""
    results, devices, dev_errors, new = [None] * len(msgs), {}, {}, []
    for i, msg in enumerate(msgs):
        try:
            if not isinstance(msg, dict):
                raise Rejected("Message must be a JSON object.")
            if msg.get("type") not in ("reading", "hello", "heartbeat"):
                raise Rejected(f"Unsupported message type {msg.get('type')!r}.")
            did = msg.get("device_id")
            key = did if isinstance(did, str) else repr(did)
            if key in dev_errors:
                raise Rejected(dev_errors[key])
            if key not in devices:
                try:
                    devices[key] = _device(did)
                except Rejected as e:
                    dev_errors[key] = str(e)
                    raise
            r = _check(msg, devices[key], port)
            if r is None:
                results[i] = {"result": "status"}
            else:
                new.append((i, r))
        except Rejected as e:
            results[i] = {"result": "rejected", "detail": str(e)}

    if new:  # duplicates: already stored, or repeated inside this batch
        from django.db.models import Q
        q = Q()
        for _, r in new:
            q |= Q(device_id=r.device_id, boot_id=r.boot_id, seq=r.seq)
        stored = set(SR.objects.filter(q).values_list("device_id", "boot_id", "seq"))
        fresh = []
        for i, r in new:
            k = (r.device_id, r.boot_id, r.seq)
            if k in stored:
                results[i] = {"result": "duplicate"}
            else:
                stored.add(k)
                fresh.append((i, r))
        if fresh:
            rows = [r for _, r in fresh]
            try:
                with transaction.atomic():
                    SR.objects.bulk_create(rows)
            except IntegrityError:  # a concurrent request stored some of them a moment ago
                SR.objects.bulk_create(rows, ignore_conflicts=True)
                for _, r in fresh:
                    r.pk = None
            for i, _ in fresh:
                results[i] = {"result": "saved"}
            saved = [r for _, r in fresh]
            for pid in {r.patient_id for r in saved}:
                ids = [r.pk for r in saved if r.patient_id == pid]
                _nudge(ids[-1] if len(ids) == 1 else None, pid)
    for dev in devices.values():
        dev.save(update_fields=["last_seen", "connected", "port", "boot_id", "firmware", "interval_ms",
                                "sensor_state"])
    return results


def ingest(msg, port=""):
    """One message. Returns "saved" | "duplicate" | "status"; raises Rejected."""
    res = ingest_batch([msg], port)[0]
    if res["result"] == "rejected":
        raise Rejected(res["detail"])
    return res["result"]


def set_connection(device_id, connected, port=""):
    """The bridge reports that the serial link to a device opened or dropped."""
    dev = _device(device_id, pending_ok=True)
    dev.connected = bool(connected)
    if port:
        dev.port = str(port)[:64]
    dev.save(update_fields=["connected", "port"])
    _nudge(None, dev.patient_id)
    return dev


def _nudge(reading_id, patient_id):
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer
        layer = get_channel_layer()
        if layer is None:
            return
        event = {"type": "notify.event", "kind": "iot", "id": reading_id}
        async_to_sync(layer.group_send)("operators", event)
        if patient_id:
            async_to_sync(layer.group_send)(f"patient_{patient_id}", event)
    except Exception:
        log.exception("IoT push failed (reading stored; dashboards pick it up by polling)")


def device_dict(dev, now=None):
    now = now or timezone.now()
    stale_after = max(STALE_AFTER, timedelta(milliseconds=3 * (dev.interval_ms or 0)))
    online = bool(dev.connected and dev.last_seen and now - dev.last_seen <= stale_after)
    return {
        "device_id": dev.device_id, "name": dev.name or dev.device_id, "mode": dev.mode, "approved": dev.approved,
        "online": online, "connected": dev.connected, "port": dev.port, "firmware": dev.firmware,
        "last_seen": dev.last_seen.isoformat() if dev.last_seen else None, "sensor_state": dev.sensor_state,
        "patient": (dev.patient.get_full_name() or dev.patient.username) if dev.patient else None,
        "stale_after_s": int(stale_after.total_seconds()),
    }


def staff_feed(user, limit=60):
    """Operators/admins: every device and the newest readings."""
    if not (user.is_superuser or user.role in ("operator", "admin")):
        return {"devices": [], "readings": []}
    now = timezone.now()
    devices = [device_dict(d, now) for d in IoTDevice.objects.select_related("patient")]
    readings = [r.as_dict() for r in SR.objects.select_related("device", "patient")[:limit]]
    return {"devices": devices, "readings": readings, "server_time": now.isoformat(),
            "stale_after_s": int(STALE_AFTER.total_seconds())}


def patient_feed(patient, limit=30):
    """A patient sees only readings from devices assigned to them (never another patient's)."""
    now = timezone.now()
    qs = SR.objects.select_related("device", "patient").filter(patient=patient)[:limit]
    return {"devices": [device_dict(d, now) for d in IoTDevice.objects.filter(patient=patient)],
            "readings": [r.as_dict() for r in qs], "server_time": now.isoformat(),
            "stale_after_s": int(STALE_AFTER.total_seconds())}
