"""Siren schedule and AI escalation for emergencies no operator has accepted.

Schedule: see emergency/alert_workflow.py (alerts 1-3 every ALERT_INTERVAL, AI takeover one interval after
alert 3 - this module's escalate() IS the takeover for emergencies). Stored on EmergencyRequest, so a browser
refresh or a server restart never resets it.
  Severe symptoms escalate immediately on arrival (no waiting for three alerts).
  The siren rings 3 times; the request then stays visible in red ("AI takeover - waiting for a human") until accepted.

What the AI escalation does (and does not do):
  * reads THIS emergency and its consented vital signs, labelling each reading REAL or SIMULATED;
  * does NOT start a medical test itself: in this system only an operator can start a test, and the virtual
    devices produce SIMULATED values that must never be presented as a real patient's measurements. If the
    patient consented and owns a real device, it asks the patient to take a measurement with it;
  * explains the readings in plain words (no diagnosis, no prescription, no claim of treatment);
  * sends the patient first-aid guidance from the reviewed knowledge base + "help is being arranged";
  * notifies operators and the receiving hospital's staff;
  * never closes the request - it stays open until a human healthcare professional takes responsibility.

Ticks: every operator-feed poll calls tick(); `python manage.py run_alert_monitor` does the same in a loop so
escalation also happens when no dashboard is open. All updates are conditional, so concurrent ticks are safe.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.db.models import F
from django.utils import timezone

from .models import EmergencyRequest

log = logging.getLogger(__name__)


def interval():
    return timedelta(seconds=int(getattr(settings, "ALERT_INTERVAL_SECONDS", 120)))


def escalate_after():
    return int(getattr(settings, "ALERT_ESCALATE_AFTER", 3))


def unhandled():
    return EmergencyRequest.objects.filter(accepted_at__isnull=True).exclude(status__in=["completed", "cancelled"])


# ------------------------------------------------------------------ severity
def severe_reasons(em):
    r = []
    if em.priority == "critical":
        r.append("critical priority")
    if not em.conscious or em.patient_condition == "unresponsive":
        r.append("not conscious / not responding")
    if em.breathing_difficulty:
        r.append("difficulty breathing")
    if em.severe_bleeding:
        r.append("severe bleeding")
    if em.chest_pain:
        r.append("chest pain")
    if em.seizure:
        r.append("seizure")
    if em.emergency_type == "stroke":
        r.append("possible stroke")
    return r


# ------------------------------------------------------------------ schedule
def start(em):
    """Alert 1 at arrival (shared alert workflow); severe requests are escalated to humans at once."""
    from . import alert_workflow
    alert_workflow.start(em)
    em.refresh_from_db()
    if severe_reasons(em):
        escalate(em, "severe_symptoms")
    return em


def tick(now=None):
    """Kept for existing callers: the shared workflow handles emergencies, tests and service requests."""
    from . import alert_workflow
    return alert_workflow.tick(now)


def next_alert_at(em):
    return (em.last_alert_at + interval()) if em.last_alert_at else None


# ------------------------------------------------------------------ escalation
GUIDANCE_QUESTION = {
    "cardiac": "chest pain heart attack what to do while waiting",
    "respiratory": "severe difficulty breathing what to do",
    "stroke": "stroke signs FAST what to do",
    "trauma": "severe bleeding first aid",
    "burns": "burns first aid",
    "poisoning": "poisoning what to do",
    "maternity": "danger signs in pregnancy",
    "other": "when to seek emergency medical help",
}


def _vitals(em):
    """Consented readings for THIS emergency, newest first, each labelled REAL or SIMULATED."""
    from patient_monitoring import services as mon
    from patient_monitoring.clinical import RISK_LABEL
    if not mon.has_consent(em):
        return [], "The patient has not consented to share vital signs for this emergency."
    out = []
    for r in em.vitals.select_related("device")[:5]:
        out.append({"label": "SIMULATED" if r.is_simulated else "REAL", "summary": mon.vitals_text(r),
                    "risk": RISK_LABEL.get(r.risk, r.risk or "not scored"), "at": r.measured_at.isoformat(),
                    "flags": r.threshold_flags})
    return out, "" if out else "No vital signs have been received for this emergency yet."


def _explain(readings):
    """Plain-language explanation of the latest reading - general meaning only, never a diagnosis."""
    if not readings:
        return ""
    r = readings[0]
    text = f"Latest {r['label']} reading: {r['summary']}."
    if r["label"] == "SIMULATED":
        text += " These are SIMULATED demonstration values, not real measurements."
    if r["flags"]:
        text += (" Some values are outside the usual range (" + ", ".join(f"{k} {v}" for k, v in r["flags"].items())
                 + "). A health professional will look at this.")
    else:
        text += " A health professional will review these numbers."
    return text


def _test_plan(em):
    """Whether a measurement can be requested. The AI never starts simulated tests for a real patient."""
    from patient_monitoring import services as mon
    from patient_monitoring.models import MedicalDevice
    if not mon.has_consent(em):
        return {"started": False, "action": "none", "why": "no monitoring consent for this emergency"}
    devices = list(MedicalDevice.objects.filter(patient=em.patient, is_simulated=False).values_list("name", flat=True)[:3])
    if not devices:
        return {"started": False, "action": "none", "why": "no real measuring device registered for this patient"}
    return {"started": False, "action": "asked_patient_to_measure", "devices": devices,
            "why": "only an operator can start a test; the patient was asked to measure with their own device"}


def _guidance(em):
    from medical_rag import retriever
    a = retriever.answer(GUIDANCE_QUESTION.get(em.emergency_type, GUIDANCE_QUESTION["other"]), use_llm=False,
                         hotline=getattr(settings, "EMERGENCY_HOTLINE", "912"))
    if not a.get("ok") or a.get("mode") == "none":
        return "", []
    text = a["answer"].split("\n\n")[0][:600]  # one short section: patients should not read an essay now
    return text, [{"title": s["title"], "source": s["source"], "url": s["url"], "reviewed": s["reviewed"]}
                  for s in a["sources"][:2]]


def escalate(em, reason):
    """Runs at most once per request (conditional update on escalated_at)."""
    from notifications.services import emergency_summary, notify_hospital, notify_responders, notify_user
    from .services import log_status, nudge_operators
    if not EmergencyRequest.objects.filter(pk=em.pk, escalated_at__isnull=True).update(
            escalated_at=timezone.now(), escalation_reason=reason):
        return None  # already escalated
    # Work on a fresh copy: reading related objects (e.g. monitoring consent) on the caller's instance would
    # cache "no consent" there, and consent given a moment later would then be ignored by the caller.
    em = EmergencyRequest.objects.select_related("patient", "assigned_hospital", "assigned_ambulance").get(pk=em.pk)
    hotline = getattr(settings, "EMERGENCY_HOTLINE", "912")
    try:
        readings, vitals_note = _vitals(em)
        plan = _test_plan(em)
        guidance, sources = _guidance(em)
    except Exception:  # escalation must still notify humans even if a helper fails
        log.exception("AI escalation helpers failed for %s", em.reference)
        readings, vitals_note, plan, guidance, sources = [], "Vital signs could not be read.", {"started": False}, "", []
    explanation = _explain(readings)

    severe = reason == "severe_symptoms"
    lines = [f"Your emergency request {em.reference} is open and the emergency team has been alerted"
             + (" urgently." if severe else ". An operator will take it as soon as possible.")]
    if severe:
        lines.append(f"Your symptoms may be serious. If it gets worse or the person stops breathing, call {hotline} now.")
    if guidance:
        lines.append("While you wait: " + guidance)
    if explanation:
        lines.append(explanation)
    if plan.get("action") == "asked_patient_to_measure":
        lines.append("If you can, take a measurement now with your " + ", ".join(plan["devices"])
                     + " and share it from the app (Share vital signs).")
    lines.append("This is automatic information, not a diagnosis or treatment. A health professional will take over.")
    patient_msg = "\n\n".join(lines)
    notify_user(em.patient, f"Emergency {em.reference}: help is being arranged", patient_msg, em)

    why = ("SEVERE SYMPTOMS: " + ", ".join(severe_reasons(em))) if severe else \
        f"no operator accepted it after {em.alert_count} alerts"
    staff_msg = (f"AI ESCALATION ({why}). {emergency_summary(em)}"
                 + (f" Vitals: {readings[0]['label']} {readings[0]['summary']}." if readings else f" {vitals_note}")
                 + " The patient has been sent first-aid guidance. A human must accept responsibility.")
    notify_responders(f"ESCALATED {em.reference} - accept now", staff_msg, em)
    if em.assigned_hospital:
        notify_hospital(em.assigned_hospital, f"ESCALATED {em.reference}", staff_msg, em)

    em.escalation = {"reason": reason, "severe_reasons": severe_reasons(em), "alert_count": em.alert_count,
                     "vitals": readings, "vitals_note": vitals_note, "test": plan, "guidance_sources": sources,
                     "patient_message": patient_msg, "at": em.escalated_at.isoformat()}
    em.save(update_fields=["escalation"])
    log_status(em, f"AI escalation ({reason}): patient guided, operators{' and hospital' if em.assigned_hospital else ''} "
                   "notified; waiting for a human to accept")
    nudge_operators(em)
    return em
