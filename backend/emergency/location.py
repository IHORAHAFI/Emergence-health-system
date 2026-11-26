"""Patient location for emergencies: validated updates from the patient's own device, history, and the status
every map shows (web dashboard, emergency page, patient tracking page, mobile app).

Status shown to operators / staff / the patient - never an invented position:
    live        GPS received within LOCATION_LIVE_SECONDS (default 2 minutes)
    last_known  GPS older than that (shown with its age and a "stale" warning)
    manual      chosen on the map by the patient (not GPS)
    simulated   demo data
    unavailable no location was shared (no marker is drawn; operators must phone the patient)

Updates come only from the patient's own device, only for their own OPEN emergency, while they share it.
A history row is written only when the patient really moved (MIN_MOVE_M) or after MIN_INTERVAL - a phone
sending the same position over and over refreshes the timestamp without filling the table.
"""
import logging
import math
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import EmergencyLocation, EmergencyRequest

log = logging.getLogger(__name__)
MIN_MOVE_M = 15
MIN_INTERVAL = timedelta(seconds=60)
MAX_ACCURACY_M = 5000      # worse than 5 km is not a usable emergency position
MAX_CLOCK_SKEW = timedelta(minutes=5)
MAX_AGE = timedelta(minutes=10)  # a position measured longer ago than this is not "live" and is refused


class LocationError(Exception):
    pass


def live_seconds():
    return int(getattr(settings, "LOCATION_LIVE_SECONDS", 120))


def distance_m(lat1, lng1, lat2, lng2):
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _number(v, name):
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise LocationError(f"{name} must be a number.")
    if math.isnan(f) or math.isinf(f):
        raise LocationError(f"{name} must be a number.")
    return f


def validate(latitude, longitude, accuracy=None, recorded_at=None):
    lat, lng = _number(latitude, "latitude"), _number(longitude, "longitude")
    if not -90 <= lat <= 90:
        raise LocationError("latitude must be between -90 and 90.")
    if not -180 <= lng <= 180:
        raise LocationError("longitude must be between -180 and 180.")
    if lat == 0 and lng == 0:
        raise LocationError("0,0 is not a real position (device returned no fix).")
    acc = None
    if accuracy not in (None, ""):
        acc = _number(accuracy, "accuracy")
        if acc < 0 or acc > MAX_ACCURACY_M:
            raise LocationError(f"accuracy must be between 0 and {MAX_ACCURACY_M} m.")
    now = timezone.now()
    when = now
    if recorded_at not in (None, ""):
        when = recorded_at if hasattr(recorded_at, "tzinfo") else parse_datetime(str(recorded_at))
        if when is None:
            raise LocationError("timestamp is not a valid date/time.")
        if timezone.is_naive(when):
            when = timezone.make_aware(when)
        if when > now + MAX_CLOCK_SKEW:
            raise LocationError("timestamp is in the future - check the device clock.")
        if now - when > MAX_AGE:
            raise LocationError("this position is too old to be a live update.")
    return lat, lng, acc, when


def update(em, user, latitude, longitude, accuracy=None, recorded_at=None, source="gps"):
    """Patient's device sends its position for its own open emergency. Returns (status_dict, stored: bool)."""
    if user.pk != em.patient_id:
        raise PermissionError("Only the patient can share their own location.")
    if not em.is_active:
        raise LocationError("This emergency is closed - location sharing has stopped.")
    if source not in ("gps", "manual"):
        raise LocationError("source must be gps or manual.")
    lat, lng, acc, when = validate(latitude, longitude, accuracy, recorded_at)
    last = em.location_history.order_by("-recorded_at").first()
    moved = last is None or distance_m(last.latitude, last.longitude, lat, lng) >= MIN_MOVE_M
    due = last is None or when - last.recorded_at >= MIN_INTERVAL or source != last.source
    stored = moved or due
    if stored:
        EmergencyLocation.objects.create(emergency=em, latitude=lat, longitude=lng, accuracy_m=acc, source=source,
                                         recorded_at=when, submitted_by=user)
    started = em.location_source != source or not em.location_consent
    EmergencyRequest.objects.filter(pk=em.pk).update(
        latitude=lat, longitude=lng, location_accuracy_m=acc, location_source=source, location_consent=True,
        location_updated_at=when, location_description="" if em.location_source == "none" else em.location_description)
    em.refresh_from_db()
    if started:
        from .services import log_status
        log_status(em, "Patient is sharing live GPS location" if source == "gps" else "Patient set the location manually",
                   user)
    _nudge(em)
    return status(em), stored


def _nudge(em):
    try:
        from patient_monitoring.test_requests import push
        push("operators", "location", None, reference=em.reference)
    except Exception:
        log.exception("location nudge failed")


def status(em, now=None):
    """What a map may show for this emergency. 'unavailable' never carries coordinates."""
    now = now or timezone.now()
    src = em.location_source
    if src == "none" or em.latitude is None:
        return {"state": "unavailable", "label": "LOCATION UNAVAILABLE", "latitude": None, "longitude": None,
                "accuracy_m": None, "updated_at": None, "age_seconds": None, "stale": False, "source": "none",
                "note": "No location shared - call the patient or caregiver."}
    updated = em.location_updated_at or em.created_at
    age = int((now - updated).total_seconds())
    if src == "gps":
        live = age <= live_seconds() and em.is_active
        state, label = ("live", "LIVE LOCATION") if live else ("last_known", "LAST KNOWN LOCATION")
    elif src == "manual":
        state, label = "manual", "MANUAL LOCATION"
    else:
        state, label = "simulated", "SIMULATED LOCATION"
    return {"state": state, "label": label, "latitude": em.latitude, "longitude": em.longitude,
            "accuracy_m": em.location_accuracy_m, "updated_at": updated.isoformat(), "age_seconds": age,
            "stale": state == "last_known", "source": src,
            "note": "" if state != "last_known" else "Position may have changed - no recent GPS update."}


def marker(em, now=None):
    """Everything an operator map needs for one emergency marker."""
    s = status(em, now)
    return {"id": em.pk, "reference": em.reference, "type": em.get_emergency_type_display(), "priority": em.priority,
            "status": em.status, "status_display": em.get_status_display(),
            "patient": em.patient_name or em.patient.get_full_name() or em.patient.username,
            "patient_code": f"P-{em.patient_id:05d}", "location": s}
