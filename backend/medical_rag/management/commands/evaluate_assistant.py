import json
import os

from django.core.management.base import BaseCommand

from medical_rag import evaluation, retriever


class Command(BaseCommand):
    help = ("Run the assistant's evaluation set (terminology, diseases, symptoms, treatment, medication safety, "
            "emergencies, insufficient information, out-of-scope, languages). Extractive mode by default (free).")

    def add_arguments(self, p):
        p.add_argument("--llm", action="store_true",
                       help="Use the Claude model if ANTHROPIC_API_KEY is set (billed API calls)")
        p.add_argument("--json", help="Write detailed results to this file")

    def handle(self, *a, **o):
        if not o["llm"]:
            os.environ.pop("ANTHROPIC_API_KEY", None)
            os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
        retriever.answer("warm up")  # build the index once so timings measure answering, not indexing
        rows, summary = evaluation.run(retriever.answer)
        for r in rows:
            mark = "PASS" if not r["problems"] else "FAIL"
            self.stdout.write(f"[{mark}] {r['category']:<13} {r['ms']:7.0f} ms  {r['question']}")
            for p in r["problems"]:
                self.stdout.write(f"         - {p}")
        self.stdout.write("\nCategory        passed  median ms")
        total = passed = 0
        for c, s in summary.items():
            self.stdout.write(f"{c:<15} {s['passed']:>2}/{s['total']:<3} {s['median_ms']:9.0f}")
            total, passed = total + s["total"], passed + s["passed"]
        self.stdout.write(self.style.SUCCESS(f"\nOverall: {passed}/{total} checks passed"))
        if o["json"]:
            with open(o["json"], "w", encoding="utf-8") as f:
                json.dump({"rows": rows, "summary": summary}, f, indent=2)
