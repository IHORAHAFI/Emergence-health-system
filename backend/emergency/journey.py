"""SIMULATED ambulance journey engine.

    HOSPITAL --(road route)--> PATIENT --(pickup)--> PATIENT ON BOARD --(road route)--> HOSPITAL

* Road routes come from an OSRM routing server (ROUTING_URL, default the public OSRM demo server; free, no API key).
  If no route can be calculated the journey stops in FAILED with the reason - it never draws a fake straight line
  and never pretends the ambulance arrived.
* The SERVER is the source of truth. The position is a pure function of (stored road route, time spent in the
  current leg, simulation speed-up, pause time). Every step is an optimistic-locked UPDATE (field `version`), so the
  background scheduler, several dashboards and page reloads can all call `advance()` at the same time without
  duplicate notifications or competing movement loops.
* Simulation speed is separate from the real-world estimate: the routing server's travel time (free-flow, no live
  traffic) is shown as the estimate, the marker moves `JOURNEY_SIM_SPEEDUP` times faster so a demo can be watched.
* Only ambulances flagged `location_is_simulated` are moved. A unit reporting real GPS is never touched.
"""
import json
import logging
import math
import urllib.parse
import urllib.request
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from ai_recommendation.geo import haversine_km
from .models import AmbulanceJourney, EmergencyRequest

log = logging.getLogger(__name__)
J = AmbulanceJourney.Status
MOVING = (J.EN_ROUTE_TO_PATIENT, J.EN_ROUTE_TO_HOSPITAL)
TERMINAL = (J.ARRIVED_AT_HOSPITAL, J.CANCELLED)
MAX_ROUTE_POINTS = 1500


class JourneyError(Exception):
    pass


class RoutingError(Exception):
    pass


def cfg(name, default):
    return getattr(settings, name, default)


# ---------------------------------------------------------------------------------------------- routing service
def fetch_route(start, end):
    """Road route between two (lat, lng) points -> {"coords": [[lat, lng], ...], "distance_m", "duration_s", "source"}.
    Raises RoutingError when the service is down or finds no road route (the caller must NOT invent one)."""
    base = cfg("ROUTING_URL", "https://router.project-osrm.org").rstrip("/")
    path = f"{start[1]:.6f},{start[0]:.6f};{end[1]:.6f},{end[0]:.6f}"
    url = f"{base}/route/v1/driving/{path}?" + urllib.parse.urlencode({"overview": "full", "geometries": "geojson"})
    req = urllib.request.Request(url, headers={"User-Agent": "smart-emergency-simulation/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=cfg("ROUTING_TIMEOUT", 10)) as r:
            data = json.loads(r.read().decode())
    except Exception as e:
        raise RoutingError(f"Routing service unavailable ({e.__class__.__name__})") from e
    if data.get("code") != "Ok" or not data.get("routes"):
        raise RoutingError(f"No road route found ({data.get('code', 'unknown')})")
    rt = data["routes"][0]
    coords = [[c[1], c[0]] for c in rt["geometry"]["coordinates"]]
    if len(coords) < 2:
        raise RoutingError("Routing service returned an empty route")
    if len(coords) > MAX_ROUTE_POINTS:  # keep the payload small; keep first/last points exact
        step = math.ceil(len(coords) / MAX_ROUTE_POINTS)
        coords = coords[::step] + ([coords[-1]] if (len(coords) - 1) % step else [])
    return {"coords": coords, "distance_m": float(rt["distance"]), "duration_s": float(rt["duration"]),
            "source": "OSRM (OpenStreetMap road network)"}


# ------------------------------------------------------------------------------------------------- geometry
def prepare(route):
    """Adds cumulative distances (metres) along the polyline."""
    coords = route["coords"]
    cum = [0.0]
    for (a, b), (c, d) in zip(coords, coords[1:]):
        cum.append(cum[-1] + haversine_km(a, b, c, d) * 1000)
    route["cum_m"] = cum
    route["geom_m"] = cum[-1]
    return route


def point_at(route, fraction):
    """(lat, lng, bearing_deg) at `fraction` (0..1) of the route's length."""
    coords, cum = route["coords"], route["cum_m"]
    fraction = min(max(fraction, 0.0), 1.0)
    target = fraction * cum[-1]
    lo, hi = 0, len(cum) - 1
    while lo < hi - 1:  # binary search for the segment
        mid = (lo + hi) // 2
        if cum[mid] <= target:
            lo = mid
        else:
            hi = mid
    seg = cum[hi] - cum[lo]
    t = (target - cum[lo]) / seg if seg > 0 else 0
    (a, b), (c, d) = coords[lo], coords[hi]
    return a + (c - a) * t, b + (d - b) * t, bearing(a, b, c, d)


def bearing(lat1, lng1, lat2, lng2):
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lng2 - lng1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def valid_coords(lat, lng):
    return (lat is not None and lng is not None and -90 <= lat <= 90 and -180 <= lng <= 180
            and not (abs(lat) < 1e-6 and abs(lng) < 1e-6))


def patient_location(em):
    """The request's latest stored patient position, or None. Never invented."""
    if em.location_source == EmergencyRequest.LocationSource.NONE or not valid_coords(em.latitude, em.longitude):
        return None
    return em.latitude, em.longitude


# ---------------------------------------------------------------------------------------------------- timing
def leg_route(j):
    if j.status in (J.EN_ROUTE_TO_PATIENT,):
        return j.route_to_patient
    if j.status in (J.EN_ROUTE_TO_HOSPITAL,):
        return j.route_to_hospital
    return None


def sim_duration(route, speedup):
    """Seconds the simulated marker needs for the whole leg (at least 15 s so it stays visible)."""
    return max(route["duration_s"] / max(speedup, 0.1), 15.0)


def elapsed(j, now):
    if not j.phase_started_at:
        return 0.0
    end = j.paused_at or now
    return max((end - j.phase_started_at).total_seconds() - j.paused_seconds, 0.0)


def _transition(j, expected_status, **fields):
    """Optimistic-locked state change. Returns True only for the ONE caller that won."""
    n = AmbulanceJourney.objects.filter(pk=j.pk, version=j.version, status=expected_status).update(
        version=F("version") + 1, updated_at=timezone.now(), **fields)
    j.refresh_from_db()
    return n == 1


def _move_ambulance(j, lat, lng):
    from ambulances.models import Ambulance
    Ambulance.objects.filter(pk=j.ambulance_id, location_is_simulated=True).update(
        latitude=lat, longitude=lng, last_location_update=timezone.now())


def _em_status(j, new, note):
    """Keep the emergency's existing workflow in step (it also updates ambulance status and notifies people)."""
    from . import services
    em = EmergencyRequest.objects.get(pk=j.emergency_id)
    try:
        services.update_status(em, new, None, f"SIMULATION: {note}")
    except ValueError as e:  # an operator already moved the request on by hand - keep their decision
        log.info("journey %s: emergency status not changed (%s)", j.pk, e)


def _push(j, **extra):
    from patient_monitoring.test_requests import push
    msg = dict(journey=j.pk, status=j.status, emergency=j.emergency_id, **extra)
    push("operators", "journey", None, **msg)
    push(f"patient_{j.patient_id}", "journey", None, **msg)


def _tell(j, title, message, patient=True):
    from notifications.services import notify_responders, notify_user
    em = j.emergency
    if patient:
        notify_user(em.patient, title, message, em)
    notify_responders(title, message, em)


# --------------------------------------------------------------------------------------------------- creation
def create_for(em, by=None):
    """Create the SIMULATED journey for a dispatched emergency. Raises JourneyError (never creates duplicates)."""
    em.refresh_from_db()
    amb = em.assigned_ambulance
    if not amb:
        raise JourneyError("No ambulance is assigned to this emergency.")
    if not amb.location_is_simulated:
        raise JourneyError("This ambulance reports real GPS - it is never moved by the simulator.")
    if em.status in ("completed", "cancelled", "arrived"):
        raise JourneyError("This emergency is already closed.")
    if AmbulanceJourney.objects.filter(emergency=em, is_open=True).exists():
        raise JourneyError("A simulated journey already exists for this emergency.")
    start = amb.base_hospital
    dest = em.assigned_hospital or start
    j = AmbulanceJourney(
        emergency=em, patient=em.patient, ambulance=amb, start_hospital=start, destination_hospital=dest,
        sim_speedup=float(cfg("JOURNEY_SIM_SPEEDUP", 10)), pickup_seconds=int(cfg("JOURNEY_PICKUP_SECONDS", 20)))
    if not start:
        j.status, j.error = J.FAILED, "The ambulance has no base hospital - set one before the journey can start."
    elif not valid_coords(start.latitude, start.longitude):
        j.status, j.error = J.FAILED, f"Hospital {start.name} has no valid coordinates."
    else:
        j.start_latitude, j.start_longitude = start.latitude, start.longitude
        j.current_latitude, j.current_longitude = start.latitude, start.longitude
        pos = patient_location(em)
        if pos:
            j.pickup_latitude, j.pickup_longitude = pos
            j.status = J.ROUTING
        else:
            j.status = J.WAITING_LOCATION
            j.error = "The patient has not shared a valid location - confirm it before the ambulance can leave."
    try:
        with transaction.atomic():
            j.save()
    except IntegrityError:
        raise JourneyError("A simulated journey already exists for this emergency or this ambulance.")
    if j.current_latitude is not None:
        _move_ambulance(j, j.current_latitude, j.current_longitude)  # the ambulance appears at its hospital
    from . import services
    services.log_status(em, f"SIMULATED journey #{j.pk} created ({j.get_status_display()})", by)
    if j.status == J.WAITING_LOCATION:
        _tell(j, f"LOCATION NEEDED {em.reference}", j.error, patient=False)
    elif j.status == J.FAILED:
        _tell(j, f"SIMULATION STOPPED {em.reference}", j.error, patient=False)
    _push(j)
    return j


def auto_start(em):
    """Called after a successful dispatch. Never raises - dispatch must not fail because of the simulation."""
    if not cfg("JOURNEY_AUTO_START", True):
        return None
    try:
        return create_for(em)  # the scheduler (or the next page load) calculates the route within seconds
    except JourneyError as e:
        log.info("journey not started for %s: %s", em.reference, e)
    except Exception:
        log.exception("journey auto start failed for %s", em.reference)
    return None


# ---------------------------------------------------------------------------------------------------- engine
def advance(j, now=None):
    """Move the journey forward to `now`. Safe to call from any number of places at once."""
    for _ in range(8):  # several phases may be due at once (e.g. after the server slept)
        now = now or timezone.now()
        j.refresh_from_db()
        if not j.is_open or j.paused_at:
            return j
        if not _step(j, now):
            return j
    return j


def _route_leg(j, now, leg):
    start = (j.start_latitude, j.start_longitude) if leg == "patient" else (j.pickup_latitude, j.pickup_longitude)
    end = (j.pickup_latitude, j.pickup_longitude) if leg == "patient" else (j.destination_latitude,
                                                                            j.destination_longitude)
    expected = j.status
    try:
        route = prepare(fetch_route(start, end))
    except RoutingError as e:
        if _transition(j, expected, status=J.FAILED, error=str(e)[:255]):
            _tell(j, f"SIMULATION STOPPED {j.emergency.reference}",
                  f"{e}. The last valid journey state is kept; press Retry when the routing service is back.",
                  patient=False)
            _push(j)
        return False
    field = "route_to_patient" if leg == "patient" else "route_to_hospital"
    new = J.EN_ROUTE_TO_PATIENT if leg == "patient" else J.EN_ROUTE_TO_HOSPITAL
    extra = {"started_at": now} if leg == "patient" else {}
    if _transition(j, expected, status=new, error="", progress=0, phase_started_at=now, paused_seconds=0,
                   routing_source=route["source"], **{field: route}, **extra):
        if leg == "patient":
            _em_status(j, "en_route", f"ambulance left {j.start_hospital.name} on a road route "
                                      f"({route['distance_m'] / 1000:.1f} km)")
        _push(j)
    return True


def _step(j, now):
    """One state-machine step. Returns True if something changed (call again)."""
    em = j.emergency
    if j.status == J.WAITING_LOCATION:
        pos = patient_location(em)
        if not pos:
            return False
        if _transition(j, J.WAITING_LOCATION, status=J.ROUTING, error="",
                       pickup_latitude=pos[0], pickup_longitude=pos[1]):
            _push(j)
        return True

    if j.status == J.ROUTING:
        return _route_leg(j, now, "patient")

    if j.status in MOVING:
        route = leg_route(j)
        dur = sim_duration(route, j.sim_speedup)
        el = elapsed(j, now)
        frac = 1.0 if el >= dur - 0.05 else el / dur  # tolerate clock rounding at the very end
        lat, lng, _ = point_at(route, frac)
        if frac < 1.0:
            AmbulanceJourney.objects.filter(pk=j.pk, version=j.version).update(
                current_latitude=lat, current_longitude=lng, progress=frac, updated_at=now)
            _move_ambulance(j, lat, lng)
            return False
        if j.status == J.EN_ROUTE_TO_PATIENT:
            if _transition(j, J.EN_ROUTE_TO_PATIENT, status=J.ARRIVED_AT_PATIENT, progress=1,
                           current_latitude=lat, current_longitude=lng, arrived_patient_at=now,
                           phase_started_at=now, paused_seconds=0):
                _move_ambulance(j, lat, lng)
                _em_status(j, "on_scene", "ambulance arrived at the patient's location")
                _tell(j, f"Ambulance arrived - {em.reference}",
                      f"SIMULATED: ambulance {j.ambulance.plate_number} has arrived at the patient's location "
                      f"and is ready for pickup.")
                _push(j)
            return True
        if _transition(j, J.EN_ROUTE_TO_HOSPITAL, status=J.ARRIVED_AT_HOSPITAL, progress=1, is_open=False,
                       current_latitude=lat, current_longitude=lng, arrived_hospital_at=now):
            _move_ambulance(j, lat, lng)
            _em_status(j, "arrived", f"ambulance arrived at {j.destination_hospital.name}")
            _tell(j, f"Arrived at hospital - {em.reference}",
                  f"SIMULATED: ambulance {j.ambulance.plate_number} has arrived at {j.destination_hospital.name}. "
                  f"The simulated journey is complete.")
            _push(j)
        return True

    if j.status == J.ARRIVED_AT_PATIENT:
        if elapsed(j, now) < cfg("JOURNEY_ARRIVAL_HOLD_SECONDS", 5):
            return False
        if _transition(j, J.ARRIVED_AT_PATIENT, status=J.PICKING_UP_PATIENT, phase_started_at=now, paused_seconds=0):
            _tell(j, f"Patient pickup - {em.reference}",
                  "SIMULATED: the crew is bringing the patient on board.", patient=False)
            _push(j)
        return True

    if j.status == J.PICKING_UP_PATIENT:
        if elapsed(j, now) < j.pickup_seconds:
            return False
        dest = _destination(j)
        if not dest:
            return False
        if _transition(j, J.PICKING_UP_PATIENT, status=J.PATIENT_ON_BOARD, pickup_completed_at=now,
                       destination_hospital=dest, destination_latitude=dest.latitude,
                       destination_longitude=dest.longitude, phase_started_at=now, paused_seconds=0):
            _em_status(j, "transporting", f"patient on board, transporting to {dest.name}")
            _push(j)
        return True

    if j.status == J.PATIENT_ON_BOARD:
        return _route_leg(j, now, "hospital")
    return False


def _destination(j):
    """Assigned hospital; if it closed meanwhile, the ambulance's own hospital; else stop with a clear reason."""
    for h in (j.destination_hospital, j.start_hospital):
        if h and h.is_active and valid_coords(h.latitude, h.longitude):
            if h != j.destination_hospital:
                _tell(j, f"Destination changed - {j.emergency.reference}",
                      f"SIMULATED: {j.destination_hospital} is unavailable; transporting to {h.name} instead.")
            return h
    if _transition(j, J.PICKING_UP_PATIENT, status=J.FAILED,
                   error="No available destination hospital with valid coordinates."):
        _tell(j, f"SIMULATION STOPPED {j.emergency.reference}", j.error, patient=False)
        _push(j)
    return None


def tick(now=None):
    """Advance every open, running journey (called by the background scheduler)."""
    n = 0
    qs = AmbulanceJourney.objects.filter(is_open=True, paused_at__isnull=True).exclude(status=J.FAILED)
    for j in qs.select_related("emergency", "ambulance"):
        try:
            before = (j.status, j.version)
            j = advance(j, now)
            n += (j.status, j.version) != before or j.status in MOVING
            if j.status in MOVING:
                _push(j, position=[j.current_latitude, j.current_longitude])
        except Exception:
            log.exception("journey %s step failed", j.pk)
    return n


# --------------------------------------------------------------------------------------------- operator actions
def act(j, action, user=None, now=None):
    now = now or timezone.now()
    j.refresh_from_db()
    from . import services
    if action == "pause":
        if j.status not in MOVING + (J.PICKING_UP_PATIENT, J.ARRIVED_AT_PATIENT) or j.paused_at:
            raise JourneyError("Only a running journey can be paused.")
        advance(j, now)
        if not _transition(j, j.status, paused_at=now):
            raise JourneyError("The journey changed meanwhile - try again.")
    elif action == "resume":
        if not j.paused_at:
            raise JourneyError("The journey is not paused.")
        pause = (now - j.paused_at).total_seconds()
        if not _transition(j, j.status, paused_at=None, paused_seconds=j.paused_seconds + pause):
            raise JourneyError("The journey changed meanwhile - try again.")
    elif action == "retry":
        if j.status != J.FAILED:
            raise JourneyError("Only a stopped journey can be retried.")
        if j.route_to_hospital or j.pickup_completed_at:
            back = J.PATIENT_ON_BOARD
        elif j.start_latitude is None:
            j.is_open = False
            AmbulanceJourney.objects.filter(pk=j.pk).update(is_open=False, status=J.CANCELLED, cancelled_at=now)
            em = j.emergency
            return create_for(em, user)  # re-check base hospital / coordinates from scratch
        else:
            back = J.ROUTING if patient_location(j.emergency) else J.WAITING_LOCATION
        _transition(j, J.FAILED, status=back, error="")
    elif action == "reset":
        if j.status not in (J.WAITING_LOCATION, J.ROUTING, J.EN_ROUTE_TO_PATIENT, J.FAILED) or j.pickup_completed_at:
            raise JourneyError("Reset is only allowed before the patient is picked up.")
        if j.start_latitude is None:
            raise JourneyError(j.error or "The journey has no start hospital.")
        if not _transition(j, j.status, status=J.ROUTING, error="", progress=0, route_to_patient={},
                           current_latitude=j.start_latitude, current_longitude=j.start_longitude,
                           phase_started_at=None, paused_at=None, paused_seconds=0, started_at=None):
            raise JourneyError("The journey changed meanwhile - try again.")
        _move_ambulance(j, j.start_latitude, j.start_longitude)
    elif action == "cancel":
        close(j, "Simulation cancelled by operator", user)
        return j
    else:
        raise JourneyError("Unknown action.")
    services.log_status(j.emergency, f"SIMULATED journey #{j.pk}: {action}", user)
    _push(j)
    return advance(j, now)


def close(j, reason, user=None):
    """Stop the simulation (the emergency itself is not changed). Keeps the last position."""
    if not j.is_open:
        return j
    if _transition(j, j.status, status=J.CANCELLED, is_open=False, cancelled_at=timezone.now(), error=reason[:255]):
        from . import services
        services.log_status(j.emergency, f"SIMULATED journey #{j.pk} stopped: {reason}", user)
        _push(j)
    return j


def close_for_emergency(em, reason):
    for j in AmbulanceJourney.objects.filter(emergency=em, is_open=True):
        close(j, reason)


def set_pickup_location(j, lat, lng, user):
    """Operator confirms where the patient is (e.g. after a phone call) - only while the ambulance has not left."""
    from .models import EmergencyLocation
    if j.status not in (J.WAITING_LOCATION, J.FAILED, J.ROUTING) or j.route_to_patient:
        raise JourneyError("The pickup location can only be set before the ambulance leaves.")
    if not valid_coords(lat, lng):
        raise JourneyError("Invalid coordinates.")
    em = j.emergency
    em.latitude, em.longitude = lat, lng
    em.location_source = EmergencyRequest.LocationSource.MANUAL
    em.location_updated_at = timezone.now()
    em.location_description = (f"Confirmed by operator {user.get_full_name() or user.username}")[:255]
    em.save(update_fields=["latitude", "longitude", "location_source", "location_updated_at",
                           "location_description", "updated_at"])
    EmergencyLocation.objects.create(emergency=em, latitude=lat, longitude=lng, source="manual",
                                     recorded_at=em.location_updated_at, submitted_by=user)
    from . import services
    services.log_status(em, f"Pickup location confirmed by operator: {lat:.5f}, {lng:.5f}", user)
    if j.status == J.FAILED and j.start_latitude is not None:
        _transition(j, J.FAILED, status=J.WAITING_LOCATION, error="")
    return advance(j)


# ------------------------------------------------------------------------------------------------ serialization
def state(j, now=None):
    """Everything a map needs. Distances/times come from the routing result - never invented."""
    now = now or timezone.now()
    route = leg_route(j)
    data = {
        "id": j.pk, "simulated": True, "label": "SIMULATED", "status": j.status,
        "status_display": j.get_status_display(), "is_open": j.is_open, "paused": bool(j.paused_at),
        "error": j.error, "emergency": j.emergency.reference,
        "ambulance": {"id": j.ambulance_id, "plate": j.ambulance.plate_number},
        "start_hospital": j.start_hospital and {"name": j.start_hospital.name,
                                                "pos": [j.start_latitude, j.start_longitude]},
        "destination_hospital": j.destination_hospital and {
            "name": j.destination_hospital.name,
            "pos": [j.destination_latitude or j.destination_hospital.latitude,
                    j.destination_longitude or j.destination_hospital.longitude]},
        "pickup": [j.pickup_latitude, j.pickup_longitude] if j.pickup_latitude is not None else None,
        "position": [j.current_latitude, j.current_longitude] if j.current_latitude is not None else None,
        "progress": j.progress, "routing_source": j.routing_source,
        "routes": {k: v.get("coords") for k, v in (("to_patient", j.route_to_patient),
                                                   ("to_hospital", j.route_to_hospital)) if v},
        "pickup_seconds": j.pickup_seconds, "sim_speedup": j.sim_speedup,
        "server_time": now.isoformat(),
        "times": {k: getattr(j, k) and getattr(j, k).isoformat() for k in (
            "created_at", "started_at", "arrived_patient_at", "pickup_completed_at", "arrived_hospital_at",
            "cancelled_at")},
        "leg": None,
    }
    if route:
        dur = sim_duration(route, j.sim_speedup)
        el = min(elapsed(j, now), dur)
        frac = el / dur
        lat, lng, brg = point_at(route, frac)
        data["position"], data["bearing"], data["progress"] = [lat, lng], brg, frac
        data["leg"] = {
            "name": "to_patient" if j.status == J.EN_ROUTE_TO_PATIENT else "to_hospital",
            "coords": route["coords"], "cum_m": route["cum_m"],
            "distance_m": route["distance_m"], "duration_s": route["duration_s"],
            "remaining_m": route["distance_m"] * (1 - frac),
            "remaining_real_s": route["duration_s"] * (1 - frac),   # routing estimate, free-flow, no live traffic
            "sim_duration_s": dur, "sim_elapsed_s": el, "sim_remaining_s": dur - el,
        }
    elif j.status in (J.ARRIVED_AT_PATIENT, J.PICKING_UP_PATIENT):
        data["pickup_remaining_s"] = (max(j.pickup_seconds - elapsed(j, now), 0)
                                      if j.status == J.PICKING_UP_PATIENT else None)
    return data


def visible_to(user):
    """Journeys a user may see (no unrelated patients or ambulances)."""
    qs = AmbulanceJourney.objects.select_related("emergency", "ambulance", "start_hospital", "destination_hospital")
    if user.is_superuser or user.role in ("admin", "operator"):
        return qs
    if user.role == "patient":
        return qs.filter(patient=user)
    if user.role == "paramedic":
        return qs.filter(ambulance__driver=user)
    if user.role == "hospital" and user.is_approved and user.hospital_id:
        return qs.filter(emergency__assigned_hospital_id=user.hospital_id)
    return qs.none()


def can_control(user):
    return user.is_authenticated and (user.is_superuser or user.role in ("admin", "operator"))


def recent_cutoff():
    return timezone.now() - timedelta(hours=6)
