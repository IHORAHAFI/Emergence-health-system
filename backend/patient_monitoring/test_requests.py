"""Patient-initiated medical tests: patient requests -> operator accepts -> IoT devices measure -> results to patient.

Business rules enforced here (not only in the pages):
* the patient is always the authenticated user who made the request (never chosen by the operator);
* one open request per patient for the same set of tests (and no overlap with an open request);
* readings are stored ONLY while the request is TESTING, and always get the request's own patient;
* completing / cancelling a request stops its simulation immediately;
* every status change notifies the other side (in-app Notification + WebSocket nudge).
"""
import logging
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from notifications.models import Notification
from . import clinical, services
from .models import MedicalTestRequest, VitalSignReading

log = logging.getLogger(__name__)
R = MedicalTestRequest
OPERATOR_ROLES = ("operator", "admin")


class TestRequestError(Exception):
    pass


# ------------------------------------------------------------------ permissions
def is_operator(user):
    return user.is_authenticated and (user.is_superuser or user.role in OPERATOR_ROLES)


def can_view(user, req):
    return is_operator(user) or (user.is_authenticated and req.patient_id == user.id)


# ------------------------------------------------------------------ live nudges
def push(group, kind, req=None, **extra):
    """Tell open pages to refresh. Pages also poll, so a failure here never loses information."""
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer
        layer = get_channel_layer()
        if layer is not None:
            async_to_sync(layer.group_send)(group, {"type": "notify.event", "kind": kind,
                                                    "reference": req.reference if req else None, **extra})
    except Exception:
        log.exception("WebSocket push failed (pages will pick it up by polling)")


def _note(recipient, req, title, message):
    Notification.objects.create(recipient=recipient, test_request=req, emergency=req.emergency, title=title,
                                message=message, channel="in_app", delivered=True, delivery_status="sent",
                                provider="in_app")


def _tell_patient(req, title, message):
    _note(req.patient, req, title, message)
    push(f"patient_{req.patient_id}", "test_update", req, status=req.status)


def _tell_operators(req, title, message):
    User = get_user_model()
    from django.db.models import Q
    for op in User.objects.filter(Q(role__in=OPERATOR_ROLES) | Q(is_superuser=True), is_active=True):
        _note(op, req, title, message)
    push("operators", "test_request", req, status=req.status)


# ------------------------------------------------------------------ patient actions
def create_request(patient, tests, note=""):
    if patient.role != "patient" or not patient.is_active:
        raise TestRequestError("Only patients can request medical tests.")
    tests = sorted(set(tests))
    if not tests or any(t not in R.TESTS for t in tests):
        raise TestRequestError("Choose at least one valid test.")
    open_reqs = R.objects.filter(patient=patient, status__in=R.OPEN)
    for other in open_reqs:
        overlap = set(other.tests) & set(tests)
        if overlap:
            names = ", ".join(R.TESTS[t][0] for t in sorted(overlap))
            raise TestRequestError(f"You already have an open request ({other.reference}) for {names}. "
                                   f"Wait for its results or cancel it first.")
    em = patient.emergencies.exclude(status__in=["completed", "cancelled"]).first()
    try:
        with transaction.atomic():
            req = R.objects.create(patient=patient, tests=tests, note=note[:255], emergency=em)
    except IntegrityError:  # double click / two tabs at the same moment
        raise TestRequestError("This test was just requested - see your open requests below.")
    name = patient.get_full_name() or patient.username
    _tell_operators(req, f"NEW TEST REQUEST {req.reference}",
                    f"{name} ({req.patient_code}) requests: {req.test_names}."
                    + (f" Active emergency {em.reference}." if em else ""))
    services.audit(patient, em, "test_requested", req.reference)
    from emergency import alert_workflow
    alert_workflow.start(req)  # alert 1 on every operator dashboard
    return req


def cancel(req, user):
    if not req.is_open:
        raise TestRequestError("Only open requests can be cancelled.")
    if user.role == "patient":
        if req.patient_id != user.id:
            raise TestRequestError("Not your request.")
        if req.status == R.Status.TESTING:
            raise TestRequestError("The test is already running - ask the operator to stop it.")
    elif not is_operator(user):
        raise TestRequestError("Not allowed.")
    _stop_session(req, user)
    req.status, req.cancelled_at, req.cancelled_by = R.Status.CANCELLED, timezone.now(), user
    req.save()
    if user.id == req.patient_id:
        _tell_operators(req, f"Test request {req.reference} cancelled", "The patient cancelled the request.")
    else:
        _tell_patient(req, "Test request cancelled", f"Your request {req.reference} ({req.test_names}) was cancelled.")
        push("operators", "test_request", req, status=req.status)
    return req


# ------------------------------------------------------------------ operator actions
def _require_operator(user):
    if not is_operator(user):
        raise TestRequestError("Only emergency operators and administrators can manage test requests.")


def accept(req, operator):
    _require_operator(operator)
    updated = R.objects.filter(pk=req.pk, status=R.Status.PENDING).update(
        status=R.Status.ACCEPTED, accepted_at=timezone.now(), assigned_operator=operator)
    if not updated:
        raise TestRequestError("Only pending requests can be accepted (someone may have accepted it already).")
    req.refresh_from_db()
    _tell_patient(req, "Test request accepted",
                  f"An operator received your request {req.reference} ({req.test_names}). Testing will start soon.")
    push("operators", "test_request", req, status=req.status)
    from emergency import alert_workflow
    alert_workflow.cancelled(req, operator)  # audit: remaining alerts + AI takeover cancelled
    return req


def start(req, operator, scenario="normal", speed=1.0):
    """Creates (or re-uses) the IoT session for THIS request's patient, with only the requested devices."""
    from . import simulation
    _require_operator(operator)
    if req.status not in (R.Status.ACCEPTED, R.Status.TESTING):
        raise TestRequestError("Accept the request before starting the test.")
    if scenario not in simulation.SCENARIOS or scenario == "custom":
        raise TestRequestError("Unknown scenario.")
    session = getattr(req, "session", None)
    if session is None:
        session = simulation.create_test_session(req, operator, scenario, speed)
    if session.status == "ready":
        simulation.control(session, "start", operator)
    req.status = R.Status.TESTING
    req.started_at = req.started_at or timezone.now()
    req.assigned_operator = req.assigned_operator or operator
    req.save()
    _tell_patient(req, "Your test has started",
                  f"Measurement for {req.reference} ({req.test_names}) is in progress. Results appear on your "
                  f"results page automatically.")
    push("operators", "test_request", req, status=req.status)
    return session


def complete(req, operator):
    _require_operator(operator)
    if req.status not in (R.Status.TESTING, R.Status.ACCEPTED):
        raise TestRequestError("Only accepted or running tests can be completed.")
    if not req.readings.exists():
        raise TestRequestError("No measurement has been recorded yet - run the test first.")
    _stop_session(req, operator)
    req.status, req.completed_at = R.Status.COMPLETED, timezone.now()
    req.save()
    latest = summary(req)
    text = "; ".join(f"{v['label']} {v['display']}" for v in latest.values())
    _tell_patient(req, "Your test results are ready", f"{req.reference} ({req.test_names}): {text}."
                  + (" These are SIMULATED demonstration values." if req.readings.filter(source="simulated").exists()
                     else ""))
    push("operators", "test_request", req, status=req.status)
    return req


def _stop_session(req, user):
    from . import simulation
    session = getattr(req, "session", None)
    if session and session.status != "stopped":
        simulation.control(session, "stop", user)


# ------------------------------------------------------------------ measurements
def record_test_reading(req, values, source, device_info, entered_by=None):
    """Stores one device transmission for a test request. Refuses if the request is not TESTING."""
    req.refresh_from_db(fields=["status"])
    if req.status != R.Status.TESTING:
        raise services.ReadingRejected("This test is not running - no measurement can be stored.")
    allowed = set(req.fields)
    data = {k: v for k, v in values.items() if k in allowed and v not in (None, "")}
    if not data:
        raise services.ReadingRejected("The device sent no value for the requested tests.")
    if "temperature_c" in data:
        from decimal import Decimal
        data["temperature_c"] = Decimal(str(round(float(data["temperature_c"]), 1)))
    r = VitalSignReading(
        emergency=None, test_request=req, patient=req.patient,  # always the request's own patient
        source=source, entered_by=entered_by, measured_at=timezone.now(),
        device=services._device(req.patient, device_info, source == VitalSignReading.Source.SIMULATED), **data)
    try:
        r.full_clean(exclude=["emergency", "test_request", "patient", "entered_by", "device"])
    except ValidationError as e:
        raise services.ReadingRejected("Implausible value: " + "; ".join(
            f"{k}: {' '.join(v)}" for k, v in e.message_dict.items()))
    result = clinical.assess({k: getattr(r, k) for k in clinical.PARAMETERS + ("diastolic_mmhg",)})
    r.news2_score, r.news2_partial, r.risk, r.alerts = result["score"], result["partial"], result["risk"], result["alerts"]
    r.save()
    hits = []
    try:
        hits = services.check_thresholds(r)
    except Exception:
        log.exception("threshold check failed (reading saved)")
    push(f"patient_{req.patient_id}", "test_reading", req)
    push("operators", "test_reading", req, critical=bool(hits))
    return r


UNITS = {"temperature_c": "°C", "heart_rate_bpm": "beats/min", "spo2_pct": "%", "systolic_mmhg": "mmHg",
         "diastolic_mmhg": "mmHg", "respiratory_rate": "breaths/min"}
# Plain-language names shown to patients (the medical abbreviation is kept in brackets where useful).
LABELS = {"temperature_c": "Body temperature", "heart_rate_bpm": "Heart rate", "spo2_pct": "Oxygen level (SpO₂)",
          "blood_pressure": "Blood pressure", "respiratory_rate": "Breathing rate"}
EXPLAIN = {
    "temperature_c": "How warm your body is. Usually about 36.1 to 37.5 °C.",
    "heart_rate_bpm": "Heart beats in one minute. Usually 60 to 100 when resting.",
    "spo2_pct": "How much oxygen your blood carries. Usually 95 to 100%.",
    "blood_pressure": "How hard your blood pushes on your blood vessels. For example 120/80.",
    "respiratory_rate": "Breaths in one minute. Usually 12 to 20 when resting.",
}
STATUS_TEXT = {
    "pending": "Your request has been received. Please wait for medical staff.",
    "accepted": "Medical staff have your request. The test will start soon.",
    "testing": "Your medical test is now in progress. Results appear automatically.",
    "completed": "Your test results are available.",
    "cancelled": "This request was cancelled.",
}


def summary(req):
    """Latest value of every requested measurement (blood pressure combined)."""
    out = {}
    readings = list(req.readings.select_related("device").order_by("-measured_at"))
    for f in req.fields:
        r = next((x for x in readings if getattr(x, f) is not None), None)
        if r is None:
            continue
        key = "blood_pressure" if f in ("systolic_mmhg", "diastolic_mmhg") else f
        if key == "blood_pressure":
            if key in out:
                continue
            display = f"{r.systolic_mmhg}/{r.diastolic_mmhg} mmHg"
            value = f"{r.systolic_mmhg}/{r.diastolic_mmhg}"
        else:
            v = getattr(r, f)
            value = float(v) if f == "temperature_c" else v
            display = f"{value} {UNITS[f]}"
        out[key] = {"label": LABELS[key], "explain": EXPLAIN[key], "value": value, "display": display, "unit": "mmHg" if key == "blood_pressure"
                    else UNITS[f], "at": r.measured_at.isoformat(), "device": r.device.name if r.device else "manual",
                    "device_id": r.device.device_identifier if r.device else "", "source": r.source,
                    "simulated": r.is_simulated, "flags": services.threshold_flags(r)}
    return out


def as_dict(req, with_readings=True, limit=200):
    d = {"reference": req.reference, "status": req.status, "status_display": req.get_status_display(),
         "status_text": STATUS_TEXT[req.status],
         "tests": req.tests, "test_names": req.test_names, "fields": req.fields,
         "requested_at": req.requested_at.isoformat(),
         "accepted_at": req.accepted_at.isoformat() if req.accepted_at else None,
         "started_at": req.started_at.isoformat() if req.started_at else None,
         "completed_at": req.completed_at.isoformat() if req.completed_at else None,
         "summary": summary(req), "units": UNITS}
    session = getattr(req, "session", None)
    d["simulation"] = {"status": session.status, "step": session.step} if session else None
    if with_readings:
        d["readings"] = [{"at": r.measured_at.isoformat(), "device": r.device.name if r.device else "manual",
                          "device_id": r.device.device_identifier if r.device else "", "source": r.source,
                          "simulated": r.is_simulated,
                          **{f: (float(getattr(r, f)) if f == "temperature_c" and getattr(r, f) is not None
                                 else getattr(r, f)) for f in UNITS}}
                         for r in req.readings.select_related("device").order_by("-measured_at")[:limit]]
    return d


STATUS_LABEL = {  # the four labels operators see on every alert
    "pending": "PENDING", "accepted": "ACCEPTED", "assigned": "ACCEPTED", "testing": "IN PROGRESS",
    "en_route": "IN PROGRESS", "on_scene": "IN PROGRESS", "transporting": "IN PROGRESS", "arrived": "IN PROGRESS",
    "completed": "COMPLETED", "cancelled": "CANCELLED"}


def unhandled_alerts():
    """Every patient service request no authorised human has taken yet, oldest first, with its own alert schedule.
    Emergencies (open, not accepted - automatic dispatch is not handling), test requests (no operator assigned)
    and service requests (contact / symptom / caregiver reports). See emergency/alert_workflow.py."""
    from django.urls import reverse
    from emergency import alert_workflow as wf, escalation
    from .models import MonitoringEvent
    out = []
    for e in wf.BY_KEY["emergency"].unhandled().select_related("patient")[:50]:
        out.append({"key": f"emergency-{e.pk}", "kind": "emergency", "reference": e.reference,
                    "patient": e.patient_name or e.patient.get_full_name() or e.patient.username,
                    "patient_code": f"P-{e.patient_id:05d}", "type": f"Emergency: {e.get_emergency_type_display()}",
                    "submitted_at": e.created_at.isoformat(), "priority": e.priority,
                    "status": e.status, "status_label": "PENDING", "status_detail": e.get_status_display(),
                    "url": reverse("emergency_detail", args=[e.pk]),
                    "escalated": bool(e.escalated_at), "escalation_reason": e.escalation_reason,
                    "severe": bool(escalation.severe_reasons(e)), **wf.schedule(e)})
    for r in wf.BY_KEY["test"].unhandled().select_related("patient")[:50]:
        out.append({"key": f"test-{r.reference}", "kind": "test", "reference": r.reference,
                    "patient": r.patient.get_full_name() or r.patient.username, "patient_code": r.patient_code,
                    "type": f"Medical test: {r.test_names}", "submitted_at": r.requested_at.isoformat(),
                    "priority": "high" if r.emergency_id else "medium",
                    "status": r.status, "status_label": "PENDING", "status_detail": r.get_status_display(),
                    "url": reverse("test_request_detail", args=[r.reference]),
                    "escalated": bool(r.ai_takeover_at), "escalation_reason": (r.ai_response or {}).get("action", ""),
                    "severe": False, **wf.schedule(r)})
    for ev in wf.BY_KEY["service"].unhandled().select_related("patient")[:50]:
        out.append({"key": f"service-{ev.pk}", "kind": "service", "reference": f"REQ-{ev.pk}",
                    "patient": ev.patient.get_full_name() or ev.patient.username,
                    "patient_code": f"P-{ev.patient_id:05d}", "type": ev.get_kind_display(),
                    "detail": ev.reason, "submitted_at": ev.created_at.isoformat(),
                    "priority": "high" if ev.severity in ("urgent", "severe") else "medium",
                    "status": ev.status, "status_label": "PENDING", "status_detail": ev.get_status_display(),
                    "url": reverse("service_request_detail", args=[ev.pk]),
                    "escalated": bool(ev.ai_takeover_at), "escalation_reason": "", "severe": ev.severity == "severe",
                    **wf.schedule(ev)})
    return sorted(out, key=lambda a: a["submitted_at"])


def accept_alert(key, operator):
    """An authorised operator takes responsibility for one request: its alerts and AI takeover stop; the others
    keep ringing. Viewing a request never counts as accepting it."""
    from emergency import services as em_services
    from emergency.models import EmergencyRequest
    from .models import MonitoringEvent
    _require_operator(operator)
    kind, _, ident = key.partition("-")
    if kind == "emergency" and ident.isdigit():
        em = EmergencyRequest.objects.filter(pk=int(ident)).first()
        if em is None:
            raise TestRequestError("Request not found.")
        try:
            em_services.accept_emergency(em, operator)
        except ValueError as e:
            raise TestRequestError(str(e))
        return
    if kind == "test":
        req = R.objects.filter(reference=ident).first()
        if req is None:
            raise TestRequestError("Request not found.")
        if req.status == R.Status.PENDING:
            accept(req, operator)
        elif req.status in R.OPEN and R.objects.filter(pk=req.pk, assigned_operator__isnull=True).update(
                assigned_operator=operator):
            # e.g. the AI started it: a human now takes over responsibility
            req.refresh_from_db()
            from emergency import alert_workflow
            alert_workflow.cancelled(req, operator)
            _tell_patient(req, "A staff member took your test",
                          f"{operator.get_full_name() or operator.username} is now responsible for {req.reference}.")
            push("operators", "test_request", req, status=req.status)
        return
    if kind == "service" and ident.isdigit():
        ev = MonitoringEvent.objects.filter(pk=int(ident)).first()
        if ev is None or ev.status not in MonitoringEvent.OPEN:
            raise TestRequestError("Request not found or already closed.")
        if MonitoringEvent.objects.filter(pk=ev.pk, accepted_at__isnull=True).update(
                accepted_at=timezone.now(), accepted_by=operator):
            from . import proactive
            ev.refresh_from_db()
            name = operator.get_full_name() or operator.username
            proactive._log(ev, "operator", f"Accepted by {name}")
            ev.save(update_fields=["log"])
            Notification.objects.create(recipient=ev.patient, title="A staff member is handling your request",
                                        message=f"{name} has your request and will contact you.", channel="in_app",
                                        delivered=True, delivery_status="sent", provider="in_app")
            services.audit(operator, ev.emergency, "service_request_accepted", f"event {ev.pk}")
            from emergency import alert_workflow
            alert_workflow.cancelled(ev, operator)
            push("operators", "service_request", None, status="accepted")
        return
    raise TestRequestError("Unknown alert.")


def operator_feed():
    """What the operator alarm needs: unhandled requests + open test requests + recent critical vital-sign alerts
    (used by polling and after WS nudges)."""
    from emergency import alert_workflow
    from .models import EmergencyAlert
    try:
        alert_workflow.tick()  # advance each request's own alert schedule (idempotent, safe under concurrent polls)
        from . import proactive
        proactive.tick()   # re-prompt patients, handle no-response, start due check-ins
    except Exception:
        log.exception("alert tick failed")
    pending = list(R.objects.filter(status=R.Status.PENDING).select_related("patient", "emergency")[:50])
    since = timezone.now() - timedelta(minutes=30)
    critical = list(EmergencyAlert.objects.filter(status="open", created_at__gte=since)
                    .select_related("test_request", "emergency")[:20])
    alerts = unhandled_alerts()
    return {
        "alerts": alerts,
        "alert_count": len(alerts),
        "pending_count": len(pending),
        "open_count": R.objects.filter(status__in=R.OPEN).count(),
        "pending": [{"reference": r.reference, "patient": r.patient.get_full_name() or r.patient.username,
                     "patient_code": r.patient_code, "tests": r.test_names,
                     "requested_at": r.requested_at.isoformat(),
                     "emergency": r.emergency.reference if r.emergency else None} for r in pending],
        # critical vital-sign threshold alerts (unhandled emergencies are in "alerts" above)
        "critical": [{"id": f"alert-{a.id}", "message": a.message,
                      "reference": (a.test_request or a.emergency).reference if (a.test_request or a.emergency) else ""}
                     for a in critical],
    }
