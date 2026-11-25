"""
Core workflow:
  create -> AI triage (with rule fallback) -> hospital recommendation
  -> ambulance assignment (race-safe) -> notifications -> status updates -> completion.

Safety rule: the request is SAVED and responders are ALERTED even if every AI
component fails. AI only adds decision support; it never blocks the request.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from ai_recommendation.geo import eta_minutes, haversine_km
from ai_recommendation.recommender import rank_ambulances, recommend_hospitals
from ambulances.models import Ambulance, AmbulanceAssignment
from hospitals.models import Hospital
from notifications.services import (emergency_summary, notify_hospital, notify_responders, notify_user, sms)
from .models import EmergencyRequest, StatusLog

log = logging.getLogger(__name__)
S = EmergencyRequest.Status
A = Ambulance.Status

ALLOWED_TRANSITIONS = {
    "pending": {"assigned", "cancelled"},
    "assigned": {"en_route", "cancelled"},
    "en_route": {"on_scene", "cancelled"},
    "on_scene": {"transporting", "completed", "cancelled"},
    "transporting": {"arrived"},
    "arrived": {"completed"},
}
# Ambulance status that follows each emergency status.
AMBULANCE_FOR_STATUS = {"en_route": A.ON_THE_WAY, "on_scene": A.ARRIVED, "transporting": A.BUSY}


class DuplicateEmergency(Exception):
    def __init__(self, existing):
        self.existing = existing
        super().__init__(f"You already have an active emergency ({existing.reference}).")


def log_status(em, note="", by=None):
    StatusLog.objects.create(emergency=em, status=em.status, note=note[:255], by=by)


# ------------------------------------------------------------------- AI triage
def _rule_priority(data):
    from ai_recommendation.priority import _rule_label
    return _rule_label(data.get("emergency_type", "other"), int(data.get("age") or 35),
                       bool(data.get("conscious", True)), bool(data.get("breathing_difficulty")),
                       bool(data.get("severe_bleeding")), bool(data.get("chest_pain")),
                       int(data.get("pain_level") or 0), bool(data.get("pregnant")))


def triage(em):
    if em.age is None and hasattr(em.patient, "patient_profile"):
        em.age = em.patient.patient_profile.age
    data = {f: getattr(em, f) for f in ("emergency_type", "age", "conscious", "breathing_difficulty",
                                        "severe_bleeding", "chest_pain", "pain_level", "pregnant")}
    try:
        from ai_recommendation.priority import predict_priority
        em.priority, em.ai_confidence = predict_priority(data)
        em.triage_method = "ml_model"
    except Exception:  # model file missing/corrupt, sklearn not installed, ...
        log.exception("AI priority model unavailable - using rule-based fallback")
        try:
            em.priority = _rule_priority(data)
        except Exception:
            em.priority = "high"  # fail safe: treat as urgent
        em.ai_confidence = None
        em.triage_method = "rule_fallback"
    try:
        em.recommendations = recommend_hospitals(em.latitude, em.longitude, em.emergency_type, em.priority)
    except Exception:
        log.exception("Hospital recommendation failed")
        em.recommendations = []


# ------------------------------------------------------------ ambulance handling
def open_assignment(em):
    return em.assignments.filter(released_at__isnull=True).select_related("ambulance").first()


def release_assignment(em, outcome=AmbulanceAssignment.Outcome.COMPLETED):
    """Close the open assignment and make the ambulance available again."""
    asg = open_assignment(em)
    if not asg:
        return None
    asg.released_at = timezone.now()
    asg.outcome = outcome
    asg.save(update_fields=["released_at", "outcome"])
    Ambulance.objects.filter(pk=asg.ambulance_id).exclude(status=A.OFFLINE).update(status=A.AVAILABLE)
    return asg.ambulance


def _reserve_ambulance(em, by=None):
    """Try candidates best-first. The conditional UPDATE ... WHERE status='available'
    is atomic in the database, so two simultaneous requests can never both win the
    same ambulance; the partial unique constraint on AmbulanceAssignment is a second guard."""
    for amb, dist, reason in rank_ambulances(em.latitude, em.longitude, em.priority):
        if Ambulance.objects.filter(pk=amb.pk, status=A.AVAILABLE).update(status=A.ASSIGNED) != 1:
            continue  # someone else took it a moment ago
        try:
            with transaction.atomic():
                AmbulanceAssignment.objects.create(
                    ambulance=amb, emergency=em, assigned_by=by, distance_km=dist,
                    eta_minutes=eta_minutes(dist, settings.AMBULANCE_AVG_SPEED_KMH),
                    start_latitude=amb.latitude, start_longitude=amb.longitude)
        except IntegrityError:
            Ambulance.objects.filter(pk=amb.pk, status=A.ASSIGNED).update(status=A.AVAILABLE)
            continue
        amb.refresh_from_db()
        return amb, dist, reason
    return None, None, None


@transaction.atomic
def dispatch(em, hospital_id=None, by=None):
    """Choose hospital + reserve the best available ambulance, then alert everyone."""
    if hospital_id:
        hospital = Hospital.objects.get(pk=hospital_id)
    elif em.recommendations:
        hospital = Hospital.objects.filter(pk=em.recommendations[0]["hospital_id"]).first()
    else:
        hospital = None
    em.assigned_hospital = hospital

    if open_assignment(em):  # re-dispatch: free the previous ambulance first
        release_assignment(em, AmbulanceAssignment.Outcome.RELEASED)
        em.assigned_ambulance = None

    ambulance, dist, reason = _reserve_ambulance(em, by)
    if ambulance:
        em.assigned_ambulance = ambulance
        em.ambulance_distance_km = dist
        em.eta_minutes = eta_minutes(dist, settings.AMBULANCE_AVG_SPEED_KMH)
        em.status = S.ASSIGNED
        em.dispatched_at = timezone.now()
    else:
        em.status = S.PENDING
    em.save()
    log_status(em, f"Ambulance {ambulance.plate_number} assigned: {reason}" if ambulance
               else "No ambulance available - waiting for operator", by)

    summary = emergency_summary(em)
    if ambulance:
        notify_user(em.patient, "Ambulance assigned",
                    f"{em.reference}: ambulance {ambulance.plate_number} is assigned. "
                    f"Estimated arrival about {em.eta_minutes:.0f} min (estimate, not live traffic).",
                    em, sms_too=True)
        if ambulance.driver:
            notify_user(ambulance.driver, f"NEW {em.priority.upper()} emergency {em.reference}", summary,
                        em, sms_too=True)
        elif ambulance.contact_phone:
            sms(ambulance.contact_phone, f"NEW emergency {em.reference}", summary, em)
        Ambulance.objects.filter(pk=ambulance.pk, status=A.ASSIGNED).update(status=A.DISPATCHED)
        ambulance.status = A.DISPATCHED
    else:
        notify_user(em.patient, "Request received",
                    f"{em.reference}: all ambulances are busy. An operator has been alerted and will "
                    f"contact you. If you can, also call {settings.EMERGENCY_HOTLINE}.", em, sms_too=True)
    notify_responders(f"{'Dispatched' if ambulance else 'UNASSIGNED'} emergency {em.reference}", summary, em)
    if hospital:
        notify_hospital(hospital, f"Incoming patient - {em.priority.upper()}", summary, em)
    return em


def find_duplicate(patient):
    since = timezone.now() - timedelta(minutes=settings.DUPLICATE_WINDOW_MINUTES)
    return (EmergencyRequest.objects.filter(patient=patient, created_at__gte=since)
            .exclude(status__in=[S.COMPLETED, S.CANCELLED]).first())


def create_emergency(patient, data, allow_duplicate=False):
    if not allow_duplicate:
        existing = find_duplicate(patient)
        if existing:
            raise DuplicateEmergency(existing)
    if not data.get("contact_phone"):
        data["contact_phone"] = patient.phone
    em = EmergencyRequest(patient=patient, **data)
    triage(em)
    em.save()
    note = (f"Created. AI priority={em.priority} (confidence {em.ai_confidence})"
            if em.triage_method == "ml_model" else f"Created. AI unavailable - rule-based priority={em.priority}")
    log_status(em, note, patient)
    profile = getattr(patient, "patient_profile", None)
    try:
        dispatch(em)
    except Exception:
        # Even if dispatch crashes, the request exists and humans are told.
        log.exception("Automatic dispatch failed for %s", em.reference)
        em.refresh_from_db()
        log_status(em, "Automatic dispatch failed - operator must dispatch manually")
        notify_responders(f"MANUAL DISPATCH NEEDED {em.reference}", emergency_summary(em), em)
    if profile and profile.notify_emergency_contact and profile.emergency_contact_phone:
        sms(profile.emergency_contact_phone, "Emergency alert",
            f"{patient.get_full_name() or patient.username} requested emergency help ({em.reference}).", em)
    try:
        from . import escalation
        escalation.start(em)  # alert 1 (+ immediate escalation for severe symptoms)
    except Exception:  # the request is saved and operators are nudged even if this fails
        log.exception("Alert schedule failed for %s", em.reference)
    nudge_operators(em)
    return em


def nudge_operators(em):
    """Tell open operator dashboards to refresh their alert list (they also poll, so this is best-effort)."""
    from patient_monitoring.test_requests import push
    push("operators", "emergency", None, reference=em.reference, status=em.status)


def accept_emergency(em, operator):
    """Operator acknowledges the request: stops the dashboard siren for THIS request only.
    Dispatch/status are unchanged - the existing workflow continues as before."""
    if em.status in ("completed", "cancelled"):
        raise ValueError("This request is already closed.")
    updated = EmergencyRequest.objects.filter(pk=em.pk, accepted_at__isnull=True).update(
        accepted_at=timezone.now(), accepted_by=operator)
    em.refresh_from_db()
    if updated:
        log_status(em, f"Accepted by operator {operator.get_full_name() or operator.username}", operator)
        nudge_operators(em)
    return em


@transaction.atomic
def update_status(em, new_status, by=None, note=""):
    if new_status not in ALLOWED_TRANSITIONS.get(em.status, set()):
        raise ValueError(f"Cannot change status from {em.get_status_display()} to {new_status}")
    em.status = new_status
    if new_status in AMBULANCE_FOR_STATUS and em.assigned_ambulance_id:
        Ambulance.objects.filter(pk=em.assigned_ambulance_id).update(status=AMBULANCE_FOR_STATUS[new_status])
    if new_status in ("arrived", "completed"):
        release_assignment(em, AmbulanceAssignment.Outcome.COMPLETED)
    if new_status == "cancelled":
        release_assignment(em, AmbulanceAssignment.Outcome.CANCELLED)
    if new_status in ("completed", "cancelled"):
        em.completed_at = timezone.now()
    em.save()
    log_status(em, note, by)
    notify_user(em.patient, f"Emergency {em.reference} update", f"Status: {em.get_status_display()}", em)
    nudge_operators(em)
    if new_status in ("transporting", "cancelled") and em.assigned_hospital:
        notify_hospital(em.assigned_hospital, f"{em.reference}: {em.get_status_display()}",
                        emergency_summary(em), em)
    return em


# ------------------------------------------------------- demo movement simulator
def simulate_step(em, fraction=0.35):
    """Move the assigned ambulance part of the way to its current target.
    Only works for ambulances flagged `location_is_simulated` - real GPS units
    are never moved by the server."""
    amb = em.assigned_ambulance
    if not amb or not amb.location_is_simulated:
        return None
    if em.status in ("assigned", "en_route"):
        target = (em.latitude, em.longitude)
    elif em.status == "transporting" and em.assigned_hospital:
        target = (em.assigned_hospital.latitude, em.assigned_hospital.longitude)
    else:
        return None
    amb.latitude += (target[0] - amb.latitude) * fraction
    amb.longitude += (target[1] - amb.longitude) * fraction
    if haversine_km(amb.latitude, amb.longitude, *target) < 0.05:
        amb.latitude, amb.longitude = target
    amb.save(update_fields=["latitude", "longitude", "last_location_update"])
    remaining = haversine_km(amb.latitude, amb.longitude, *target)
    em.eta_minutes = eta_minutes(remaining, settings.AMBULANCE_AVG_SPEED_KMH)
    em.save(update_fields=["eta_minutes", "updated_at"])
    return remaining
