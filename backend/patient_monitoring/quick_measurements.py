"""Patient quick measurements (phone-camera heart rate, Bluetooth thermometer) -> database -> operator dashboard.

    app  --POST /api/measurements/ {client_id, ...}-->  validate -> save (idempotent on client_id)
                                                                 -> push "measurement" to the operators group
    dashboard  <-- WebSocket nudge / polling --  GET /dashboard/measurements.json   (always read from the database)

Rules (enforced here, not only in the app):
  * heart_rate comes only from the phone camera (PPG) and is ESTIMATED; temperature only from a BLE thermometer
    and is MEASURED. Any other combination is rejected.
  * a FAILED / INCONCLUSIVE attempt carries no value, so it can never be shown as a reading.
  * a retry with the same client_id returns the stored row (HTTP 200) instead of creating another one.
  * the push happens only after the row is committed; if it fails the row is kept and dashboards get it by polling.
"""
import logging
from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import PatientMeasurement as PM

log = logging.getLogger(__name__)

RULES = {
    PM.Kind.HEART_RATE: {"source": PM.Source.PHONE_CAMERA, "method": PM.Method.PPG, "valid": PM.Status.ESTIMATED,
                         "unit": "bpm", "range": (30, 220), "step": Decimal("1")},
    PM.Kind.TEMPERATURE: {"source": PM.Source.BLE_THERMOMETER, "method": PM.Method.BLE, "valid": PM.Status.MEASURED,
                          "unit": "°C", "range": (30, 43), "step": Decimal("0.1")},
}
MAX_AGE = timedelta(days=7)  # an offline phone may upload late; anything older is refused


class Rejected(Exception):
    pass


class Conflict(Exception):
    pass


def _emergency_for(patient, ref):
    """The emergency to attach: the one named by the app (must be the patient's), else their latest active one."""
    from emergency.models import EmergencyRequest
    if ref:
        em = EmergencyRequest.objects.filter(reference=ref).first()
        if not em or em.patient_id != patient.id:
            raise Rejected("This emergency does not belong to you.")
        return em
    return (EmergencyRequest.objects.filter(patient=patient).exclude(status__in=["completed", "cancelled"])
            .order_by("-created_at").first())


def validate(d):
    """d: serializer output. Returns the model field values or raises Rejected."""
    rule = RULES[d["kind"]]
    if d["source"] != rule["source"] or d["method"] != rule["method"]:
        raise Rejected(f"{d['kind']} must come from {rule['source']} using {rule['method']}.")
    status, value = d["status"], d.get("value")
    if status in PM.VALID:
        if status != rule["valid"]:
            raise Rejected(f"A {d['kind']} result must have the status {rule['valid']}.")
        if value is None:
            raise Rejected("A completed measurement needs a value.")
        lo, hi = rule["range"]
        if not lo <= float(value) <= hi:
            raise Rejected(f"{value} {rule['unit']} is outside the plausible range {lo}-{hi}; "
                           "send it as inconclusive.")
        if d["kind"] == PM.Kind.TEMPERATURE and not (d.get("device") or {}).get("device_identifier"):
            raise Rejected("A thermometer measurement must identify the thermometer.")
    else:
        if value is not None:
            raise Rejected("A failed or inconclusive measurement must not carry a value.")
        if not d.get("failure_reason"):
            raise Rejected("Say why the measurement failed or was inconclusive.")
    now = timezone.now()
    at = d.get("measured_at") or now
    if at > now + timedelta(minutes=5):
        raise Rejected("Measurement time is in the future - check the phone clock.")
    if at < now - MAX_AGE:
        raise Rejected("This measurement is too old to send.")
    valid = status in PM.VALID
    return {
        "kind": d["kind"], "status": status, "unit": rule["unit"], "source": d["source"], "method": d["method"],
        "value": Decimal(str(value)).quantize(rule["step"]) if valid else None,
        "device": d.get("device") or {}, "quality": d.get("quality") or {},
        "failure_reason": "" if valid else d["failure_reason"][:200],
        "duration_s": d.get("duration_s"), "measured_at": at,
    }


def submit(patient, d):
    """Returns (measurement, created). Idempotent on d['client_id']."""
    existing = PM.objects.select_related("patient", "emergency").filter(client_id=d["client_id"]).first()
    if existing:
        if existing.patient_id != patient.id:
            raise Conflict("This measurement id is already used.")
        return existing, False
    fields = validate(d)
    em = _emergency_for(patient, d.get("emergency"))
    try:
        with transaction.atomic():
            m = PM.objects.create(client_id=d["client_id"], patient=patient, emergency=em, **fields)
    except IntegrityError:  # two retries of the same measurement arrived at the same moment
        m = PM.objects.select_related("patient", "emergency").get(client_id=d["client_id"])
        if m.patient_id != patient.id:
            raise Conflict("This measurement id is already used.")
        return m, False
    from . import services
    services._safe(services.audit, patient, em, "quick_measurement_submitted",
                   f"{m.kind} {m.status} {m.value if m.value is not None else ''}")
    return m, True


def publish(m):
    """Tell operator dashboards a measurement was saved. True when the channel layer accepted the message.
    Never raises: the measurement is already stored and dashboards also poll."""
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer
        layer = get_channel_layer()
        if layer is None:
            return False
        async_to_sync(layer.group_send)("operators", {
            "type": "notify.event", "kind": "measurement", "id": m.id,
            "reference": m.emergency.reference if m.emergency_id else None})
    except Exception:
        log.exception("measurement push failed (stored; dashboards pick it up by polling)")
        return False
    now = timezone.now()
    PM.objects.filter(pk=m.pk, dashboard_notified_at__isnull=True).update(dashboard_notified_at=now)
    m.dashboard_notified_at = m.dashboard_notified_at or now
    return True


def visible_to(user):
    """Operators/admins see every measurement; approved hospital staff only those attached to emergencies at
    their hospital; everyone else nothing."""
    qs = PM.objects.select_related("patient", "emergency")
    if user.is_superuser or user.role in ("admin", "operator"):
        return qs
    if user.role == "hospital" and user.is_approved and user.hospital_id:
        return qs.filter(emergency__assigned_hospital_id=user.hospital_id)
    return qs.none()


def latest_by_patient(rows):
    """Per patient: the latest VALID heart rate and temperature (a failed attempt never hides a real value) and
    the latest attempt of any status. rows are as_dict() values, newest first."""
    out = {}
    for r in rows:
        e = out.setdefault(r["patient_id"], {"patient": r["patient"], "patient_id": r["patient_id"],
                                             "heart_rate": None, "temperature": None, "last_attempt": r})
        if r["valid"] and e[r["kind"]] is None:
            e[r["kind"]] = r
    return list(out.values())
