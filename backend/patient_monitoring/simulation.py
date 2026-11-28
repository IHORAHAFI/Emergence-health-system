"""Virtual patient + virtual IoT device simulation engine (DEMONSTRATION ONLY).

How it works
------------
* A SimulationSession advances in steps. One step = one simulated minute.
  At speed 1.0 a step happens every 2 real seconds (speed 5.0 -> every 0.4 s).
* Each scenario is a list of phases. A phase starts at a step and moves every vital sign
  from where it is towards a target, either gradually (ramp_steps > 0) or suddenly (0).
  Small random noise is added so values change like a real monitor, not fixed numbers.
* Every step, each switched-on, connected virtual device whose interval has elapsed
  "transmits" its own measurements. Transmission goes through the SAME function real
  Bluetooth / manual readings use (services.record_reading) with source=SIMULATED, so
  validation, NEWS2 scoring, storage, live updates and alerts are exercised for real.
* tick() is called by the control-panel page (every second) and/or by the
  `python manage.py run_iot_simulator` command. It is safe to call from both:
  a step is claimed with a conditional UPDATE, so it can never be processed twice.

These scenarios are illustrative trajectories, NOT predictions of real physiology.
"""
import logging
import random
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from . import services
from .models import SimulationEvent, SimulationSession, VirtualDevice, VirtualPatient, VitalSignReading

log = logging.getLogger(__name__)

VITALS = ("temperature_c", "heart_rate_bpm", "spo2_pct", "systolic_mmhg", "diastolic_mmhg", "respiratory_rate")
BASELINE = dict(temperature_c=36.7, heart_rate_bpm=76, spo2_pct=98, systolic_mmhg=120, diastolic_mmhg=80,
                respiratory_rate=16)
NOISE = dict(temperature_c=0.08, heart_rate_bpm=2.0, spo2_pct=0.6, systolic_mmhg=3.0, diastolic_mmhg=2.0,
             respiratory_rate=0.8)
# Hard limits keep values inside what the database accepts as plausible.
LIMITS = dict(temperature_c=(30, 43), heart_rate_bpm=(25, 220), spo2_pct=(60, 100), systolic_mmhg=(55, 250),
              diastolic_mmhg=(32, 150), respiratory_rate=(5, 55))


def _p(at, ramp, **target):
    return {"at": at, "ramp": ramp, "target": target}


SCENARIOS = {
    "normal": {"label": "Healthy person", "emergency_type": "other",
               "description": "Stable vital signs with normal small variation.",
               "phases": []},
    "fever": {"label": "Fever", "emergency_type": "other",
              "description": "Temperature rises gradually over ~20 minutes with a faster heart rate and breathing.",
              "phases": [_p(2, 20, temperature_c=39.3, heart_rate_bpm=112, respiratory_rate=22, spo2_pct=95,
                            systolic_mmhg=114, diastolic_mmhg=74)]},
    "breathing": {"label": "Breathing difficulty", "emergency_type": "respiratory",
                  "description": "Breathing rate climbs and oxygen saturation falls over ~15 minutes.",
                  "phases": [_p(2, 15, respiratory_rate=30, spo2_pct=88, heart_rate_bpm=118, systolic_mmhg=132,
                                diastolic_mmhg=86)]},
    "tachycardia": {"label": "Elevated heart rate", "emergency_type": "cardiac",
                    "description": "Heart rate rises to ~140 bpm over ~10 minutes.",
                    "phases": [_p(2, 10, heart_rate_bpm=142, respiratory_rate=21, systolic_mmhg=134,
                                  diastolic_mmhg=88)]},
    "low_spo2": {"label": "Low oxygen saturation", "emergency_type": "respiratory",
                 "description": "SpO2 drifts down to ~85% over ~12 minutes.",
                 "phases": [_p(2, 12, spo2_pct=85, respiratory_rate=24, heart_rate_bpm=104)]},
    "abnormal_bp": {"label": "Abnormal blood pressure", "emergency_type": "cardiac",
                    "description": "Blood pressure climbs to a hypertensive crisis range (~190/122).",
                    "phases": [_p(2, 15, systolic_mmhg=192, diastolic_mmhg=122, heart_rate_bpm=96)]},
    "accident": {"label": "Accident victim", "emergency_type": "trauma",
                 "description": "Sudden change at minute 3 (injury), then gradual deterioration as if bleeding.",
                 "phases": [_p(3, 0, heart_rate_bpm=118, respiratory_rate=24, systolic_mmhg=104, diastolic_mmhg=66),
                            _p(6, 18, heart_rate_bpm=138, respiratory_rate=28, systolic_mmhg=82, diastolic_mmhg=50,
                               spo2_pct=92, temperature_c=36.0)]},
    "critical": {"label": "Critical emergency", "emergency_type": "cardiac",
                 "description": "Rapid deterioration: very low blood pressure and oxygen, very fast heart and breathing.",
                 "phases": [_p(1, 6, heart_rate_bpm=152, respiratory_rate=34, systolic_mmhg=72, diastolic_mmhg=42,
                               spo2_pct=82, temperature_c=35.6)]},
    "custom": {"label": "Custom scenario", "emergency_type": "other",
               "description": "Your own target values, reached gradually or suddenly.", "phases": []},
}

# What each virtual device measures, and how often it transmits (in simulated minutes).
DEVICES = [
    ("thermometer", "Digital thermometer", "VTH", ("temperature_c",), 3),
    ("pulse_oximeter", "Pulse oximeter", "VOX", ("spo2_pct", "heart_rate_bpm"), 1),
    ("blood_pressure", "Blood pressure monitor", "VBP", ("systolic_mmhg", "diastolic_mmhg", "heart_rate_bpm"), 5),
    ("heart_rate", "Heart-rate sensor", "VHR", ("heart_rate_bpm",), 1),
    ("respiratory", "Respiratory rate sensor", "VRR", ("respiratory_rate",), 2),
]
DEVICE_FIELDS = {d[0]: d[3] for d in DEVICES}
UNITS = dict(temperature_c="°C", heart_rate_bpm="bpm", spo2_pct="%", systolic_mmhg="mmHg", diastolic_mmhg="mmHg",
             respiratory_rate="/min")
BATTERY_PER_TRANSMISSION = 0.25


def phases_for(session):
    if session.patient_id:  # home vital-signs demo: temperature + blood pressure scenarios only
        from .home_vitals import SCENARIOS as HOME
        return HOME.get(session.scenario, HOME["normal"])["phases"]
    if session.scenario == "custom":
        c = session.custom or {}
        target = {k: float(c[k]) for k in VITALS if c.get(k) not in (None, "")}
        return [_p(int(c.get("start_at", 2)), 0 if c.get("sudden") else int(c.get("ramp", 10)), **target)] if target else []
    return SCENARIOS.get(session.scenario, SCENARIOS["normal"])["phases"]


def true_values(session, step):
    """Noise-free trajectory value of every vital sign at a given step."""
    v = dict(BASELINE)
    for ph in phases_for(session):
        if step < ph["at"]:
            break
        prog = 1.0 if ph["ramp"] == 0 else min(1.0, (step - ph["at"]) / ph["ramp"])
        for k, target in ph["target"].items():
            v[k] = v[k] + (target - v[k]) * prog
    return v


def measured_values(session, step):
    """Trajectory + reproducible noise, rounded like a real device and kept plausible."""
    rng = random.Random(session.id * 100003 + step)
    out = {}
    for k, base in true_values(session, step).items():
        lo, hi = LIMITS[k]
        val = min(hi, max(lo, base + rng.gauss(0, NOISE[k])))
        out[k] = round(val, 1) if k == "temperature_c" else int(round(val))
    out["spo2_pct"] = min(out["spo2_pct"], 100)
    if out["diastolic_mmhg"] >= out["systolic_mmhg"] - 10:
        out["diastolic_mmhg"] = out["systolic_mmhg"] - 10
    return out


# ------------------------------------------------------------------ creating sessions
def event(session, message, level="info"):
    SimulationEvent.objects.create(session=session, step=session.step, level=level, message=message[:255])


@transaction.atomic
def create_virtual_patient(data, created_by):
    """Creates the virtual patient + an inactive placeholder account (so it can own a simulated emergency)."""
    import secrets
    User = get_user_model()
    first, _, last = data["full_name"].partition(" ")
    user = User.objects.create(username=f"virtual-{secrets.token_hex(4)}", role="patient", is_active=False,
                               first_name=f"[VIRTUAL] {first}"[:150], last_name=last[:150])
    user.set_unusable_password()
    user.save(update_fields=["password"])
    vp = VirtualPatient.objects.create(user=user, created_by=created_by, **data)
    return vp


def create_session(vp, scenario, started_by, emergency=None, custom=None, speed=1.0):
    """Creates a session with its five virtual devices. If no emergency is given, a new
    SIMULATED emergency is created through the normal workflow (AI triage + dispatch)."""
    from emergency import services as em_services
    if scenario not in SCENARIOS:
        raise ValueError("Unknown scenario")
    if emergency is not None and not emergency.is_simulated:
        raise ValueError("Simulations can only be attached to SIMULATED emergencies, never a real patient's.")
    if emergency is None:
        sc = SCENARIOS[scenario]
        emergency = em_services.create_emergency(vp.user, {
            "emergency_type": sc["emergency_type"], "description": f"SIMULATION: {sc['label']} - virtual patient "
                                                                   f"{vp.patient_code}",
            "patient_name": f"{vp.full_name} (VIRTUAL)", "gender": vp.gender, "age": vp.age,
            "latitude": -1.9536 + random.uniform(-0.03, 0.03), "longitude": 30.0606 + random.uniform(-0.03, 0.03),
            "location_source": "simulated", "location_consent": True, "is_simulated": True,
            "conscious": scenario != "critical", "breathing_difficulty": scenario in ("breathing", "low_spo2", "critical"),
            "severe_bleeding": scenario == "accident", "chest_pain": scenario == "tachycardia",
            "pain_level": 7 if scenario in ("accident", "critical") else 3,
        }, allow_duplicate=True)
    s = SimulationSession.objects.create(virtual_patient=vp, emergency=emergency, scenario=scenario,
                                         custom=custom or {}, speed=speed, started_by=started_by,
                                         current=dict(BASELINE))
    import secrets
    for kind, name, prefix, _fields, every in DEVICES:
        VirtualDevice.objects.create(session=s, kind=kind, name=name, device_id=f"{prefix}-{secrets.token_hex(2).upper()}",
                                     every_steps=every)
    s.current = measured_values(s, 0)
    s.save(update_fields=["current"])
    event(s, f"Session created: {SCENARIOS[scenario]['label']} for {vp} on emergency {emergency.reference}.")
    return s


def create_test_session(req, operator, scenario="normal", speed=1.0):
    """IoT session for an existing patient's test request. The patient comes from the request -
    the operator cannot choose or create one. Only the requested devices are created."""
    import secrets
    s = SimulationSession.objects.create(test_request=req, scenario=scenario, speed=speed, started_by=operator,
                                         current=dict(BASELINE))
    for kind, name, prefix, _fields, every in DEVICES:
        if kind in req.device_kinds:
            VirtualDevice.objects.create(session=s, kind=kind, name=name, every_steps=min(every, 2),
                                         device_id=f"{prefix}-{secrets.token_hex(2).upper()}")
    s.current = measured_values(s, 0)
    s.save(update_fields=["current"])
    event(s, f"Test session created for {req.patient_code} ({req.reference}): {req.test_names}.")
    return s


# ------------------------------------------------------------------ controls
def control(session, action, user=None):
    S = SimulationSession.Status
    now = timezone.now()
    if action == "start" and session.status in (S.READY, S.STOPPED):
        if session.status == S.STOPPED:
            raise ValueError("A stopped session cannot be restarted - press Reset first.")
        session.status, session.last_tick_at = S.RUNNING, now - timedelta(seconds=session.tick_seconds)
        msg = "Simulation started."
    elif action == "pause" and session.status == S.RUNNING:
        session.status, msg = S.PAUSED, "Simulation paused."
    elif action == "resume" and session.status == S.PAUSED:
        session.status, session.last_tick_at, msg = S.RUNNING, now, "Simulation resumed."
    elif action == "stop" and session.status in (S.RUNNING, S.PAUSED, S.READY):
        session.status, session.stopped_at, msg = S.STOPPED, now, "Simulation stopped."
    elif action == "reset":
        session.status, session.step, session.stopped_at, session.last_tick_at = S.READY, 0, None, None
        session.current = measured_values(session, 0)
        session.devices.update(battery_pct=100.0, connected=True, enabled=True, last_error="")
        msg = "Simulation reset to minute 0 (stored readings are kept as history)."
    else:
        raise ValueError(f"Cannot {action} a {session.get_status_display().lower()} simulation.")
    session.save()
    event(session, msg + (f" ({user.username})" if user else ""))
    return session


def set_speed(session, speed):
    speed = float(speed)
    if not 0.25 <= speed <= 10:
        raise ValueError("Speed must be between 0.25x and 10x.")
    session.speed = speed
    session.save(update_fields=["speed"])
    event(session, f"Speed set to {speed:g}x (a simulated minute every {session.tick_seconds:.1f} s).")


def device_action(device, action):
    if action == "connect":
        if device.battery_pct <= 0:
            raise ValueError("Battery is empty - recharge it first.")
        device.connected, msg = True, "connected"
    elif action == "disconnect":
        device.connected, msg = False, "disconnected (no data will be transmitted)"
    elif action == "enable":
        device.enabled, msg = True, "switched on"
    elif action == "disable":
        device.enabled, msg = False, "switched off"
    elif action == "recharge":
        device.battery_pct, device.connected, msg = 100.0, True, "battery recharged"
    else:
        raise ValueError("Unknown device action.")
    device.last_error = ""
    device.save()
    event(device.session, f"{device.name} {device.device_id} {msg}.", "warning" if action in ("disconnect", "disable") else "info")


# ------------------------------------------------------------------ running
def tick(session, max_steps=5):
    """Advance a running session by every step that is due (at most max_steps to catch up). Returns steps done."""
    done = 0
    while done < max_steps:
        session.refresh_from_db()
        if session.status != SimulationSession.Status.RUNNING:
            break
        now = timezone.now()
        if session.last_tick_at and (now - session.last_tick_at).total_seconds() < session.tick_seconds:
            break
        # Claim the next step atomically: only one caller (page or background command) wins it.
        claimed = SimulationSession.objects.filter(pk=session.pk, step=session.step, status="running").update(
            step=session.step + 1, last_tick_at=now)
        if not claimed:
            break
        session.step += 1
        _run_step(session)
        done += 1
    return done


def device_fields(session, kind):
    """Fields a device transmits: for a test request, only what the patient asked for."""
    if session.patient_id:  # never heart rate: that comes only from the patient's camera test
        from .home_vitals import FIELDS as HOME_FIELDS
        return HOME_FIELDS.get(kind, ())
    fields = DEVICE_FIELDS[kind]
    if session.test_request_id:
        wanted = set(session.test_request.fields)
        fields = tuple(f for f in fields if f in wanted)
    return fields


def _run_step(session):
    if session.test_request_id:
        req = session.test_request
        req.refresh_from_db(fields=["status"])
        if req.status != "testing":
            # Never generate measurements without an active testing session.
            SimulationSession.objects.filter(pk=session.pk).update(status="stopped", stopped_at=timezone.now())
            event(session, f"Stopped automatically: request {req.reference} is {req.status}.", "warning")
            return
    values = measured_values(session, session.step)
    SimulationSession.objects.filter(pk=session.pk).update(current=values)
    session.current = values
    for dev in session.devices.all():
        if not dev.enabled or not dev.connected or session.step % dev.every_steps:
            continue
        if dev.battery_pct <= 0:
            continue
        payload = {k: values[k] for k in device_fields(session, dev.kind)}
        if not payload:
            continue
        info = {"device_identifier": dev.device_id, "name": f"{dev.name} (virtual)", "kind": dev.kind,
                "manufacturer": "simulation"}
        try:
            if session.patient_id:
                from .home_vitals import record as record_home
                r = record_home(session.patient, dict(payload), info)
            elif session.test_request_id:
                from .test_requests import record_test_reading
                r = record_test_reading(session.test_request, dict(payload), VitalSignReading.Source.SIMULATED,
                                        info, entered_by=session.started_by)
            else:
                r = services.record_reading(session.emergency, session.started_by, dict(payload),
                                            VitalSignReading.Source.SIMULATED, info, simulator=True)
        except services.ReadingRejected as e:
            dev.last_error = str(e)[:200]
            dev.save(update_fields=["last_error"])
            event(session, f"{dev.name}: reading rejected - {e}", "error")
            continue
        dev.last_values, dev.last_sent_at, dev.last_error = payload, timezone.now(), ""
        dev.transmissions += 1
        dev.battery_pct = max(0.0, round(dev.battery_pct - BATTERY_PER_TRANSMISSION, 2))
        if dev.battery_pct <= 0:
            dev.connected = False
            event(session, f"{dev.name} {dev.device_id} battery empty - device disconnected.", "warning")
        dev.save()
        if services.clinical.needs_attention(r.risk):
            reasons = "; ".join(a["explanation"] for a in r.alerts if a["severity"] in ("red", "amber"))
            event(session, f"ALERT from {dev.name}: {services.clinical.RISK_LABEL[r.risk]} - {reasons}"[:255], "alert")


def tick_all():
    """Advance every running session (used by the background command)."""
    total = 0
    for s in SimulationSession.objects.filter(status="running").select_related("emergency", "started_by", "patient"):
        try:
            total += tick(s)
        except Exception:
            log.exception("Simulation %s tick failed", s.id)
    return total


def state(session, readings=40):
    """Everything the control panel needs, as JSON."""
    devices = []
    for d in session.devices.all():
        devices.append({"id": d.id, "kind": d.kind, "name": d.name, "device_id": d.device_id, "state": d.state,
                        "enabled": d.enabled, "connected": d.connected, "battery": round(d.battery_pct),
                        "every_steps": d.every_steps, "values": d.last_values, "fields": device_fields(session, d.kind),
                        "last_sent_at": d.last_sent_at.isoformat() if d.last_sent_at else None,
                        "transmissions": d.transmissions, "error": d.last_error})
    from .models import AlertThreshold, EmergencyAlert
    if session.test_request_id:
        req = session.test_request
        rs = list(req.readings.filter(source="simulated").select_related("device")[:readings])
        thresholds = list(AlertThreshold.objects.filter(enabled=True, age_group=services.age_group(None, req.patient)))
        alerts = EmergencyAlert.objects.filter(test_request=req)
    else:
        rs = list(session.emergency.vitals.filter(source="simulated").select_related("device")[:readings])
        thresholds = list(AlertThreshold.objects.filter(enabled=True, age_group=services.age_group(session.emergency)))
        alerts = session.emergency.vital_alerts.all()
    return {
        "id": session.id, "status": session.status, "status_display": session.get_status_display(),
        "step": session.step, "speed": session.speed, "tick_seconds": session.tick_seconds,
        "scenario": session.scenario, "scenario_label": SCENARIOS[session.scenario]["label"],
        "current": session.current, "units": UNITS, "devices": devices,
        "readings": [{"id": r.id, "at": r.measured_at.isoformat(), "device": r.device.name if r.device else "",
                      "device_id": r.device.device_identifier if r.device else "",
                      **{k: (float(getattr(r, k)) if k == "temperature_c" and getattr(r, k) is not None
                             else getattr(r, k)) for k in VITALS},
                      "risk": r.risk, "news2": r.news2_score, "partial": r.news2_partial,
                      "flags": services.threshold_flags(r, thresholds),
                      "alerts": [a for a in r.alerts if a["severity"] in ("red", "amber")]} for r in rs],
        "events": [{"at": e.at.isoformat(), "step": e.step, "level": e.level, "message": e.message}
                   for e in session.events.all()[:30]],
        "alerts": [{"id": a.id, "at": a.created_at.isoformat(), "message": a.message, "status": a.status,
                    "device": a.device.name if a.device else "manual"}
                   for a in alerts.select_related("device")[:20]],
        "test_request": session.test_request.reference if session.test_request_id else None,
        "test_status": session.test_request.status if session.test_request_id else None,
        "simulated": True,
    }
