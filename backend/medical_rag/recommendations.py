"""Context-aware next-step suggestions for the assistant reply.

Every suggestion is an existing app feature with a short reason. It is a SUGGESTION: nothing is executed - the user
or operator taps it and the normal, permission-checked screen opens. Suggestions are filtered by role, never invent
resources (no "ambulance X is free"), and are not repeated if already shown in the last turns.

Ordering: emergency suggestions are pinned first and never reordered. The rest may be ordered by the ACTIVE,
human-approved recommendation policy (ai_training/policy.py); without one, the fixed order below is used.
"""
import json
import logging

log = logging.getLogger(__name__)

# id: (label, reason, roles, action) - action is a route key the apps map to an existing screen
CATALOG = {
    "emergency_button": ("Use the Emergency button", "Sends your location and request to an operator immediately.",
                         {"patient", "anonymous"}, "emergency"),
    "call_hotline": ("Call the emergency hotline", "A person can help you right now, even without internet.",
                     {"patient", "anonymous", "caregiver"}, "call_hotline"),
    "heart_rate_test": ("Do the Heart Rate Test", "A 60-second camera estimate that is sent to the operator.",
                        {"patient"}, "heart_rate_test"),
    "view_vitals": ("Open Vital signs", "See your latest readings and their history, with where each comes from.",
                    {"patient"}, "vital_signs"),
    "request_test": ("Request a medical test", "An operator can arrange the measurement you need.",
                     {"patient"}, "request_test"),
    "view_results": ("Open Results", "Your previous test results in one place.", {"patient"}, "results"),
    "voice_mode": ("Speak instead of typing", "The voice assistant reads answers aloud too.",
                   {"patient", "anonymous"}, "voice"),
    "nearby_hospitals": ("Find a nearby hospital", "Shows hospitals near you on the map.",
                         {"patient", "anonymous"}, "hospitals"),
    "contact_operator": ("Contact an operator", "A person can check your situation and answer directly.",
                         {"patient"}, "contact_operator"),
    "check_permissions": ("Check app permissions", "Camera, microphone and location must be allowed for these "
                          "features.", {"patient", "operator", "admin", "hospital"}, "settings"),
    "review_new_requests": ("Review new emergency requests", "Unassigned requests wait on the dashboard.",
                            {"operator", "admin"}, "dashboard_emergencies"),
    "check_patient_vitals": ("Check the patient's latest readings", "The Vital signs panel shows each value with "
                             "its source (camera estimate or SIMULATED).", {"operator", "admin", "hospital"},
                             "dashboard_vitals"),
    "verify_reading": ("Verify an abnormal or unreliable reading", "Camera estimates and simulated values are not "
                       "clinical measurements - confirm with the patient or a device.", {"operator", "admin", "hospital"},
                       "dashboard_vitals"),
    "follow_up_unanswered": ("Follow up on unanswered requests", "Requests without a response are listed first.",
                             {"operator", "admin"}, "dashboard_emergencies"),
}

BY_INTENT = {
    "emergency": ["emergency_button", "call_hotline", "nearby_hospitals"],
    "greeting": ["voice_mode", "view_vitals", "request_test"],
    "how_are_you": ["view_vitals", "voice_mode"],
    "capabilities": ["view_vitals", "heart_rate_test", "voice_mode", "request_test"],
    "app_help": ["view_vitals", "heart_rate_test", "request_test", "view_results"],
    "tech_support": ["check_permissions", "contact_operator"],
    "medical_info": ["view_vitals", "contact_operator", "nearby_hospitals"],
    "operator_support": ["review_new_requests", "check_patient_vitals", "verify_reading", "follow_up_unanswered"],
    "out_of_scope": ["view_vitals", "voice_mode"],
    "thanks": [], "goodbye": [],
}
STAFF_DEFAULT = ["review_new_requests", "check_patient_vitals", "follow_up_unanswered"]
PINNED = {"emergency_button", "call_hotline"}
MAX_SUGGESTIONS = 3


def _policy_order():
    """{suggestion_id: score} from the ACTIVE approved policy, or {}."""
    try:
        from ai_training import registry
        mv, path = registry.active_artifact("recommendation")
        if path is None:
            return {}
        cached = _policy_order.cache
        if cached and cached[0] == mv.id:
            return cached[1]
        scores = json.loads(path.read_text(encoding="utf-8")).get("scores", {})
        _policy_order.cache = (mv.id, scores)
        return scores
    except Exception:
        log.exception("recommendation policy unavailable - fixed order")
        return {}


_policy_order.cache = None


def role_of(user):
    if user is None or not getattr(user, "is_authenticated", False):
        return "anonymous"
    return "admin" if user.is_superuser else (getattr(user, "role", "") or "patient")


def suggest(intent, role, emergency=False, recently_shown=(), text=""):
    ids = list(BY_INTENT.get("emergency" if emergency else intent, []))
    if role in ("operator", "admin", "hospital") and intent not in ("emergency", "tech_support", "thanks", "goodbye"):
        ids = BY_INTENT["operator_support"] if intent == "operator_support" else STAFF_DEFAULT + ids
    if intent == "medical_info" and role == "patient" and any(w in text.lower() for w in ("heart", "pulse", "bpm")):
        ids.insert(0, "heart_rate_test")
    allowed = [i for i in dict.fromkeys(ids) if role in CATALOG[i][2]]
    pinned = [i for i in allowed if i in PINNED]
    rest = [i for i in allowed if i not in PINNED and i not in set(recently_shown)]
    scores = _policy_order()
    if scores:
        rest.sort(key=lambda i: -scores.get(i, 0.5))  # stable sort: unknown ids keep their fixed order
    out = []
    for i in (pinned + rest)[:MAX_SUGGESTIONS if not pinned else MAX_SUGGESTIONS + 1]:
        label, reason, _roles, action = CATALOG[i]
        out.append({"id": i, "label": label, "reason": reason, "action": action, "pinned": i in PINNED})
    return out
