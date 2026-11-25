"""Recording vital signs: validation -> NEWS2 -> save -> live broadcast -> staff alerts."""
import logging
import random
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from notifications.services import notify_hospital, notify_responders, notify_user
from . import clinical
from .models import AccessAuditLog, MedicalDevice, MonitoringConsent, VitalSignReading

log = logging.getLogger(__name__)
MEASUREMENT_FIELDS = ("temperature_c", "heart_rate_bpm", "spo2_pct", "systolic_mmhg", "diastolic_mmhg",
                      "respiratory_rate", "ecg_rhythm")
ALERT_REPEAT = timedelta(minutes=5)
CONSENT_TEXT = ("I agree that vital-sign measurements I send for this emergency may be stored and shown to the "
                "emergency operators, ambulance crew and hospital staff handling it. I can stop sharing at any time.")


# ------------------------------------------------------------------ permissions
def can_view(user, em):
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.role in ("admin", "operator"):
        return True
    if user.role == "hospital":
        return bool(user.is_approved and user.hospital_id and em.assigned_hospital_id == user.hospital_id)
    if user.role == "paramedic":
        return bool(em.assigned_ambulance_id and em.assigned_ambulance.driver_id == user.id)
    return user.role == "patient" and em.patient_id == user.id


def can_review(user, em):
    """Only clinical staff (approved healthcare professionals of the receiving hospital,
    and the ambulance crew) may confirm / override decision support. Operators can view."""
    return user.role in ("hospital", "paramedic") and can_view(user, em)


def can_submit(user, em):
    if not em.is_active:
        return False
    if user.role == "patient":
        return em.patient_id == user.id
    if user.role == "paramedic":
        return bool(em.assigned_ambulance_id and em.assigned_ambulance.driver_id == user.id)
    return False


def has_consent(em):
    c = getattr(em, "monitoring_consent", None)
    return bool(c and c.active)


def give_consent(em, user):
    c, _ = MonitoringConsent.objects.update_or_create(
        emergency=em, defaults={"patient": em.patient, "revoked_at": None, "consent_text": CONSENT_TEXT})
    audit(user, em, "consent_given")
    return c


def revoke_consent(em, user):
    MonitoringConsent.objects.filter(emergency=em).update(revoked_at=timezone.now())
    audit(user, em, "consent_revoked")


def audit(user, em, action, detail="", request=None):
    ip = None
    if request is not None:
        ip = request.META.get("REMOTE_ADDR")
    AccessAuditLog.objects.create(user=user if getattr(user, "is_authenticated", False) else None,
                                  emergency=em, action=action, detail=detail[:255], ip_address=ip)


# ----------------------------------------------------------------- recording
class ReadingRejected(Exception):
    pass


def _device(patient, info, simulated):
    if not info or not info.get("device_identifier"):
        return None
    dev, _ = MedicalDevice.objects.update_or_create(
        patient=patient, device_identifier=str(info["device_identifier"])[:128],
        defaults={"kind": info.get("kind") or "simulator", "name": str(info.get("name") or "Unknown device")[:100],
                  "manufacturer": str(info.get("manufacturer") or "")[:100], "is_simulated": simulated})
    return dev


def record_reading(em, user, data, source, device_info=None, simulator=False):
    """Validate + store one reading. Raises ReadingRejected with a readable message.

    simulator=True is used only by the virtual-device engine: it may write SIMULATED readings
    to SIMULATED emergencies (never to a real patient's emergency)."""
    if simulator:
        if source != VitalSignReading.Source.SIMULATED or not em.is_simulated:
            raise ReadingRejected("The simulator may only send SIMULATED readings to simulated emergencies.")
        if not em.is_active:
            raise ReadingRejected("The simulated emergency is closed.")
    elif not can_submit(user, em):
        raise ReadingRejected("You cannot send measurements for this emergency.")
    if not simulator and user.role == "patient" and not has_consent(em):
        raise ReadingRejected("Consent is required before measurements can be shared.")
    if not any(data.get(f) not in (None, "") for f in MEASUREMENT_FIELDS) and not data.get("consciousness"):
        raise ReadingRejected("The reading contains no measurement (device disconnected or nothing entered?).")
    sys_, dia = data.get("systolic_mmhg"), data.get("diastolic_mmhg")
    if (sys_ is None) != (dia is None):
        raise ReadingRejected("Blood pressure needs both systolic and diastolic values.")
    if sys_ is not None and int(sys_) <= int(dia):
        raise ReadingRejected("Systolic pressure must be higher than diastolic - please re-measure.")
    if data.get("temperature_c") not in (None, ""):
        try:  # floats like 37.3 are not exact; store exactly one decimal place
            data = {**data, "temperature_c": Decimal(str(round(float(data["temperature_c"]), 1)))}
        except (TypeError, ValueError):
            raise ReadingRejected("Temperature must be a number.")
    measured_at = data.get("measured_at") or timezone.now()
    if measured_at > timezone.now() + timedelta(minutes=5):
        raise ReadingRejected("Measurement time is in the future - check the device clock.")

    r = VitalSignReading(
        emergency=em, patient=em.patient, source=source, entered_by=user, measured_at=measured_at,
        device=_device(em.patient, device_info, source == VitalSignReading.Source.SIMULATED),
        **{k: data.get(k) for k in ("temperature_c", "heart_rate_bpm", "spo2_pct", "systolic_mmhg", "diastolic_mmhg",
                                    "respiratory_rate", "consciousness", "ecg_rhythm", "ecg_samples",
                                    "ecg_sample_rate_hz") if data.get(k) not in (None, "")},
        on_supplemental_oxygen=bool(data.get("on_supplemental_oxygen")))
    try:
        r.full_clean(exclude=["emergency", "patient", "entered_by", "device"])
    except ValidationError as e:
        msgs = [f"{k.replace('_', ' ')}: {' '.join(v)}" for k, v in e.message_dict.items()]
        raise ReadingRejected("Implausible value - please re-measure. " + "; ".join(msgs))

    result = clinical.assess({k: getattr(r, k) for k in clinical.PARAMETERS + ("diastolic_mmhg",)})
    r.news2_score, r.news2_partial, r.risk, r.alerts = result["score"], result["partial"], result["risk"], result["alerts"]
    with transaction.atomic():
        r.save()
    audit(user, em, "reading_submitted", f"{source} risk={r.risk} score={r.news2_score}")
    # Side effects must never lose the measurement.
    r.threshold_hits = []
    try:
        r.threshold_hits = check_thresholds(r)
    except Exception:
        log.exception("Threshold check failed (measurement is saved)")
    _safe(broadcast, r)
    _safe(alert_staff, r)
    return r


# ------------------------------------------------------------------ configurable thresholds
def patient_age(user):
    profile = getattr(user, "patient_profile", None)
    if profile and profile.age is not None:
        return profile.age
    vp = getattr(user, "virtual_patient", None)
    return vp.age if vp else None


def age_group(em=None, patient=None):
    """'child' (< 16 years) or 'adult'. Uses the age given in the emergency, else the patient's profile."""
    age = em.age if em is not None else None
    if age is None:
        age = patient_age(patient if patient is not None else em.patient)
    return "child" if age is not None and age < 16 else "adult"


def threshold_flags(reading, thresholds=None):
    """{parameter: '> 38'} for every configured limit this reading crosses (read-only, no alert created)."""
    from .models import AlertThreshold
    if thresholds is None:
        thresholds = AlertThreshold.objects.filter(
            enabled=True, age_group=age_group(reading.emergency, reading.patient))
    flags = {}
    for t in thresholds:
        v = getattr(reading, t.parameter, None)
        if v is None:
            continue
        if t.low is not None and float(v) < t.low:
            flags[t.parameter] = f"< {t.low:g}"
        elif t.high is not None and float(v) > t.high:
            flags[t.parameter] = f"> {t.high:g}"
    return flags


def check_thresholds(reading):
    """Compares each measured value with the configured limits. Creates an EmergencyAlert for every
    crossing (at most one open alert per parameter per ALERT_REPEAT window, so a continuous device
    cannot flood the dashboard). Returns the crossings found (new or repeated)."""
    from .models import AlertThreshold, EmergencyAlert
    em, hits = reading.emergency, []
    units = {"temperature_c": "°C", "heart_rate_bpm": "bpm", "spo2_pct": "%", "systolic_mmhg": "mmHg",
             "diastolic_mmhg": "mmHg", "respiratory_rate": "/min"}
    labels = dict(AlertThreshold.PARAMS)
    for param, limit in threshold_flags(reading).items():
        v = float(getattr(reading, param))
        msg = f"{labels[param].split(' (')[0]} {v:g} {units[param]} ({limit})"
        hits.append(msg)
        recent = EmergencyAlert.objects.filter(emergency=em, test_request=reading.test_request, parameter=param,
                                               status="open", created_at__gte=timezone.now() - ALERT_REPEAT).exists()
        if not recent:
            EmergencyAlert.objects.create(
                emergency=em, test_request=reading.test_request, reading=reading, device=reading.device, parameter=param, value=v,
                unit=units[param], limit=limit, is_simulated=reading.is_simulated,
                message=(msg + (" [SIMULATED]" if reading.is_simulated else ""))[:255])
    return hits


def acknowledge_alert(alert, user):
    alert.status, alert.acknowledged_by, alert.acknowledged_at = "acknowledged", user, timezone.now()
    alert.save(update_fields=["status", "acknowledged_by", "acknowledged_at"])
    audit(user, alert.emergency, "alert_acknowledged", alert.message)


def _safe(fn, *a):
    try:
        fn(*a)
    except Exception:
        log.exception("%s failed (measurement is saved)", getattr(fn, "__name__", repr(fn)))


def broadcast(reading):
    from asgiref.sync import async_to_sync
    from channels.layers import get_channel_layer
    layer = get_channel_layer()
    if layer is None:
        return
    msg = {"type": "vitals.update", "emergency": reading.emergency.reference, "reading": reading.as_dict()}
    async_to_sync(layer.group_send)(f"vitals_{reading.emergency_id}", msg)
    async_to_sync(layer.group_send)("vitals_all", msg)


def vitals_text(reading):
    parts = []
    if reading.temperature_c is not None: parts.append(f"T {reading.temperature_c}°C")
    if reading.heart_rate_bpm is not None: parts.append(f"HR {reading.heart_rate_bpm}")
    if reading.spo2_pct is not None: parts.append(f"SpO2 {reading.spo2_pct}%")
    if reading.systolic_mmhg is not None: parts.append(f"BP {reading.systolic_mmhg}/{reading.diastolic_mmhg}")
    if reading.respiratory_rate is not None: parts.append(f"RR {reading.respiratory_rate}")
    if reading.ecg_rhythm: parts.append(f"ECG {reading.ecg_rhythm}")
    tag = " [SIMULATED]" if reading.is_simulated else ""
    score = f"NEWS2 {reading.news2_score}{' (partial)' if reading.news2_partial else ''}" if reading.news2_score is not None else ""
    return f"{', '.join(parts)} at {timezone.localtime(reading.measured_at):%H:%M}{tag}. {score}".strip()


def alert_staff(reading):
    em = reading.emergency
    hits = getattr(reading, "threshold_hits", [])
    attention = clinical.needs_attention(reading.risk) or bool(hits)
    reasons = "; ".join([a["explanation"] for a in reading.alerts if a["severity"] in ("red", "amber")]
                        + [f"threshold crossed: {h}" for h in hits])
    title = (f"VITALS NEED REVIEW {em.reference} - {clinical.RISK_LABEL[reading.risk]}" if attention
             else f"New vital signs {em.reference}")
    body = vitals_text(reading) + (f" Why: {reasons}." if attention and reasons else "") + \
        " Decision support only - clinical review required."
    # Continuous devices send many readings: send the same alert at most once per ALERT_REPEAT window
    # (a higher risk level has a different title, so escalation is never held back).
    from notifications.models import Notification
    if Notification.objects.filter(emergency=em, title=title,
                                   created_at__gte=timezone.now() - ALERT_REPEAT).exists():
        return
    if em.assigned_hospital:
        notify_hospital(em.assigned_hospital, title, body, em) if attention else \
            _in_app_hospital(em.assigned_hospital, title, body, em)
    if attention:
        notify_responders(title, body, em)
        if em.assigned_ambulance and em.assigned_ambulance.driver:
            notify_user(em.assigned_ambulance.driver, title, body, em)


def _in_app_hospital(hospital, title, body, em):
    """Routine readings: in-app only (no SMS spam)."""
    from notifications.models import Notification
    Notification.objects.create(hospital=hospital, emergency=em, title=title, message=body,
                                channel="in_app", delivered=True, delivery_status="sent", provider="in_app")
    for staff in hospital.staff.filter(is_active=True, is_approved=True):
        Notification.objects.create(recipient=staff, emergency=em, title=title, message=body,
                                    channel="in_app", delivered=True, delivery_status="sent", provider="in_app")


def latest_vitals_summary(em):
    r = em.vitals.first()
    return f" Latest vitals: {vitals_text(r)}" if r else " No vital signs received yet."


# ---------------------------------------------------------------- simulator
SCENARIOS = {
    "normal": dict(temperature_c=(36.4, 37.2), heart_rate_bpm=(62, 88), spo2_pct=(96, 99),
                   systolic_mmhg=(112, 132), diastolic_mmhg=(70, 85), respiratory_rate=(12, 18)),
    "fever": dict(temperature_c=(38.6, 39.8), heart_rate_bpm=(98, 118), spo2_pct=(94, 97),
                  systolic_mmhg=(105, 125), diastolic_mmhg=(65, 80), respiratory_rate=(19, 23)),
    "hypoxia": dict(temperature_c=(36.8, 37.6), heart_rate_bpm=(110, 135), spo2_pct=(84, 91),
                    systolic_mmhg=(100, 120), diastolic_mmhg=(60, 78), respiratory_rate=(24, 30)),
    "shock": dict(temperature_c=(35.2, 36.2), heart_rate_bpm=(125, 145), spo2_pct=(90, 94),
                  systolic_mmhg=(78, 92), diastolic_mmhg=(45, 58), respiratory_rate=(22, 28)),
}


def simulate_values(scenario="normal", device="all", rng=random):
    """Fake device output for demos/tests. Always stored with source=simulated."""
    s = SCENARIOS.get(scenario, SCENARIOS["normal"])
    v = {k: (round(rng.uniform(*r), 1) if k == "temperature_c" else rng.randint(*r)) for k, r in s.items()}
    wanted = {"thermometer": ["temperature_c"], "pulse_oximeter": ["spo2_pct", "heart_rate_bpm"],
              "blood_pressure": ["systolic_mmhg", "diastolic_mmhg", "heart_rate_bpm"]}.get(device)
    if wanted:
        v = {k: v[k] for k in wanted}
    return v
