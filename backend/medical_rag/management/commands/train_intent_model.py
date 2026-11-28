"""Train the supervised intent classifier and register it as a CANDIDATE (not used until approved).

    python manage.py train_intent_model                 # downloads CLINC150 once (2.5 MB), trains, evaluates
    python manage.py train_intent_model --no-clinc      # project examples only
"""
import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from ai_training import datasets, intent, registry


class Command(BaseCommand):
    help = "Train + evaluate the intent classifier; registers a candidate ModelVersion and writes a report."

    def add_arguments(self, parser):
        parser.add_argument("--no-clinc", action="store_true")
        parser.add_argument("--seed", type=int, default=13)

    def handle(self, *a, no_clinc, seed, **kw):
        if not no_clinc:
            path = datasets.download_clinc()
            self.stdout.write(f"CLINC150: {path} sha256={datasets.sha256(path)[:16]}...")
        rows, report = datasets.build(use_clinc=not no_clinc, seed=seed)
        self.stdout.write(f"Data: {report['rows']} rows after cleaning ({report['dropped_duplicates']} duplicates, "
                          f"{report['dropped_empty']} empty removed, {report['label_conflicts']} label conflicts)")
        self.stdout.write("Labels: " + json.dumps(report["label_counts"]))
        path, version, metrics, gate = intent.train(rows, report, seed=seed)
        mv = registry.register("intent", version, path, metrics, gate, report)
        t = metrics["test"]
        self.stdout.write(metrics["report_text"])
        self.stdout.write(f"TEST accuracy={t['accuracy']} macro-F1={t['macro_f1']} "
                          f"emergency recall={gate['emergency_recall']} "
                          f"precision={t['per_label']['emergency']['precision']}")
        self.stdout.write(f"Missed emergencies in test set: {metrics['test_emergency_missed']}")
        out = Path(settings.BASE_DIR).parent / "docs" / "ai" / "reports"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"intent-{version}.md").write_text(_markdown(version, metrics, gate, report), encoding="utf-8")
        style = self.style.SUCCESS if gate["passed"] else self.style.ERROR
        self.stdout.write(style(f"Candidate #{mv.id} intent {version}: gate {'PASSED' if gate['passed'] else 'FAILED'}."
                                f" Approve with: python manage.py approve_model {mv.id} --by <admin username>"))


def _markdown(version, m, gate, data):
    t = m["test"]
    lines = [f"# Intent classifier {version}", "", f"Model: `{m['model']}`, seed {m['seed']}.", "",
             f"Split: {m['split_sizes']}", "", "## Data", "", "```json", json.dumps(data, indent=2, ensure_ascii=False),
             "```", "", "## Test results (held-out set, used once)", "",
             f"accuracy **{t['accuracy']}**, macro precision {t['macro_precision']}, macro recall {t['macro_recall']}, "
             f"macro F1 **{t['macro_f1']}** (train macro F1 {m['train']['macro_f1']}, "
             f"validation {m['validation']['macro_f1']})",
             "", "| label | precision | recall | F1 | support |", "|---|---|---|---|---|"]
    lines += [f"| {k} | {v['precision']} | {v['recall']} | {v['f1']} | {v['support']} |"
              for k, v in t["per_label"].items()]
    cm = m["confusion_matrix"]
    lines += ["", "## Confusion matrix (rows = true, columns = predicted)", "",
              "| | " + " | ".join(cm["labels"]) + " |", "|---" * (len(cm["labels"]) + 1) + "|"]
    lines += [f"| **{lab}** | " + " | ".join(str(v) for v in row) + " |" for lab, row in zip(cm["labels"], cm["matrix"])]
    lines += ["", "## Emergency errors", "", f"Missed (false negatives): {m['test_emergency_missed'] or 'none'}", "",
              f"False alarms: {m['test_false_emergency'] or 'none'}", "", "## Gate", "", "```json",
              json.dumps(gate, indent=2), "```", "",
              "Limitations: project examples are hand-written (optimistic scores); Kinyarwanda examples are few and "
              "not yet reviewed by a native speaker; CLINC150 is general-domain English. The safety rules always run "
              "before this model and it can never remove an emergency they found."]
    return "\n".join(lines) + "\n"
