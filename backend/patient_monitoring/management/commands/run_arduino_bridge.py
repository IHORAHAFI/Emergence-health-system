"""Arduino USB-serial bridge (separate process - Django keeps working with or without it).

    python manage.py run_arduino_bridge --list-ports            # which COM ports exist
    python manage.py run_arduino_bridge --port COM3             # real board
    python manage.py run_arduino_bridge --port socket://127.0.0.1:7000   # virtual Arduino (simulation)
    python manage.py run_arduino_bridge --status                # is it running? connected?
    python manage.py run_arduino_bridge --stop                  # ask the running bridge to stop
    python manage.py run_arduino_bridge --command "SCENARIO FEVER"   # send a command to the board

Defaults come from backend/.env: ARDUINO_PORT (or "auto"), ARDUINO_BAUD (115200), ARDUINO_BACKEND_URL.
"""
import json
import os
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from patient_monitoring.bridge import serial_bridge as sb


class Command(BaseCommand):
    help = "Read Arduino JSON Lines from a serial port and send them to the Django IoT API."

    def add_arguments(self, p):
        p.add_argument("--port", default=os.getenv("ARDUINO_PORT") or "auto",
                       help="COM3, /dev/ttyACM0, socket://127.0.0.1:7000, or auto (default)")
        p.add_argument("--baud", type=int, default=int(os.getenv("ARDUINO_BAUD") or 115200))
        p.add_argument("--backend", default=os.getenv("ARDUINO_BACKEND_URL") or "http://127.0.0.1:8000")
        p.add_argument("--list-ports", action="store_true")
        p.add_argument("--status", action="store_true")
        p.add_argument("--stop", action="store_true")
        p.add_argument("--command", help="Text command for the Arduino, e.g. \"SCENARIO HYPOXIA\"")
        p.add_argument("--seconds", type=float, help="Run for N seconds then exit (testing)")
        p.add_argument("--verbose", action="store_true")

    def handle(self, *a, **o):
        rdir = sb.runtime_dir(settings.BASE_DIR)
        if o["list_ports"]:
            ports = sb.detect_ports()
            if not ports:
                self.stdout.write("No serial ports found. Plug in the board (use a data USB cable).")
            for dev, desc, vid in ports:
                tag = f"  <- {sb.KNOWN_VIDS[vid]}" if vid in sb.KNOWN_VIDS else ""
                self.stdout.write(f"{dev:10} {desc}{tag}")
            return
        if o["status"]:
            return self._status(rdir)
        if o["stop"]:
            pid = sb.read_pid(rdir)
            if not pid or not sb.pid_alive(pid):
                self.stdout.write("The bridge is not running.")
                return
            (rdir / "stop.request").write_text("stop")
            for _ in range(50):
                if not sb.pid_alive(pid):
                    self.stdout.write(self.style.SUCCESS("Bridge stopped."))
                    return
                time.sleep(0.2)
            self.stdout.write(self.style.WARNING(f"Stop requested; PID {pid} has not exited yet."))
            return
        if o["command"]:
            pid = sb.read_pid(rdir)
            if not pid or not sb.pid_alive(pid):
                raise CommandError("The bridge is not running - start it first.")
            (rdir / "command.txt").write_text(o["command"].strip(), encoding="utf-8")
            self.stdout.write(f"Command queued for the Arduino: {o['command']}  (see bridge log for the reply)")
            return

        token = getattr(settings, "ARDUINO_BRIDGE_TOKEN", "")
        if not token:
            raise CommandError("ARDUINO_BRIDGE_TOKEN is not set in backend/.env.")
        (rdir / "stop.request").unlink(missing_ok=True)
        try:
            sb.acquire_lock(rdir)
        except RuntimeError as e:
            raise CommandError(str(e))
        sb.setup_logging(rdir, o["verbose"])
        try:
            sb.Bridge(o["port"], o["baud"], o["backend"], token, rdir, once_seconds=o["seconds"]).run()
        finally:
            sb.release_lock(rdir)

    def _status(self, rdir):
        pid = sb.read_pid(rdir)
        running = bool(pid and sb.pid_alive(pid))
        try:
            st = json.loads((rdir / "status.json").read_text())
        except (OSError, ValueError):
            st = {}
        self.stdout.write(f"Bridge running : {'YES (PID %d)' % pid if running else 'no'}")
        if not st:
            return
        self.stdout.write(f"State          : {st.get('state') if running else 'stopped'}")
        self.stdout.write(f"Port / baud    : {st.get('port')} @ {st.get('baud')}")
        self.stdout.write(f"Backend        : {st.get('backend')} reachable={st.get('backend_reachable')}")
        self.stdout.write(f"Devices        : {', '.join(st.get('devices') or []) or '-'}")
        self.stdout.write(f"Spooled        : {st.get('spooled')} (waiting for the backend)")
        self.stdout.write(f"Counters       : {st.get('counters')}")
        if st.get("error"):
            self.stdout.write(f"Last error     : {st['error']}")
        self.stdout.write(f"Updated        : {st.get('updated')}   Log: {rdir / 'bridge.log'}")
