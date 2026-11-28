"""Unsupervised clustering of de-identified assistant questions -> docs/ai/reports/clusters-<date>.md"""
import json
from datetime import date
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from ai_training import clustering
from medical_rag.models import ChatMessage


class Command(BaseCommand):
    help = "Cluster saved user questions (de-identified) to find recurring topics and knowledge gaps."

    def add_arguments(self, parser):
        parser.add_argument("--min-questions", type=int, default=40)

    def handle(self, *a, min_questions, **kw):
        texts = list(ChatMessage.objects.filter(role="user").values_list("text", flat=True)[:5000])
        source = f"{len(texts)} saved user questions"
        if len(set(texts)) < min_questions:
            from ai_training import project_intents
            texts += [t for t, lab, _ in project_intents.examples()
                      if lab in ("medical_info", "app_help", "tech_support")]
            source += " + project example questions (too few real questions yet)"
        r = clustering.run(texts)
        self.stdout.write(f"{source}; {r['questions']} distinct after de-identification")
        self.stdout.write(f"silhouette by k: {r['silhouette_by_k']} -> k={r['chosen_k']} (s={r['silhouette']}); "
                          f"k-means vs Ward ARI={r['kmeans_vs_ward_adjusted_rand']}")
        for c in r["clusters"]:
            self.stdout.write(f"  [{c['size']:3}] {', '.join(c['top_terms'])}")
        out = Path(settings.BASE_DIR).parent / "docs" / "ai" / "reports"
        out.mkdir(parents=True, exist_ok=True)
        md = [f"# Question clusters {date.today()}", "", f"Data: {source}. De-identified; no user ids.", "",
              f"Silhouette by k: `{r['silhouette_by_k']}` -> k = {r['chosen_k']} (silhouette {r['silhouette']}).",
              f"K-means vs Ward agreement (adjusted Rand index): {r['kmeans_vs_ward_adjusted_rand']}.", "",
              "How to read this: silhouette below 0.25 means weak structure, so treat clusters as rough topic hints. "
              "Clusters are topics, never diagnoses.", ""]
        for c in r["clusters"]:
            md += [f"## Cluster {c['cluster']} ({c['size']} questions): {', '.join(c['top_terms'])}", ""]
            md += [f"- {e}" for e in c["examples"]] + [""]
        md += ["## Near-duplicates (cosine >= 0.9)", "", "```json",
               json.dumps(r["near_duplicates"], indent=1, ensure_ascii=False), "```"]
        path = out / f"clusters-{date.today()}.md"
        path.write_text("\n".join(md) + "\n", encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"Report: {path}"))
