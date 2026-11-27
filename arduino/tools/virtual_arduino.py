"""Virtual Arduino - stands in for emergency_monitor.ino when no physical board is connected.

It speaks exactly the same JSON Lines protocol as the sketch in SIMULATION_MODE, but over a local TCP socket
instead of a COM port. pyserial opens it like a serial port, so the bridge code path is the same:

    python arduino\\tools\\virtual_arduino.py                      # listens on 127.0.0.1:7000
    python manage.py run_arduino_bridge --port socket://127.0.0.1:7000

Every reading is labelled "source":"SIMULATED". Nothing here is a real measurement.

Options for testing the bridge:
    --interval 5        seconds between samples
    --scenario FEVER    NORMAL | FEVER | HYPOXIA | TACHYCARDIA (also changeable live with the SCENARIO command)
    --glitch 0.1        probability of sending a corrupted line (tests malformed-message handling)
    --drop-after 30     close the connection after N seconds (tests reconnection)
Standard library only.
"""
import argparse
import json
import random
import socket
import threading
import time

FW = "1.0.0-virtual"
SCENARIOS = {"NORMAL": (36.8, 76, 98), "FEVER": (39.2, 108, 96), "HYPOXIA": (37.1, 118, 86),
             "TACHYCARDIA": (37.0, 145, 95)}
SENSORS = [  # type, unit, min, max, decimals, jitter - identical to the sketch
    ("temperature", "C", 30.0, 43.0, 1, 0.05),
    ("heart_rate", "bpm", 30.0, 220.0, 0, 1.5),
    ("spo2", "%", 70.0, 100.0, 0, 0.4),
]


class VirtualBoard:
    def __init__(self, device_id, interval_s, scenario):
        self.device_id = device_id
        self.interval_ms = int(interval_s * 1000)
        self.boot_ms = time.monotonic()
        self.boot_id = f"{random.getrandbits(32):08X}"
        self.seq = 0
        self.base = list(SCENARIOS[scenario])
        self.value = list(self.base)
        self.lock = threading.Lock()

    def uptime(self):
        return int((time.monotonic() - self.boot_ms) * 1000)

    def header(self, kind):
        return {"type": kind, "device_id": self.device_id, "boot_id": self.boot_id}

    def hello(self):
        return {**self.header("hello"), "fw": FW, "mode": "SIMULATED", "interval_ms": self.interval_ms,
                "sensors": [s[0] for s in SENSORS]}

    def sample(self):
        out = []
        with self.lock:
            for i, (kind, unit, lo, hi, dec, jit) in enumerate(SENSORS):
                self.value[i] += random.uniform(-1, 1) * jit + (self.base[i] - self.value[i]) * 0.2
                v = min(self.value[i], 100.0) if kind == "spo2" else self.value[i]
                status = "VALID" if lo <= v <= hi else "INVALID"
                self.seq += 1
                out.append({**self.header("reading"), "seq": self.seq, "uptime_ms": self.uptime(),
                            "sensor_type": kind, "value": (round(v, dec) if dec else int(round(v))) if status == "VALID" else None,
                            "unit": unit, "source": "SIMULATED", "status": status})
        out.append({**self.header("heartbeat"), "mode": "SIMULATED", "uptime_ms": self.uptime()})
        return out

    def command(self, text):
        cmd = text.strip().upper()
        if cmd.startswith("INTERVAL "):
            try:
                ms = int(cmd[9:])
            except ValueError:
                ms = 0
            if 1000 <= ms <= 60000:
                self.interval_ms = ms
                return {"type": "ack", "device_id": self.device_id, "message": "interval changed"}
            return {"type": "error", "device_id": self.device_id, "message": "interval must be 1000..60000 ms"}
        if cmd == "STATUS":
            return self.hello()
        if cmd.startswith("SCENARIO "):
            name = cmd[9:]
            if name not in SCENARIOS:
                return {"type": "error", "device_id": self.device_id, "message": "unknown scenario"}
            with self.lock:
                self.base = list(SCENARIOS[name])
            return {"type": "ack", "device_id": self.device_id, "message": "scenario changed"}
        return {"type": "error", "device_id": self.device_id, "message": "unknown command"} if cmd else None


def send(conn, obj, glitch):
    line = json.dumps(obj, separators=(",", ":"))
    if glitch and random.random() < glitch:
        line = line[: random.randint(5, len(line) - 5)]  # truncated line, like serial noise
    conn.sendall((line + "\n").encode())


def serve_client(conn, args):
    # opening the port of a real Uno resets it: imitate that with a new boot_id and seq starting again
    board = VirtualBoard(args.device_id, args.interval, args.scenario)
    stop = threading.Event()

    def reader():
        buf = b""
        while not stop.is_set():
            try:
                data = conn.recv(256)
            except OSError:
                break
            if not data:
                break
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                reply = board.command(line.decode(errors="ignore"))
                if reply:
                    try:
                        send(conn, reply, 0)
                    except OSError:
                        stop.set()
        stop.set()

    threading.Thread(target=reader, daemon=True).start()
    started = time.monotonic()
    try:
        send(conn, board.hello(), 0)
        print(f"  boot_id {board.boot_id} - streaming SIMULATED readings every {board.interval_ms} ms")
        next_at = time.monotonic() + board.interval_ms / 1000
        while not stop.is_set():
            if args.drop_after and time.monotonic() - started > args.drop_after:
                print("  --drop-after reached: closing the connection (simulated USB unplug)")
                break
            if time.monotonic() >= next_at:
                for m in board.sample():
                    send(conn, m, args.glitch)
                next_at += board.interval_ms / 1000
            time.sleep(0.05)
    except OSError as e:
        print(f"  client gone: {e}")
    finally:
        stop.set()
        conn.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7000)
    ap.add_argument("--device-id", default="ARDUINO_DEVICE_01")
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--scenario", default="NORMAL", choices=list(SCENARIOS))
    ap.add_argument("--glitch", type=float, default=0.0)
    ap.add_argument("--drop-after", type=float, default=0.0)
    args = ap.parse_args()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.host, args.port))
    srv.listen(1)
    print(f"Virtual Arduino {args.device_id} (SIMULATED) listening on socket://{args.host}:{args.port}  - Ctrl+C to stop")
    try:
        while True:
            conn, addr = srv.accept()  # one client at a time, like a real COM port
            print(f"Bridge connected from {addr[0]}:{addr[1]}")
            serve_client(conn, args)
            print("Bridge disconnected - waiting for a new connection")
    except KeyboardInterrupt:
        print("stopped")
    finally:
        srv.close()


if __name__ == "__main__":
    main()
