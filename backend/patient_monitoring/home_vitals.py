"""Home vital-signs monitoring for a logged-in patient: three vital signs, two kinds of origin.

    heart rate         phone camera PPG   -> PatientMeasurement (quick_measurements.py)   CAMERA_MEASURED_ESTIMATE
    body temperature   software simulator -> VitalSignReading source=simulated              SIMULATED
    blood pressure     software simulator -> VitalSignReading source=simulated              SIMULATED

How the simulator part works
----------------------------
* An operator starts ONE home-vitals SimulationSession per patient (start() stops any earlier one first, so there
  are never two generators for the same patient). It reuses the virtual-device engine in simulation.py: scenario
  phases + small reproducible noise, so values drift coherently instead of jumping randomly.
* Only two virtual devices are created - a thermometer and a blood-pressure monitor - and they transmit ONLY
  temperature / systolic / diastolic. The simulator never produces a heart-rate value in this mode, so it can never
  be mistaken for (or overwrite) the patient's camera measurement, which lives in a different table anyway.
* record() stores the reading with emergency=None and test_request=None: a simulated value is never attached to an
  emergency, never creates staff alerts / threshold alerts, and never goes through the proactive home-monitoring
  escalation path. It is demonstration data and is labelled as such everywhere.
* Only the newest KEEP_PER_PATIENT simulated home readings are kept per patient (no unlimited database growth).
* Steps are advanced by `python manage.py run_iot_simulator` and also by the dashboards' polling (tick_running()).
  Both are safe together: simulation.tick() claims each step with a conditional UPDATE.
"""
import logging
import secrets

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from . import clinical, services
from .models import PatientMeasurement as PM, SimulationSession, VirtualDevice, VitalSignReading

log = logging.getLogger(__name__)

SIM = VitalSignReading.Source.SIMULATED
# Marks the devices of THIS simulator, so its readings are never confused with other simulated home readings
# (e.g. the proactive home-monitoring demo), which are never pruned or relabelled here.
MANUFACTURER = "home-vitals-simulator"
KEEP_PER_PATIENT = 500
MIN_INTERVAL_S, MAX_INTERVAL_S, DEFAULT_INTERVAL_S = 4, 300, 10

# Labels shown in every API / dashboard / AI text.
LABEL_CAMERA = "CAMERA_MEASURED_ESTIMATE"
LABEL_SIM = "SIMULATED"


def _p(at, ramp, **target):
    return {"at": at, "ramp": ramp, "target": target}


# One step = one transmission interval. Targets only touch temperature and blood pressure.
SCENARIOS = {
    "normal": {"label": "Normal", "description": "Stable temperature and blood pressure with small variation.",
               "phases": []},
    "fever": {"label": "Fever (abnormal)", "description": "Temperature rises gradually to about 39.2 °C.",
              "phases": [_p(2, 20, temperature_c=39.2, systolic_mmhg=114, diastolic_mmhg=74)]},
    "hypothermia": {"label": "Low temperature (abnormal)", "description": "Temperature falls to about 35.2 °C.",
                    "phases": [_p(2, 15, temperature_c=35.2)]},
    "hypertension": {"label": "High blood pressure (abnormal)", "description": "Rises to about 165/102 mmHg.",
                     "phases": [_p(2, 15, systolic_mmhg=165, diastolic_mmhg=102)]},
    "hypotension": {"label": "Low blood pressure (abnormal)", "description": "Falls to about 88/56 mmHg.",
                    "phases": [_p(2, 12, systolic_mmhg=88, diastolic_mmhg=56)]},
}
DEVICES = [  # kind, name, id prefix, fields
    ("thermometer", "Simulated thermometer", "STH", ("temperature_c",)),
    ("blood_pressure", "Simulated blood pressure monitor", "SBP", ("systolic_mmhg", "diastolic_mmhg")),
]
FIELDS = {d[0]: d[3] for d in DEVICES}


class HomeVitalsError(Exception):
    pass


# ------------------------------------------------------------------ controls (operators / admins only)
def can_control(user):
    return bool(user and user.is_authenticated and (user.is_superuser or user.role in ("operator", "admin")))


def active_session(patient):
    return (SimulationSession.objects.filter(patient=patient, status__in=["ready", "running", "paused"])
            .order_by("-created_at").first())


@transaction.atomic
def start(patient, operator, scenario="normal", interval_s=DEFAULT_INTERVAL_S):
    if not can_control(operator):
        raise HomeVitalsError("Only operators can start the vital-signs simulator.")
    if patient.role != "patient" or not patient.is_active:
        raise HomeVitalsError("Simulated readings can only be generated for an active patient account.")
    if scenario not in SCENARIOS:
        raise HomeVitalsError("Unknown scenario.")
    try:
        interval_s = int(interval_s)
    except (TypeError, ValueError):
        raise HomeVitalsError("The sampling interval must be a whole number of seconds.")
    if not MIN_INTERVAL_S <= interval_s <= MAX_INTERVAL_S:
        raise HomeVitalsError(f"The sampling interval must be {MIN_INTERVAL_S}-{MAX_INTERVAL_S} seconds.")
    # Lock the patient's row so two simultaneous Start clicks cannot create two generators.
    type(patient).objects.select_for_update().filter(pk=patient.pk).first()
    for old in SimulationSession.objects.filter(patient=patient, status__in=["ready", "running", "paused"]):
        _stop(old, operator, "replaced by a new simulation")
    from . import simulation
    now = timezone.now()
    # speed = 2 s / interval  -> one step (= one transmission) every interval_s seconds
    s = SimulationSession.objects.create(
        patient=patient, scenario=scenario, started_by=operator, status="running",
        speed=SimulationSession.BASE_TICK_SECONDS / interval_s, last_tick_at=now,
        current=dict(simulation.BASELINE))
    for kind, name, prefix, _f in DEVICES:
        VirtualDevice.objects.create(session=s, kind=kind, name=name, every_steps=1,
                                     device_id=f"{prefix}-{secrets.token_hex(2).upper()}")
    s.current = simulation.measured_values(s, 0)
    s.save(update_fields=["current"])
    simulation.event(s, f"Home vital-signs simulation started for patient #{patient.id}: "
                        f"{SCENARIOS[scenario]['label']}, every {interval_s} s ({operator.username}).")
    services._safe(services.audit, operator, None, "home_vitals_sim_started", f"patient={patient.id} {scenario}")
    return s


def _stop(session, user, why="stopped"):
    from . import simulation
    SimulationSession.objects.filter(pk=session.pk).update(status="stopped", stopped_at=timezone.now())
    simulation.event(session, f"Home vital-signs simulation {why}" + (f" ({user.username})." if user else "."))


def stop(patient, operator):
    if not can_control(operator):
        raise HomeVitalsError("Only operators can stop the vital-signs simulator.")
    n = 0
    for s in SimulationSession.objects.filter(patient=patient, status__in=["ready", "running", "paused"]):
        _stop(s, operator)
        n += 1
    if n:
        services._safe(services.audit, operator, None, "home_vitals_sim_stopped", f"patient={patient.id}")
    return n


def interval_of(session):
    return round(session.tick_seconds)


def session_info(session):
    if not session:
        return {"running": False}
    return {"running": session.status == "running", "status": session.status, "session_id": session.id,
            "scenario": session.scenario, "scenario_label": SCENARIOS.get(session.scenario, {}).get("label", ""),
            "interval_s": interval_of(session), "started_at": session.created_at.isoformat(),
            "devices": [{"device_id": d.device_id, "name": d.name, "kind": d.kind} for d in session.devices.all()]}


# ------------------------------------------------------------------ running
def tick_running(patient=None):
    """Advance due home-vitals sessions (all, or one patient's). Called by dashboard polling; never raises."""
    from . import simulation
    qs = SimulationSession.objects.filter(patient__isnull=False, status="running")
    if patient is not None:
        qs = qs.filter(patient=patient)
    n = 0
    for s in qs:
        try:
            n += simulation.tick(s)
        except Exception:
            log.exception("home vitals tick failed for session %s", s.id)
    return n


def record(patient, data, device_info):
    """Store one SIMULATED temperature and/or blood-pressure reading for a patient (no emergency attached)."""
    data = {k: v for k, v in data.items() if k in ("temperature_c", "systolic_mmhg", "diastolic_mmhg")}
    if not data:
        raise services.ReadingRejected("Nothing to record.")
    sys_, dia = data.get("systolic_mmhg"), data.get("diastolic_mmhg")
    if (sys_ is None) != (dia is None):
        raise services.ReadingRejected("Blood pressure needs both systolic and diastolic values.")
    if sys_ is not None and int(sys_) <= int(dia):
        raise services.ReadingRejected("Systolic pressure must be higher than diastolic.")
    if "temperature_c" in data:
        from decimal import Decimal
        data["temperature_c"] = Decimal(str(round(float(data["temperature_c"]), 1)))
    r = VitalSignReading(emergency=None, test_request=None, patient=patient, source=SIM, measured_at=timezone.now(),
                         device=services._device(patient, {**device_info, "manufacturer": MANUFACTURER}, True),
                         **data)
    try:
        r.full_clean(exclude=["emergency", "test_request", "patient", "entered_by", "device"])
    except ValidationError as e:
        raise services.ReadingRejected("Implausible simulated value: " + str(e.message_dict))
    result = clinical.assess({k: getattr(r, k) for k in clinical.PARAMETERS + ("diastolic_mmhg",)})
    r.news2_score, r.news2_partial, r.risk, r.alerts = result["score"], result["partial"], result["risk"], result["alerts"]
    r.save()
    _prune(patient)
    publish(patient.id, r.id)
    return r


def _home_sim_qs(patient):
    return VitalSignReading.objects.filter(patient=patient, source=SIM, emergency=None, test_request=None,
                                           device__manufacturer=MANUFACTURER)


def _prune(patient):
    old = list(_home_sim_qs(patient).order_by("-measured_at", "-id").values_list("id", flat=True)[KEEP_PER_PATIENT:])
    if old:
        VitalSignReading.objects.filter(id__in=old).delete()


def publish(patient_id, reading_id):
    """Nudge open operator dashboards and the patient's own pages. Carries no medical data; never raises."""
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer
        layer = get_channel_layer()
        if layer is None:
            return
        msg = {"type": "notify.event", "kind": "home_vitals", "id": reading_id, "patient_id": patient_id}
        async_to_sync(layer.group_send)("operators", msg)
        async_to_sync(layer.group_send)(f"patient_{patient_id}", msg)
    except Exception:
        log.exception("home vitals push failed (stored; dashboards poll)")


# ------------------------------------------------------------------ read side (patient app, dashboards, AI)
def _hr_dict(m):
    q = m.quality or {}
    return {"id": m.id, "kind": "heart_rate", "value": float(m.value) if m.value is not None and m.is_valid else None,
            "unit": "BPM", "status": m.status.upper(), "valid": m.is_valid,
            "source": LABEL_CAMERA, "method": "SMARTPHONE_CAMERA_PPG",
            "signal_quality": q.get("quality"), "failure_reason": m.failure_reason,
            "measured_at": m.measured_at.isoformat(), "measurement_id": str(m.client_id)}


def _sim_dict(r, kind):
    base = {"id": r.id, "kind": kind, "source": LABEL_SIM, "validation_status": "VALID",
            "device": r.device.device_identifier if r.device else None,
            "measured_at": r.measured_at.isoformat()}
    if kind == "temperature":
        return {**base, "value": float(r.temperature_c), "unit": "°C"}
    return {**base, "systolic": r.systolic_mmhg, "diastolic": r.diastolic_mmhg,
            "value": f"{r.systolic_mmhg}/{r.diastolic_mmhg}", "unit": "mmHg"}


def summary(patient, limit=30):
    """Everything the Vital Signs section needs for ONE patient, newest first, all read from the database."""
    hr = list(PM.objects.filter(patient=patient, kind=PM.Kind.HEART_RATE).order_by("-measured_at")[:limit])
    sim = _home_sim_qs(patient).select_related("device").order_by("-measured_at")
    temps = list(sim.filter(temperature_c__isnull=False)[:limit])
    bps = list(sim.filter(systolic_mmhg__isnull=False)[:limit])
    hr_rows = [_hr_dict(m) for m in hr]
    valid_hr = [h for h in hr_rows if h["valid"]]
    return {
        "heart_rate": {"label": "CAMERA-ESTIMATED HEART RATE", "latest": valid_hr[0] if valid_hr else None,
                       "last_attempt": hr_rows[0] if hr_rows else None, "history": valid_hr},
        "temperature": {"label": "SIMULATED BODY TEMPERATURE", "latest": _sim_dict(temps[0], "temperature") if temps
                        else None, "history": [_sim_dict(r, "temperature") for r in temps]},
        "blood_pressure": {"label": "SIMULATED BLOOD PRESSURE", "latest": _sim_dict(bps[0], "blood_pressure") if bps
                           else None, "history": [_sim_dict(r, "blood_pressure") for r in bps]},
        "simulation": session_info(active_session(patient)),
        "server_time": timezone.now().isoformat(),
    }


def overview():
    """Operator table: one row per patient that has camera heart-rate or simulated readings (latest values)."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    ids = set(PM.objects.filter(kind=PM.Kind.HEART_RATE).values_list("patient_id", flat=True).distinct())
    ids |= set(VitalSignReading.objects.filter(source=SIM, emergency=None, test_request=None,
                                               device__manufacturer=MANUFACTURER)
               .values_list("patient_id", flat=True).distinct())
    ids |= set(SimulationSession.objects.filter(patient__isnull=False, status="running")
               .values_list("patient_id", flat=True))
    rows = []
    for p in User.objects.filter(id__in=ids, role="patient"):
        s = summary(p, limit=1)
        rows.append({"patient_id": p.id, "patient": p.get_full_name() or p.username,
                     "heart_rate": s["heart_rate"]["latest"], "hr_last_attempt": s["heart_rate"]["last_attempt"],
                     "temperature": s["temperature"]["latest"], "blood_pressure": s["blood_pressure"]["latest"],
                     "simulation": s["simulation"]})
    rows.sort(key=lambda r: max([x["measured_at"] for x in (r["heart_rate"], r["temperature"], r["blood_pressure"])
                                 if x] or [""]), reverse=True)
    return rows


def patients_for_control():
    from django.contrib.auth import get_user_model
    return [{"id": u.id, "name": u.get_full_name() or u.username}
            for u in get_user_model().objects.filter(role="patient", is_active=True).order_by("first_name", "username")[:500]]


def ai_context(patient):
    """Text for the AI assistant. Every value carries its origin; simulated values are explicitly not for diagnosis;
    a failed camera test is reported as failed, never as a normal heart rate; missing values are said to be missing."""
    s = summary(patient, limit=1)
    lines = []
    hr, att = s["heart_rate"]["latest"], s["heart_rate"]["last_attempt"]
    if hr:
        q = f", signal quality {round(hr['signal_quality'] * 100)}%" if hr["signal_quality"] is not None else ""
        lines.append(f"Heart rate {int(hr['value'])} BPM - {LABEL_CAMERA} (smartphone camera PPG estimate, not "
                     f"medical-grade{q}), status {hr['status']}, at {_t(hr['measured_at'])}.")
    else:
        lines.append("Heart rate: no successful camera measurement available (do not assume a value).")
    if att and not att["valid"] and (not hr or att["measured_at"] > hr["measured_at"]):
        lines.append(f"The latest camera heart-rate test at {_t(att['measured_at'])} was {att['status']} "
                     f"({att['failure_reason']}) - it gives NO heart-rate value.")
    t, bp = s["temperature"]["latest"], s["blood_pressure"]["latest"]
    lines.append(f"Body temperature {t['value']} °C - {LABEL_SIM} demonstration value, NOT a real measurement, "
                 f"at {_t(t['measured_at'])}." if t else "Body temperature: not available.")
    lines.append(f"Blood pressure systolic {bp['systolic']} / diastolic {bp['diastolic']} mmHg - {LABEL_SIM} "
                 f"demonstration value, NOT a real measurement, at {_t(bp['measured_at'])}."
                 if bp else "Blood pressure: not available.")
    if t or bp:
        lines.append("Never diagnose, and never recommend or trigger emergency dispatch, on the basis of SIMULATED "
                     "values.")
    return "\n".join(lines)


def _t(iso):
    from django.utils.dateparse import parse_datetime
    return f"{timezone.localtime(parse_datetime(iso)):%Y-%m-%d %H:%M}"
