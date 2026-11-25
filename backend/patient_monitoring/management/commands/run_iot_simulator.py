"""Runs all RUNNING simulation sessions in the background, without a browser open.

    python manage.py run_iot_simulator            # runs until Ctrl+C
    python manage.py run_iot_simulator --once     # one pass (useful for testing)
"""
import time

from django.core.management.base import BaseCommand

from patient_monitoring import simulation


class Command(BaseCommand):
    help = "Advance every running virtual-patient simulation (virtual IoT devices transmit SIMULATED readings)."

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=float, default=0.5, help="Seconds between checks (default 0.5)")
        parser.add_argument("--once", action="store_true", help="Run one pass and exit")

    def handle(self, *args, interval, once, **kw):
        self.stdout.write(self.style.WARNING("IoT simulator running - all readings are stored as SIMULATED. Ctrl+C to stop."))
        try:
            while True:
                n = simulation.tick_all()
                if n:
                    self.stdout.write(f"{time.strftime('%H:%M:%S')} advanced {n} simulated minute(s)")
                if once:
                    break
                time.sleep(interval)
        except KeyboardInterrupt:
            self.stdout.write("Stopped.")
