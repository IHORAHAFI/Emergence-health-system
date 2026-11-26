import time

from django.core.management.base import BaseCommand

from emergency import scheduler


class Command(BaseCommand):
    help = ("Advance every request's alert schedule, AI takeover, proactive check-ins and virtual devices. "
            "Use as a separate worker (then set ALERT_SCHEDULER=0 on the web service), or once a minute from cron "
            "with --once. The web server also does this by itself (emergency/scheduler.py).")

    def add_arguments(self, p):
        p.add_argument("--once", action="store_true")
        p.add_argument("--every", type=int, default=5, help="seconds between checks")

    def handle(self, *a, **o):
        while True:
            n = scheduler.run_once()
            if n:
                self.stdout.write(f"{n} step(s) taken")
            if o["once"]:
                return
            time.sleep(o["every"])
