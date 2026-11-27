"""Arduino USB-serial -> Django bridge.

    serial port (COMx, or socket://127.0.0.1:7000 for the virtual Arduino)
        -> readline -> serial_parser.parse_line -> duplicate filter -> add "received_at"
        -> batch POST {backend}/api/iot/ingest/ (X-Bridge-Token)
             backend down / 5xx -> appended to spool.jsonl and re-sent later (valid readings are never dropped)
             rejected by Django  -> rejected.jsonl + log (bad data never retried forever)

Runtime files live in backend/var/arduino_bridge/:
    bridge.lock    PID of the running bridge (only one bridge may own the serial port)
    status.json    connection state, counters - read by `run_arduino_bridge --status`
    stop.request   created by `run_arduino_bridge --stop`; the bridge exits cleanly when it sees it
    command.txt    created by `run_arduino_bridge --command "..."`; written to the Arduino
    spool.jsonl, rejected.jsonl, bridge.log
"""
import collections
import ctypes
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import requests
import serial
from serial.tools import list_ports

from .serial_parser import FORWARDED, ParseError, parse_line, reading_key

log = logging.getLogger("arduino_bridge")

# USB vendor ids of Arduino boards and the usual USB-serial chips on clones
KNOWN_VIDS = {0x2341: "Arduino", 0x2A03: "Arduino.org", 0x1A86: "CH340 (clone)", 0x10C4: "CP210x", 0x0403: "FTDI"}
BATCH_SIZE = 50
FLUSH_EVERY_S = 1.0
HTTP_TIMEOUT_S = 20  # a hosted database can make each request take several seconds
SPOOL_LIMIT = 50_000  # lines; protects the disk if Django is down for a very long time


def runtime_dir(base):
    d = Path(base) / "var" / "arduino_bridge"
    d.mkdir(parents=True, exist_ok=True)
    return d


def setup_logging(rdir, verbose=False):
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    if log.handlers:
        return
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")
    fh = RotatingFileHandler(rdir / "bridge.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    sh = logging.StreamHandler(sys.stdout)
    for h in (fh, sh):
        h.setFormatter(fmt)
        log.addHandler(h)


def detect_ports():
    """[(device, description, vid)] for every serial port; Arduino-like ports first."""
    out = []
    for p in list_ports.comports():
        out.append((p.device, p.description or "", p.vid))
    return sorted(out, key=lambda t: t[2] not in KNOWN_VIDS)


def auto_port():
    for dev, _desc, vid in detect_ports():
        if vid in KNOWN_VIDS:
            return dev
    return None


# ------------------------------------------------------------------ single-instance lock
def pid_alive(pid):
    if pid <= 0:
        return False
    if os.name == "nt":  # os.kill(pid, 0) would TERMINATE the process on Windows
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def read_pid(rdir):
    try:
        return int((rdir / "bridge.lock").read_text().strip() or 0)
    except (OSError, ValueError):
        return 0


def acquire_lock(rdir):
    lock = rdir / "bridge.lock"
    pid = read_pid(rdir)
    if pid and pid != os.getpid() and pid_alive(pid):
        raise RuntimeError(f"Another bridge is already running (PID {pid}). Stop it with --stop first.")
    lock.write_text(str(os.getpid()))


def release_lock(rdir):
    if read_pid(rdir) == os.getpid():
        (rdir / "bridge.lock").unlink(missing_ok=True)


# ------------------------------------------------------------------ the bridge
class Bridge:
    def __init__(self, port, baud, backend, token, rdir, reconnect_max=30.0, once_seconds=None):
        self.port, self.baud = port, baud
        self.url = backend.rstrip("/")
        self.token = token
        self.rdir = rdir
        self.reconnect_max = reconnect_max
        self.once_seconds = once_seconds  # for tests: stop after this many seconds
        # HTTP runs in a sender thread so a slow backend never stops the serial port being read
        self.http = requests.Session()
        self.http.headers["X-Bridge-Token"] = token
        self.conn_http = requests.Session()  # connection reports from the reader thread
        self.conn_http.headers["X-Bridge-Token"] = token
        self.lock = threading.Lock()         # guards self.pending
        self.send_lock = threading.Lock()    # one batch in flight at a time, in order
        self.sender_stop = threading.Event()
        self.pending = []
        self.seen = collections.OrderedDict()  # recent reading keys (duplicate filter)
        self.devices = set()
        self.last_flush = time.monotonic()
        self.backend_up = None
        self._replaying = False
        self.stats = collections.Counter()
        self.state = "starting"
        self.started = datetime.now(timezone.utc).isoformat()
        self.ser = None

    # ---------------- status file
    def write_status(self, **extra):
        data = {"pid": os.getpid(), "state": self.state, "port": self.port, "baud": self.baud,
                "backend": self.url, "backend_reachable": self.backend_up, "devices": sorted(self.devices),
                "started": self.started, "updated": datetime.now(timezone.utc).isoformat(),
                "spooled": self.spool_count(), "counters": dict(self.stats), **extra}
        tmp = self.rdir / "status.json.tmp"
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, self.rdir / "status.json")

    # ---------------- control files
    def stop_requested(self):
        f = self.rdir / "stop.request"
        if f.exists():
            f.unlink(missing_ok=True)
            return True
        return False

    def pending_command(self):
        f = self.rdir / "command.txt"
        if not f.exists():
            return None
        try:
            cmd = f.read_text(encoding="utf-8").strip()
        finally:
            f.unlink(missing_ok=True)
        return cmd or None

    # ---------------- spool (backend unavailable)
    def spool_count(self):
        try:
            with open(self.rdir / "spool.jsonl", encoding="utf-8") as fh:
                return sum(1 for _ in fh)
        except OSError:
            return 0

    def spool(self, msgs):
        if self.spool_count() + len(msgs) > SPOOL_LIMIT:
            log.error("Spool is full (%d lines) - %d message(s) NOT kept. Start Django!", SPOOL_LIMIT, len(msgs))
            self.stats["spool_overflow"] += len(msgs)
            return
        with open(self.rdir / "spool.jsonl", "a", encoding="utf-8") as fh:
            for m in msgs:
                fh.write(json.dumps(m) + "\n")
        self.stats["spooled"] += len(msgs)

    def replay_spool(self):
        path = self.rdir / "spool.jsonl"
        if not path.exists():
            return True
        msgs = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        sending = path.with_suffix(".sending")
        os.replace(path, sending)
        log.info("Backend reachable again - re-sending %d spooled message(s)", len(msgs))
        for i in range(0, len(msgs), BATCH_SIZE):
            if not self.post(msgs[i:i + BATCH_SIZE]):
                self.spool(msgs[i + BATCH_SIZE:])  # the failed batch was already re-spooled by post()
                sending.unlink(missing_ok=True)
                return False
        sending.unlink(missing_ok=True)
        return True

    # ---------------- backend
    def post(self, msgs):
        """Send a batch. False (and the batch spooled) when Django is unreachable."""
        try:
            r = self.http.post(f"{self.url}/api/iot/ingest/", json={"port": self.port, "messages": msgs},
                               timeout=HTTP_TIMEOUT_S)
        except requests.RequestException as e:
            self.mark_backend(False, f"{type(e).__name__}")
            self.spool(msgs)
            return False
        if r.status_code in (403, 503):
            log.error("Backend refused the bridge: %s", r.json().get("detail", r.text) if r.content else r.status_code)
            self.mark_backend(False, f"HTTP {r.status_code}")
            self.spool(msgs)
            return False
        if r.status_code >= 500 or r.status_code == 429:
            self.mark_backend(False, f"HTTP {r.status_code}")
            self.spool(msgs)
            return False
        if r.status_code != 200:  # 400: the whole batch is malformed - never retry it
            log.error("Backend rejected batch (HTTP %s): %s", r.status_code, r.text[:300])
            self.reject(msgs, f"HTTP {r.status_code}")
            return True
        self.mark_backend(True)
        for m, res in zip(msgs, r.json().get("results", [])):
            outcome = res.get("result")
            self.stats[outcome] += 1
            if outcome == "rejected":
                log.warning("Rejected by Django: %s | %s", res.get("detail"), _short(m))
                self.reject([m], res.get("detail", ""))
            elif outcome == "saved":
                log.info("saved   %s", _short(m))
            elif outcome == "duplicate":
                log.debug("duplicate %s", _short(m))
        return True

    def mark_backend(self, up, why=""):
        if up != self.backend_up:
            if up:
                log.info("Django backend reachable at %s", self.url)
            else:
                log.warning("Django backend NOT reachable (%s) - keeping readings in the spool", why)
        self.backend_up = up
        # also replays a spool left over from an earlier run
        if up and not self._replaying and (self.rdir / "spool.jsonl").exists():
            self._replaying = True
            try:
                self.replay_spool()
            finally:
                self._replaying = False

    def reject(self, msgs, why):
        with open(self.rdir / "rejected.jsonl", "a", encoding="utf-8") as fh:
            for m in msgs:
                fh.write(json.dumps({"reason": why, "message": m}) + "\n")

    def report_connection(self, connected):
        for dev in sorted(self.devices):
            try:
                self.conn_http.post(f"{self.url}/api/iot/connection/",
                                    json={"device_id": dev, "connected": connected, "port": self.port},
                                    timeout=HTTP_TIMEOUT_S)
            except requests.RequestException:
                pass  # the dashboard shows the device as stale by itself after STALE_AFTER

    def flush(self, force=False):
        with self.send_lock:
            while True:
                now = time.monotonic()
                with self.lock:
                    if not self.pending or (not force and len(self.pending) < BATCH_SIZE
                                            and now - self.last_flush < FLUSH_EVERY_S):
                        return
                    batch, self.pending = self.pending[:BATCH_SIZE], self.pending[BATCH_SIZE:]
                self.last_flush = now
                self._send(batch)

    def _sender(self):
        while not self.sender_stop.wait(0.25):
            try:
                self.flush()
            except Exception:  # never let the sender die silently
                log.exception("sender error")

    def _send(self, batch):
        if self.spool_count():  # keep order: older spooled lines go first
            self._replaying = True
            try:
                ok = self.replay_spool()
            finally:
                self._replaying = False
            if not ok:
                self.spool(batch)
                return
        self.post(batch)

    # ---------------- serial
    def handle_line(self, raw):
        self.stats["lines"] += 1
        try:
            msg = parse_line(raw)
        except ParseError as e:
            self.stats["malformed"] += 1
            log.warning("Malformed line ignored (%s): %r", e, raw[:120])
            return
        if msg["type"] not in FORWARDED:
            log.info("Arduino %s: %s", msg["type"], msg.get("message", ""))
            return
        key = reading_key(msg)
        if key:
            if key in self.seen:
                self.stats["duplicate_local"] += 1
                return
            self.seen[key] = None
            if len(self.seen) > 5000:
                self.seen.popitem(last=False)
        if msg["device_id"] not in self.devices:
            self.devices.add(msg["device_id"])
            log.info("Device %s on %s (%s mode)", msg["device_id"], self.port,
                     msg.get("mode") or msg.get("source"))
        if msg["type"] == "hello":
            log.info("Arduino says hello: firmware %s, interval %s ms", msg.get("fw"), msg.get("interval_ms"))
        msg["received_at"] = datetime.now(timezone.utc).isoformat()
        with self.lock:
            self.pending.append(msg)

    def open_serial(self):
        port = self.port
        if port in (None, "", "auto"):
            port = auto_port()
            if not port:
                raise serial.SerialException("no Arduino-like USB serial port found (is the board plugged in?)")
            self.port = port
        # exclusive=True: on Linux/macOS refuse a port another program holds; Windows COM ports are exclusive anyway
        kwargs = {"exclusive": True} if not port.startswith("socket://") and os.name != "nt" else {}
        return serial.serial_for_url(port, baudrate=self.baud, timeout=0.5, **kwargs)

    def run(self):
        delay, deadline = 1.0, (time.monotonic() + self.once_seconds) if self.once_seconds else None
        log.info("Arduino bridge starting: port=%s baud=%s backend=%s", self.port or "auto", self.baud, self.url)
        sender = threading.Thread(target=self._sender, name="bridge-sender", daemon=True)
        sender.start()
        try:
            while True:
                if self.stop_requested() or (deadline and time.monotonic() > deadline):
                    break
                try:
                    self.ser = self.open_serial()
                except (serial.SerialException, OSError, ValueError) as e:
                    self.state = "waiting_for_device"
                    self.write_status(error=str(e))
                    log.warning("Cannot open %s: %s - retrying in %.0f s", self.port or "auto", e, delay)
                    if self._sleep(delay, deadline):
                        break
                    delay = min(delay * 2, self.reconnect_max)
                    continue
                delay = 1.0
                self.state = "connected"
                log.info("Serial port %s open at %s baud", self.port, self.baud)
                self.write_status()
                try:
                    self._read_loop(deadline)
                    break  # stop requested
                except (serial.SerialException, OSError) as e:
                    log.error("Serial connection lost: %s", e)
                    self.stats["disconnects"] += 1
                    self.state = "disconnected"
                    self.report_connection(False)
                    self.write_status(error=str(e))
                finally:
                    try:
                        self.ser.close()
                    except Exception:
                        pass
        except KeyboardInterrupt:
            log.info("Ctrl+C received")
        finally:
            self.sender_stop.set()
            sender.join(timeout=HTTP_TIMEOUT_S * 2)
            self.flush(force=True)
            self.report_connection(False)
            self.state = "stopped"
            self.write_status()
            log.info("Arduino bridge stopped")

    def _read_loop(self, deadline):
        last_status = 0.0
        while True:
            line = self.ser.readline()
            if line:
                self.handle_line(line)
            cmd = self.pending_command()
            if cmd:
                log.info("Sending command to Arduino: %s", cmd)
                self.ser.write((cmd + "\n").encode("ascii", "ignore"))
            if time.monotonic() - last_status > 2:
                self.write_status()
                last_status = time.monotonic()
            if self.stop_requested() or (deadline and time.monotonic() > deadline):
                return

    def _sleep(self, seconds, deadline):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.stop_requested() or (deadline and time.monotonic() > deadline):
                return True
            time.sleep(0.2)
        return False


def _short(m):
    if m.get("type") == "reading":
        v = m.get("value")
        return f"{m['device_id']} #{m.get('seq')} {m.get('sensor_type')}={v if v is not None else '-'}{m.get('unit', '')} " \
               f"[{m.get('source')}/{m.get('status')}]"
    return f"{m.get('device_id')} {m.get('type')}"
