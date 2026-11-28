"""Train the offline recommendation policy from logged feedback; registers a CANDIDATE (approve_model to use it)."""
from django.core.management.base import BaseCommand

from ai_training import policy, registry
from medical_rag.models import AssistantFeedback


class Command(BaseCommand):
    help = "Offline Beta-Bernoulli bandit over suggestion feedback (emergency answers excluded)."

    def handle(self, *a, **kw):
        rows = list(AssistantFeedback.objects.exclude(review="unsafe").values(
            "rating", "suggestion", "suggestions_shown", "emergency"))
        path, version, metrics, gate = policy.train(rows)
        mv = registry.register("recommendation", version, path, metrics, gate,
                               {"feedback_rows": len(rows), "excluded": "answers reviewed as unsafe; emergency answers"})
        for k, v in sorted(metrics["per_suggestion"].items(), key=lambda kv: -kv[1]["mean"]):
            self.stdout.write(f"  {k:24} mean={v['mean']} lower={v['lower_bound']} "
                              f"(+{v['successes']} / -{v['failures']})")
        need = f"not passed (need {gate['events_min']} events)"
        style = self.style.SUCCESS if gate["passed"] else self.style.WARNING
        self.stdout.write(style(f"Candidate #{mv.id}: {metrics['events']} feedback events, gate "
                                f"{'PASSED' if gate['passed'] else need}"))
