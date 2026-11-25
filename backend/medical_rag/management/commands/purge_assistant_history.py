from django.core.management.base import BaseCommand

from medical_rag.service import history_days, purge_old


class Command(BaseCommand):
    help = "Delete stored AI-assistant conversations older than ASSISTANT_HISTORY_DAYS (run daily)."

    def add_arguments(self, p):
        p.add_argument("--days", type=int, default=None)

    def handle(self, *a, **o):
        n = purge_old(o["days"])
        self.stdout.write(f"Deleted {n} rows older than {o['days'] or history_days()} days.")
