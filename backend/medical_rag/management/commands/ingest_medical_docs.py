from django.core.management.base import BaseCommand

from medical_rag.ingest import check_urls, ingest


class Command(BaseCommand):
    help = "Load / update the AI assistant's medical knowledge base (corpus files, built-in notes, reviewed library)."

    def add_arguments(self, p):
        p.add_argument("--only", choices=["corpus", "builtin", "library"], action="append",
                       help="Limit to one source (repeatable)")
        p.add_argument("--check-urls", action="store_true", help="Also check that every source link answers (internet)")

    def handle(self, *a, **o):
        counts = ingest(tuple(o["only"]) if o["only"] else ("corpus", "builtin", "library"))
        self.stdout.write(self.style.SUCCESS(
            f"Knowledge base: {counts['created']} created, {counts['updated']} updated, {counts['unchanged']} unchanged."))
        if counts["created"] or counts["updated"]:
            self.stdout.write("New or changed documents are 'Pending clinical review' - approve them in Django admin.")
        if o["check_urls"]:
            bad = {k: v for k, v in check_urls().items() if not v.startswith("HTTP 2") and not v.startswith("HTTP 3")}
            self.stdout.write(f"Source check: {len(bad)} problem(s)")
            for k, v in sorted(bad.items()):
                self.stdout.write(f"  {k}: {v}")
