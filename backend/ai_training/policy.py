"""Feedback-based recommendation policy (offline contextual bandit, human-approved).

Why this and not "reinforcement learning": real RL needs an environment where the agent's actions change future
states and rewards can be simulated or explored safely. Here the only learnable decision is WHICH helpful
suggestions to show first, and exploring on real patients is not acceptable. That is a (contextual) bandit
problem, solved OFFLINE from logged feedback, then frozen, evaluated and approved by a person.

Reward (per suggestion shown in a rated answer):
    +1  the user tapped that suggestion            (rating = suggestion)
    +1  answer rated helpful   -> shared by every suggestion shown with it (weight 0.5)
     0  answer rated unhelpful / unclear -> counted as shown without success
Model: Beta(1 + successes, 1 + failures) per suggestion; score = posterior mean (a lower bound is also reported).
Context: role (patient / staff). Emergency answers are EXCLUDED from training and their pinned suggestions are
never reordered - feedback can never teach the system to soften or delay emergency escalation.
"""
import json
import math
from datetime import datetime, timezone
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent / "models"
MIN_EVENTS = 20         # gate: at least this much feedback before a policy can be approved
SAFETY_PINNED = {"emergency_button", "call_hotline"}


def _lower_bound(a, b, z=1.0):
    mean = a / (a + b)
    sd = math.sqrt(a * b / ((a + b) ** 2 * (a + b + 1)))
    return max(0.0, mean - z * sd)


def train(feedback_rows):
    """feedback_rows: iterable of dicts {rating, suggestion, suggestions_shown, emergency}. Returns (path, version,
    metrics, gate)."""
    succ, fail, events, skipped_emergency = {}, {}, 0, 0
    for r in feedback_rows:
        if r["emergency"]:
            skipped_emergency += 1
            continue
        shown = [s for s in (r["suggestions_shown"] or []) if s not in SAFETY_PINNED]
        if r["rating"] == "suggestion" and r["suggestion"] and r["suggestion"] not in SAFETY_PINNED:
            succ[r["suggestion"]] = succ.get(r["suggestion"], 0) + 1
            events += 1
        elif r["rating"] == "helpful":
            for s in shown:
                succ[s] = succ.get(s, 0) + 0.5
            events += 1 if shown else 0
        elif r["rating"] in ("unhelpful", "unclear"):
            for s in shown:
                fail[s] = fail.get(s, 0) + 1
            events += 1 if shown else 0
    ids = sorted(set(succ) | set(fail))
    scores, detail = {}, {}
    for i in ids:
        a, b = 1 + succ.get(i, 0), 1 + fail.get(i, 0)
        scores[i] = round(a / (a + b), 4)
        detail[i] = {"successes": succ.get(i, 0), "failures": fail.get(i, 0), "mean": scores[i],
                     "lower_bound": round(_lower_bound(a, b), 4)}
    version = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    path = MODELS_DIR / f"recommendation-{version}.json"
    path.write_text(json.dumps({"version": version, "scores": scores, "pinned_never_reordered": sorted(SAFETY_PINNED)},
                               indent=2), encoding="utf-8")
    metrics = {"events": events, "skipped_emergency_answers": skipped_emergency, "per_suggestion": detail}
    gate = {"events": events, "events_min": MIN_EVENTS, "safety_pinned_untouched": not (set(scores) & SAFETY_PINNED),
            "passed": events >= MIN_EVENTS and not (set(scores) & SAFETY_PINNED)}
    return path, version, metrics, gate
