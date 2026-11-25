import time

from django.core.management.base import BaseCommand

from emergency import escalation


class Command(BaseCommand):
    help = ("Advance the operator siren schedule and run AI escalation even when no dashboard is open. "
            "Run as a background worker, or once a minute from cron with --once.")

    def add_arguments(self, p):
        p.add_argument("--once", action="store_true")
        p.add_argument("--every", type=int, default=15, help="seconds between checks")

    def handle(self, *a, **o):
        while True:
            n = escalation.tick()
            if n:
                self.stdout.write(f"{n} alert(s) advanced")
            if o["once"]:
                return
            time.sleep(o["every"])
