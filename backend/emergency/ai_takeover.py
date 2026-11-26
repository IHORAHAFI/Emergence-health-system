"""AI takeover for requests nobody handled after three alerts (emergencies use emergency.escalation instead).

What the AI may do - and what it may not:
  * test request: start the EXISTING test workflow so the patient is not left waiting:
      - patient owns a REAL device of a requested kind -> the test is opened for their device and they are asked
        to measure (readings are REAL, shown to staff);
      - otherwise, if AI_TAKEOVER_ALLOW_SIMULATION (default on) -> the existing virtual IoT devices are started;
        every value is stored and shown as SIMULATED and the patient is told it is NOT a measurement of their body;
      - otherwise the patient is told the test is waiting for staff.
    It never invents a value, never completes the test (completing needs an operator) and never "accepts" it:
    the request stays in the operator queue as "AI takeover - waiting for a human".
  * service request (contact / symptom / caregiver report): answers from the reviewed medical knowledge base
    (medical_rag, extractive), lists warning signs, tells the patient a human will follow up, notifies staff.
    Status becomes AI_RESPONDED - never closed or completed.
Each takeover runs once: the caller wins a conditional UPDATE on ai_takeover_at before doing anything.
"""
import logging

from django.conf import settings
from django.utils import timezone

log = logging.getLogger(__name__)
HOTLINE = lambda: getattr(settings, "EMERGENCY_HOTLINE", "912")  # noqa: E731
DISCLAIMER = "This is an automatic message from the assistant, not a diagnosis or treatment."


def _claim(model, obj):
    """True for exactly one caller."""
    if not model.objects.filter(pk=obj.pk, ai_takeover_at__isnull=True).update(ai_takeover_at=timezone.now()):
        return False
    obj.refresh_from_db()
    return True


def _staff(title, text, em=None):
    from notifications.services import notify_hospital, notify_responders
    notify_responders(title, text, em)
    if em is not None and em.assigned_hospital_id:
        notify_hospital(em.assigned_hospital, title, text, em)


# ------------------------------------------------------------------ tests
def take_over_test(req):
    from notifications.services import notify_user
    from patient_monitoring import simulation, test_requests as tr
    from patient_monitoring.models import MedicalDevice, MedicalTestRequest as R
    if not _claim(R, req):
        return False
    if req.status not in R.OPEN or req.assigned_operator_id:
        req.ai_response = {"action": "none", "why": "request was handled meanwhile"}
        req.save(update_fields=["ai_response"])
        return True
    kinds = set(req.device_kinds)
    real = list(MedicalDevice.objects.filter(patient=req.patient, is_simulated=False, kind__in=kinds)
                .values_list("name", flat=True)[:3])
    action, patient_msg = "none", ""
    if req.status == R.Status.PENDING:
        if real:
            # open the test for the patient's own device - no simulation, nothing invented
            R.objects.filter(pk=req.pk, status=R.Status.PENDING).update(status=R.Status.TESTING, started_at=timezone.now())
            action = "opened_for_real_device"
            patient_msg = (f"No staff member could answer your test request {req.reference} yet, so the assistant "
                           f"opened it for your own device ({', '.join(real)}). Please take the measurement now from "
                           f"the app. Medical staff will review the result.")
        elif getattr(settings, "AI_TAKEOVER_ALLOW_SIMULATION", True):
            req.refresh_from_db()
            if not getattr(req, "session", None):
                session = simulation.create_test_session(req, None, "normal", 1.0)
                simulation.control(session, "start", None)
            R.objects.filter(pk=req.pk).update(status=R.Status.TESTING, started_at=timezone.now())
            action = "started_simulation"
            patient_msg = (f"No staff member could answer your test request {req.reference} yet, so the assistant "
                           "started it on the system's VIRTUAL devices. Their values are SIMULATED demonstration data, "
                           "NOT a measurement of your body. A health professional still needs to take a real "
                           "measurement and will contact you.")
    req.refresh_from_db()
    if action == "none":
        patient_msg = (f"Your test request {req.reference} ({req.test_names}) is still waiting for medical staff. "
                       "No measurement has been taken yet. The team has been alerted again.")
    patient_msg += f" If you feel very unwell, use the Emergency button or call {HOTLINE()}. {DISCLAIMER}"
    notify_user(req.patient, f"Test request {req.reference}: update from your assistant", patient_msg, req.emergency)
    who = req.patient.get_full_name() or req.patient.username
    staff_msg = (f"AI TAKEOVER - test request {req.reference} ({req.test_names}) for {who} ({req.patient_code}) was not "
                 f"handled after 3 alerts. AI action: {action.replace('_', ' ')}. "
                 + ("Values will be SIMULATED. " if action == "started_simulation" else "")
                 + "A human must accept the request and complete it.")
    _staff(f"AI TAKEOVER {req.reference} - accept now", staff_msg, req.emergency)
    req.ai_response = {"action": action, "real_devices": real, "patient_message": patient_msg, "staff_message": staff_msg,
                       "at": req.ai_takeover_at.isoformat()}
    req.save(update_fields=["ai_response"])
    tr.push(f"patient_{req.patient_id}", "test_update", req, status=req.status)
    return True


# ------------------------------------------------------------------ service requests
def take_over_service(ev):
    from medical_rag import retriever
    from notifications.services import notify_user
    from patient_monitoring import proactive
    from patient_monitoring.models import MonitoringEvent as E
    if not _claim(E, ev):
        return False
    if ev.status not in E.OPEN or ev.accepted_at:
        return True
    resp = ev.response or {}
    symptoms = resp.get("symptoms") or []
    words = ev.reason
    if symptoms:
        words += " " + " ".join(proactive.SYMPTOM_LABELS.get(s, s) for s in symptoms)
    a = retriever.answer(words, use_llm=False, hotline=HOTLINE())
    info = ""
    sources = []
    if a.get("ok") and a.get("mode") != "none":
        info = a["answer"].split("\n\n")[0][:600]
        sources = [{"title": s["title"], "source": s["source"], "url": s["url"], "reviewed": s["reviewed"]}
                   for s in a.get("sources", [])[:2]]
    asks_contact = ev.kind == "contact_request"
    lines = [("No health professional could answer your request yet. " if asks_contact else
              "No staff member could answer your report yet. ") + "The team has been alerted again and a person "
             "will contact you."]
    if info:
        lines.append("General information while you wait: " + info)
    lines.append(f"Get emergency help at once (Emergency button or call {HOTLINE()}) if you have chest pain, difficulty "
                 "breathing, fainting, severe bleeding, confusion or signs of a stroke.")
    lines.append(DISCLAIMER)
    patient_msg = "\n\n".join(lines)
    notify_user(ev.patient, "Update from your assistant", patient_msg, ev.emergency)
    who = ev.patient.get_full_name() or ev.patient.username
    staff_msg = (f"AI TAKEOVER - {ev.get_kind_display()} from {who} (P-{ev.patient_id:05d}) was not handled after 3 "
                 f"alerts: {ev.reason}. The patient received general information. A human must accept and follow up.")
    _staff(f"AI TAKEOVER - {who}", staff_msg, ev.emergency)
    proactive._log(ev, "ai", "AI takeover after 3 unanswered operator alerts: general information sent")
    ev.status = E.Status.AI_RESPONDED
    ev.ai_response = {"action": "information_sent", "patient_message": patient_msg, "sources": sources,
                      "staff_message": staff_msg, "at": ev.ai_takeover_at.isoformat()}
    ev.save()
    return True
