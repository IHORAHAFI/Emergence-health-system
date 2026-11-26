"""Proactive assistant: the system may start the conversation, and nobody depends on the patient pressing a button.

Triggers (each creates or updates ONE MonitoringEvent - duplicates are merged):
  * home vital signs from the patient's own devices (home-monitoring consent required) -> evaluate_reading()
  * scheduled check-ins (AssistantPreferences.checkin_times)                            -> tick()
  * possible fall from the phone's motion sensor, symptom reports, caregiver reports     -> report()

Flow:
  1. the AI asks the patient (in-app notification + push if configured; the app speaks it when it is open);
  2. the patient (or a permitted caregiver) answers by voice, YES/NO buttons or symptom cards -> respond();
  3. no answer: the question is repeated at most MONITOR_MAX_PROMPTS times, MONITOR_PROMPT_INTERVAL apart, then the
     event becomes "no response" (never "unconscious") and is escalated to humans;
  4. severe readings or emergency symptoms escalate IMMEDIATELY - the AI never delays human help to keep talking;
  5. escalation reuses the existing emergency workflow (siren on the operator dashboard, AI escalation, hospital
     notification) - an already-open emergency is reused, never duplicated - and stays open for a human.

Safety rules kept here: one abnormal "concern" reading is not enough (needs a second within MONITOR_CONFIRM_WINDOW);
old readings never trigger; SIMULATED readings are labelled everywhere; no diagnosis, no prescription.
"""
import logging
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import clinical, services
from .models import AssistantPreferences, MonitoringConsent, MonitoringEvent, VitalSignReading

log = logging.getLogger(__name__)
E = MonitoringEvent


def _s(name, default):
    return int(getattr(settings, name, default))


def prompt_interval():
    return timedelta(seconds=_s("MONITOR_PROMPT_INTERVAL_SECONDS", 60))


def max_prompts():
    return _s("MONITOR_MAX_PROMPTS", 3)


CONFIRM_WINDOW = timedelta(minutes=15)   # a "concern" needs two abnormal readings within this window
MERGE_WINDOW = timedelta(minutes=30)     # readings within this window update the same open event
STALE_AFTER = timedelta(minutes=30)      # older measurements are stored but never trigger an event
SEVERITY_ORDER = ["info", "concern", "urgent", "severe"]

PROMPTS = {
    "vital_signs": "Hello. This is your medical assistant. We noticed a change in your health readings. "
                   "Are you feeling okay?",
    "possible_fall": "Hello. This is your medical assistant. Your phone detected a possible fall. Are you okay?",
    "check_in_morning": "Good morning. This is your medical assistant. How are you feeling today? Are you okay?",
    "check_in_day": "Hello. This is your medical assistant. Are you feeling okay, without pain or discomfort?",
    "report": "Thank you for telling me. Are you able to stay safe while help is arranged?",
}
FOLLOW_UP = "Would you like me to notify medical assistance?"
SEVERE_SYMPTOMS = {"cannot_breathe", "chest_pain", "emergency_help", "unconscious", "severe_bleeding", "seizure",
                   "cannot_move", "stroke_signs"}
SYMPTOM_LABELS = {
    "pain": "pain", "cannot_breathe": "cannot breathe properly", "dizzy": "dizzy", "weak": "weak", "fever": "fever",
    "emergency_help": "asked for emergency help", "cannot_speak": "cannot speak", "cannot_move": "cannot move",
    "chest_pain": "chest pain", "severe_bleeding": "severe bleeding", "seizure": "seizure", "stroke_signs": "stroke signs",
    "nausea": "nausea / vomiting",
}


# ------------------------------------------------------------------ settings & consent
def prefs(patient):
    return AssistantPreferences.objects.get_or_create(patient=patient)[0]


def home_consent(patient):
    return MonitoringConsent.objects.filter(patient=patient, scope="home", revoked_at__isnull=True).first()


HOME_CONSENT_TEXT = ("I agree that vital signs from my own devices are stored and checked automatically. If a reading "
                     "looks concerning, the assistant may contact me, and if I need help or do not answer, emergency "
                     "operators, medical staff and the caregivers I authorised may be notified. I can stop at any time.")


def give_home_consent(patient):
    c = home_consent(patient)
    if c is None:
        c = MonitoringConsent.objects.create(patient=patient, scope="home", consent_text=HOME_CONSENT_TEXT)
        services.audit(patient, None, "home_monitoring_consent_given")
    return c


def revoke_home_consent(patient):
    n = MonitoringConsent.objects.filter(patient=patient, scope="home", revoked_at__isnull=True).update(
        revoked_at=timezone.now())
    if n:
        services.audit(patient, None, "home_monitoring_consent_revoked")
    return n


# ------------------------------------------------------------------ helpers
def _log(ev, who, text, **extra):
    """who: ai / patient / caregiver / device / system / operator. Kept forever as the event history."""
    ev.log = (ev.log or []) + [{"at": timezone.now().isoformat(), "by": who, "text": text, **extra}]


def caregivers(patient, permission=None):
    from accounts.models import CaregiverLink
    qs = CaregiverLink.objects.filter(patient=patient, status="active").select_related("caregiver")
    if permission:
        qs = qs.filter(**{permission: True})
    return [l.caregiver for l in qs if l.caregiver_id]


def _notify_patient(ev, title, text):
    from notifications.services import notify_user
    notify_user(ev.patient, title, text)


def _reading_text(r):
    return f"{'SIMULATED' if r.is_simulated else 'REAL'} {services.vitals_text(r)}"


# ------------------------------------------------------------------ classification (decision support, not diagnosis)
def classify(r):
    """Severity of one reading, or None. Uses NEWS2 (clinical.assess), the configured age-group limits
    (threshold_flags) and a few single-parameter red lines that always need a human at once."""
    red = ((r.spo2_pct is not None and r.spo2_pct < 88) or (r.systolic_mmhg is not None and r.systolic_mmhg < 80)
           or (r.heart_rate_bpm is not None and (r.heart_rate_bpm > 150 or r.heart_rate_bpm < 35))
           or (r.respiratory_rate is not None and (r.respiratory_rate >= 30 or r.respiratory_rate <= 8))
           or (r.temperature_c is not None and float(r.temperature_c) < 34))
    if red or r.risk == "high":
        return "severe"
    if r.risk == "medium":
        return "urgent"
    if r.threshold_flags or r.risk == "low_medium":
        return "concern"
    return None


def _abnormal(r):
    return classify(r) is not None


# ------------------------------------------------------------------ home readings
def record_home_reading(patient, data, source, device_info=None, entered_by=None):
    """Store one home measurement (consent required) and evaluate it. Returns (reading, event_or_None)."""
    if home_consent(patient) is None:
        raise services.ReadingRejected("Home monitoring is not switched on. Give consent in the app first.")
    if source == "bluetooth" and not device_info:
        raise services.ReadingRejected("Bluetooth readings must include the device identification.")
    fields = ("temperature_c", "heart_rate_bpm", "spo2_pct", "systolic_mmhg", "diastolic_mmhg", "respiratory_rate")
    values = {k: data.get(k) for k in fields if data.get(k) not in (None, "")}
    if not values:
        raise services.ReadingRejected("The reading contains no measurement.")
    sys_, dia = values.get("systolic_mmhg"), values.get("diastolic_mmhg")
    if (sys_ is None) != (dia is None):
        raise services.ReadingRejected("Blood pressure needs both systolic and diastolic values.")
    if sys_ is not None and int(sys_) <= int(dia):
        raise services.ReadingRejected("Systolic pressure must be higher than diastolic - please re-measure.")
    if "temperature_c" in values:
        from decimal import Decimal
        values["temperature_c"] = Decimal(str(round(float(values["temperature_c"]), 1)))
    measured_at = data.get("measured_at") or timezone.now()
    if measured_at > timezone.now() + timedelta(minutes=5):
        raise services.ReadingRejected("Measurement time is in the future - check the device clock.")
    r = VitalSignReading(emergency=None, test_request=None, patient=patient, source=source, entered_by=entered_by,
                         measured_at=measured_at,
                         device=services._device(patient, device_info, source == VitalSignReading.Source.SIMULATED)
                         if device_info else None, **values)
    from django.core.exceptions import ValidationError
    try:
        r.full_clean(exclude=["emergency", "test_request", "patient", "entered_by", "device"])
    except ValidationError as e:
        raise services.ReadingRejected("Implausible value - please re-measure. " + "; ".join(
            f"{k}: {' '.join(v)}" for k, v in e.message_dict.items()))
    result = clinical.assess({k: getattr(r, k) for k in clinical.PARAMETERS + ("diastolic_mmhg",)})
    r.news2_score, r.news2_partial, r.risk, r.alerts = result["score"], result["partial"], result["risk"], result["alerts"]
    r.save()
    services.audit(entered_by or patient, None, "home_reading", f"{source} risk={r.risk}")
    try:
        services.check_thresholds(r)
    except Exception:
        log.exception("threshold check failed (reading saved)")
    ev = None
    try:
        ev = evaluate_reading(r)
    except Exception:  # the measurement is never lost because of the assistant
        log.exception("proactive evaluation failed for reading %s", r.pk)
    return r, ev


def evaluate_reading(r):
    """Decides whether a home reading needs the patient's attention. Returns the event or None."""
    if r.emergency_id or r.test_request_id:
        return None  # emergencies / tests are already watched by humans
    if timezone.now() - r.measured_at > STALE_AFTER:
        return None  # an old measurement synced late must not start a conversation now
    sev = classify(r)
    if sev is None:
        return None
    since = r.measured_at - MERGE_WINDOW
    open_ev = E.objects.filter(patient=r.patient, kind="vital_signs", status__in=E.OPEN, created_at__gte=since).first()
    if open_ev:  # same ongoing situation: update, do not create a second incident
        open_ev.readings.add(r)
        _log(open_ev, "device", f"New reading: {_reading_text(r)}")
        raised = SEVERITY_ORDER.index(sev) > SEVERITY_ORDER.index(open_ev.severity)
        if raised:
            open_ev.severity = sev
        open_ev.save()
        if raised and sev == "severe":
            escalate(open_ev, "severe_reading")
        return open_ev
    if sev == "concern":
        # one borderline reading is not enough: require a second abnormal one within the confirmation window
        earlier = [x for x in VitalSignReading.objects.filter(
            patient=r.patient, emergency=None, test_request=None, measured_at__gte=r.measured_at - CONFIRM_WINDOW,
            measured_at__lt=r.measured_at) if _abnormal(x)]
        if not earlier:
            return None
    else:
        earlier = []
    ev = _create(r.patient, "vital_signs", sev, f"{SEVERITY_LABEL[sev]}: {_reading_text(r)}", PROMPTS["vital_signs"],
                 source="device", simulated=r.is_simulated, readings=earlier + [r])
    return ev


SEVERITY_LABEL = {"concern": "Readings need attention", "urgent": "Urgent readings", "severe": "Severe readings",
                  "info": "Check-in"}


def _create(patient, kind, severity, reason, prompt, source, simulated=False, readings=(), reported_by=None,
            dedupe_key=""):
    with transaction.atomic():
        ev = E.objects.create(patient=patient, kind=kind, severity=severity, reason=reason[:255], prompt=prompt[:255],
                              source=source, is_simulated=simulated, reported_by=reported_by, dedupe_key=dedupe_key)
        if readings:
            ev.readings.add(*readings)
        _log(ev, "system", f"Event created ({ev.get_severity_display()}): {reason}")
    services.audit(reported_by or patient, None, "monitoring_event", f"{kind} {severity} #{ev.pk}")
    if severity == "severe":
        _ask(ev)            # the patient still hears what is happening...
        escalate(ev, "severe")  # ...but humans are called at the same time, not after the conversation
    else:
        _ask(ev)
        if severity == "urgent":
            _notify_staff(ev, f"Monitoring alert - {patient.get_full_name() or patient.username}",
                          f"{reason}. The assistant is asking the patient now.")
    return ev


def _ask(ev, now=None):
    ev.prompt_count += 1
    ev.last_prompt_at = now or timezone.now()
    _log(ev, "ai", ev.prompt, attempt=ev.prompt_count)
    ev.save()
    _notify_patient(ev, "Your medical assistant is checking on you", ev.prompt)


def _notify_staff(ev, title, text):
    from notifications.services import notify_responders
    notify_responders(title, text, ev.emergency)
    ev.staff_notified_at = ev.staff_notified_at or timezone.now()
    _log(ev, "system", "Operators notified")
    ev.save()


def _notify_caregivers(ev, title, text):
    if not prefs(ev.patient).notify_caregivers:
        return 0
    from notifications.services import notify_user
    people = caregivers(ev.patient, "receives_notifications")
    for c in people:
        notify_user(c, title, text, ev.emergency)
    if people:
        ev.caregivers_notified_at = ev.caregivers_notified_at or timezone.now()
        _log(ev, "system", f"{len(people)} caregiver(s) notified")
        ev.save()
    return len(people)


# ------------------------------------------------------------------ reports (fall, symptoms, caregiver)
def report(patient, kind, symptoms=(), text="", by=None, source=None):
    """A possible fall, a symptom report from the patient, a caregiver report or a contact request."""
    from medical_rag import safety
    symptoms = [s for s in symptoms if s in SYMPTOM_LABELS]
    flagged = safety.assess(text)["emergency"] if text else False
    severe = flagged or bool(set(symptoms) & SEVERE_SYMPTOMS)
    who = "caregiver" if by and by != patient else "patient"
    if kind == "possible_fall":
        recent = E.objects.filter(patient=patient, kind=kind, status__in=E.OPEN,
                                  created_at__gte=timezone.now() - timedelta(minutes=5)).first()
        if recent:
            return recent  # a burst of sensor events is one fall
        return _create(patient, kind, "concern", "Possible fall detected by the phone (not confirmed)",
                       PROMPTS["possible_fall"], source="phone_sensor")
    desc = ", ".join(SYMPTOM_LABELS[s] for s in symptoms) + (f" - \"{text[:150]}\"" if text else "")
    if kind == "contact_request":
        ev = _create(patient, kind, "concern", f"Wants to talk to a health professional. {desc}".strip(),
                     "A health professional has been asked to contact you.", source=who, reported_by=by)
        _notify_staff(ev, f"Patient asks for contact - {patient.get_full_name() or patient.username}", ev.reason)
        ev.status = E.Status.HELP
        ev.save()
        from emergency import alert_workflow
        alert_workflow.start(ev)  # operator siren: a human service was requested
        return ev
    k = "caregiver_report" if who == "caregiver" else "patient_report"
    ev = _create(patient, k, "severe" if severe else "concern",
                 f"{'Caregiver' if who == 'caregiver' else 'Patient'} reports: {desc or 'feels unwell'}",
                 PROMPTS["report"], source=who, reported_by=by)
    if not severe:
        ev.status = E.Status.HELP
        ev.save()
        _notify_staff(ev, f"Symptom report - {patient.get_full_name() or patient.username}", ev.reason)
        from emergency import alert_workflow
        alert_workflow.start(ev)  # operator siren (severe reports were already escalated to an emergency)
    return ev


# ------------------------------------------------------------------ answers
def respond(ev, user, answer, symptoms=(), pain=None, text="", channel="buttons"):
    """answer: ok | not_ok | help. Returns dict(next_prompt, guidance, escalated)."""
    from medical_rag import safety
    who = "patient" if user == ev.patient else "caregiver"
    symptoms = [s for s in symptoms if s in SYMPTOM_LABELS]
    flagged = safety.assess(text)["emergency"] if text else False
    parts = [answer] + [SYMPTOM_LABELS[s] for s in symptoms] + ([f"pain {pain}/10"] if pain is not None else [])
    _log(ev, who, (text or ", ".join(parts))[:300], answer=answer, channel=channel, symptoms=symptoms, pain=pain)
    ev.responded_at = timezone.now()
    ev.response = {"answer": answer, "symptoms": symptoms, "pain": pain, "text": text[:500], "by": who,
                   "channel": channel}
    help_needed = answer == "help" or flagged or bool(set(symptoms) & SEVERE_SYMPTOMS) or (pain is not None and pain >= 8)
    out = {"next_prompt": "", "guidance": "", "escalated": False}
    if help_needed:
        ev.status = E.Status.ESCALATED if ev.escalated_at else E.Status.HELP
        ev.save()
        escalate(ev, "patient_asked_for_help" if who == "patient" else "caregiver_asked_for_help")
        out.update(escalated=True, next_prompt="Help is being arranged now. Stay where you are if it is safe. "
                                               "A person from the emergency team will contact you.")
    elif answer == "not_ok":
        ev.status = E.Status.AWAITING
        ev.prompt = FOLLOW_UP
        ev.save()
        _ask(ev)
        out["next_prompt"] = FOLLOW_UP
    else:  # ok
        if ev.status != E.Status.ESCALATED:
            ev.status, ev.closed_at = E.Status.OK, timezone.now()
        ev.save()
        out["next_prompt"] = ("Thank you. I am glad you are okay. If anything changes, tell me or press the help button."
                              + (" Your readings will still be shared with the medical team." if ev.severity in
                                 ("urgent", "severe") else ""))
    if symptoms or text:
        out["guidance"] = _guidance(symptoms, text)
    return out


def _guidance(symptoms, text):
    from medical_rag import retriever
    q = text or " ".join(SYMPTOM_LABELS[s] for s in symptoms)
    a = retriever.answer(q, use_llm=False, hotline=getattr(settings, "EMERGENCY_HOTLINE", "912"))
    return a["answer"].split("\n\n")[-1][:500] if a.get("ok") and a.get("mode") != "none" else ""


# ------------------------------------------------------------------ escalation to humans
def escalate(ev, reason):
    """Creates (or reuses) an emergency through the existing workflow. Runs once per event."""
    from emergency import services as em_services
    from emergency.models import EmergencyRequest
    if not E.objects.filter(pk=ev.pk, escalated_at__isnull=True).update(escalated_at=timezone.now()):
        return ev.emergency
    ev.refresh_from_db()
    patient = ev.patient
    ev.summary = summary(ev, reason)
    active = EmergencyRequest.objects.filter(patient=patient).exclude(status__in=["completed", "cancelled"]).first()
    if active:  # never a second incident for the same patient
        em = active
        em_services.log_status(em, f"Monitoring event #{ev.pk} ({reason}): {ev.summary[:180]}")
        em_services.nudge_operators(em)
    else:
        p = prefs(patient)
        located = (p.share_location_on_escalation and p.last_latitude is not None and p.last_location_at
                   and timezone.now() - p.last_location_at < timedelta(hours=12))
        lat, lng = (p.last_latitude, p.last_longitude) if located else getattr(settings, "DEFAULT_MAP_CENTER", (-1.9441, 30.0619))
        syms = set((ev.response or {}).get("symptoms") or [])
        data = {
            "emergency_type": "respiratory" if "cannot_breathe" in syms or _low_oxygen(ev) else
                              "cardiac" if "chest_pain" in syms else "other",
            "description": f"AUTOMATIC ({ev.get_kind_display()}, {reason}). {ev.summary}"[:2000],
            "latitude": lat, "longitude": lng,
            "location_source": "gps" if located else "manual",
            "location_consent": bool(located),
            "location_description": "" if located else "LOCATION NOT SHARED - call the patient / caregiver to confirm",
            "breathing_difficulty": "cannot_breathe" in syms or _low_oxygen(ev),
            "chest_pain": "chest_pain" in syms,
            "severe_bleeding": "severe_bleeding" in syms,
            "seizure": "seizure" in syms,
            "patient_condition": "unknown",  # silence is never recorded as "unresponsive"
            "is_simulated": ev.is_simulated,
            "symptoms": ", ".join(SYMPTOM_LABELS[s] for s in syms),
        }
        try:
            em = em_services.create_emergency(patient, data, allow_duplicate=True, auto_dispatch=bool(located))
        except Exception:
            log.exception("automatic emergency creation failed for event %s", ev.pk)
            em = None
    ev.emergency = em
    ev.status = E.Status.ESCALATED
    _log(ev, "system", f"Escalated to humans ({reason})" + (f": emergency {em.reference}" if em else ": EMERGENCY NOT CREATED"))
    ev.save()
    who = patient.get_full_name() or patient.username
    _notify_staff(ev, f"AUTOMATIC ALERT - {who}" + (f" ({em.reference})" if em else ""), ev.summary)
    if em and em.assigned_hospital:
        from notifications.services import notify_hospital
        notify_hospital(em.assigned_hospital, f"AUTOMATIC ALERT {em.reference}", ev.summary, em)
    _notify_caregivers(ev, f"Alert for {who}", f"{ev.get_kind_display()}: {ev.reason}. The emergency team has been "
                                               "notified.")
    _notify_patient(ev, "Help is being arranged",
                    "The emergency team has been notified and a person will contact you. If you can, stay where you "
                    f"are. In danger, call {getattr(settings, 'EMERGENCY_HOTLINE', '912')}. This is an automatic "
                    "message from the assistant, not a diagnosis.")
    return em


def _low_oxygen(ev):
    return any(r.spo2_pct is not None and r.spo2_pct < 92 for r in ev.readings.all())


def summary(ev, reason):
    """Plain AI summary for operators and staff: what happened, readings (REAL/SIMULATED), patient response."""
    lines = [f"{ev.get_kind_display()} ({ev.get_severity_display()}). Reason: {ev.reason}."]
    rs = list(ev.readings.order_by("-measured_at")[:3])
    if rs:
        lines.append("Readings: " + "; ".join(_reading_text(r) for r in rs) + ".")
    if ev.is_simulated:
        lines.append("NOTE: triggered by SIMULATED demonstration data.")
    resp = ev.response or {}
    if resp:
        lines.append(f"Response by {resp.get('by')}: {resp.get('answer')}"
                     + (f", symptoms: {', '.join(SYMPTOM_LABELS.get(s, s) for s in resp.get('symptoms', []))}" if resp.get("symptoms") else "")
                     + (f", pain {resp['pain']}/10" if resp.get("pain") is not None else "") + ".")
    elif ev.prompt_count:
        lines.append(f"No answer after {ev.prompt_count} prompt(s) - the patient may be unable to respond "
                     "(NOT confirmed unconscious).")
    lines.append(f"Escalation reason: {reason}. AI decision support only - a human must take responsibility.")
    return " ".join(lines)


# ------------------------------------------------------------------ background tick
def tick(now=None):
    """Re-prompt, mark no-response, escalate, start due check-ins. Idempotent; safe to call from every poll."""
    now = now or timezone.now()
    changed = 0
    for ev in E.objects.filter(status=E.Status.AWAITING, last_prompt_at__lte=now - prompt_interval()):
        # claim this step: two simultaneous ticks must not both re-prompt / escalate the same event
        if not E.objects.filter(pk=ev.pk, status=E.Status.AWAITING, prompt_count=ev.prompt_count,
                                last_prompt_at=ev.last_prompt_at).update(last_prompt_at=now):
            continue
        ev.refresh_from_db()
        changed += 1
        if ev.prompt_count < max_prompts():
            _ask(ev, now)
            continue
        ev.status = E.Status.NO_RESPONSE
        _log(ev, "system", f"No response after {ev.prompt_count} prompts (not assumed unconscious)")
        ev.save()
        _no_response(ev, now)
    changed += _due_checkins(now)
    return changed


def _no_response(ev, now):
    if ev.kind != "check_in":
        escalate(ev, "no_response")
        return
    # a missed routine check-in alone is not an emergency - unless recent readings were concerning
    recent = [r for r in VitalSignReading.objects.filter(patient=ev.patient, measured_at__gte=now - timedelta(hours=2))
              if _abnormal(r)]
    if recent:
        ev.readings.add(*recent)
        escalate(ev, "no_response_with_concerning_readings")
        return
    n = _notify_caregivers(ev, f"{ev.patient.get_full_name() or ev.patient.username} did not answer a check-in",
                           "The assistant could not reach them. Please check on them if you can.")
    ev.status, ev.closed_at = E.Status.CLOSED, now
    _log(ev, "system", f"Check-in missed; {n} caregiver(s) told; no emergency created")
    ev.save()


def _due_checkins(now):
    created = 0
    local = timezone.localtime(now)
    for p in AssistantPreferences.objects.filter(checkins_enabled=True).select_related("patient"):
        for t in p.checkin_times or []:
            try:
                hh, mm = (int(x) for x in str(t).split(":"))
            except ValueError:
                continue
            slot = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if not (slot <= local < slot + timedelta(hours=2)):
                continue  # only around the configured time - no catch-up flood after downtime
            key = f"checkin-{slot:%Y%m%d-%H%M}"
            if E.objects.filter(patient=p.patient, dedupe_key=key).exists():
                continue
            if E.objects.filter(patient=p.patient, status__in=E.OPEN).exists():
                continue  # already talking to the patient about something else
            _create(p.patient, "check_in", "info", f"Scheduled check-in {t}",
                    PROMPTS["check_in_morning" if hh < 12 else "check_in_day"], source="ai_schedule", dedupe_key=key)
            created += 1
    return created


def as_dict(ev, for_staff=False):
    d = {"id": ev.pk, "kind": ev.kind, "kind_display": ev.get_kind_display(), "severity": ev.severity,
         "status": ev.status, "status_display": ev.get_status_display(), "prompt": ev.prompt,
         "prompt_count": ev.prompt_count, "reason": ev.reason, "simulated": ev.is_simulated,
         "created_at": ev.created_at.isoformat(), "emergency_id": ev.emergency_id,
         "emergency_ref": ev.emergency.reference if ev.emergency_id else None, "is_open": ev.is_open,
         "log": ev.log}
    if for_staff:
        d.update(summary=ev.summary, patient=ev.patient.get_full_name() or ev.patient.username,
                 patient_code=f"P-{ev.patient_id:05d}", response=ev.response,
                 readings=[{"text": _reading_text(r), "simulated": r.is_simulated, "at": r.measured_at.isoformat()}
                           for r in ev.readings.order_by("-measured_at")[:5]])
    return d


__all__ = ["datetime"]
