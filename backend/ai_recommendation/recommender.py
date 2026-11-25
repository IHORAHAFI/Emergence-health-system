"""
Hospital recommendation + ambulance selection.

Hospital score (higher is better) is a weighted multi-criteria score:
  - distance / ETA                     (closer is better)
  - specialty match for the emergency
  - bed / ICU availability
  - hospital level, weighted by priority (critical cases favour higher-level hospitals)
"""
from django.conf import settings

from ambulances.models import Ambulance
from hospitals.models import Hospital
from .geo import eta_minutes, haversine_km
from .priority import TYPE_TO_SPECIALTY

WEIGHTS = {"distance": 0.40, "specialty": 0.25, "capacity": 0.20, "level": 0.15}
PRIORITY_LEVEL_NEED = {"low": 1, "medium": 2, "high": 3, "critical": 3}
MAX_RADIUS_KM = 60


def score_hospital(h, lat, lng, emergency_type, priority):
    d = haversine_km(lat, lng, h.latitude, h.longitude)
    distance_score = max(0.0, 1 - d / MAX_RADIUS_KM)
    needed = TYPE_TO_SPECIALTY.get(emergency_type, "general")
    codes = {s.code for s in h.specialties.all()}
    specialty_score = 1.0 if needed in codes else (0.4 if "general" in codes else 0.0)
    capacity_score = min(1.0, h.available_beds / 10)
    if priority == "critical":
        capacity_score = 0.5 * capacity_score + 0.5 * min(1.0, h.icu_available / 3)
    level_score = 1.0 if h.level >= PRIORITY_LEVEL_NEED[priority] else h.level / 4
    total = (WEIGHTS["distance"] * distance_score + WEIGHTS["specialty"] * specialty_score
             + WEIGHTS["capacity"] * capacity_score + WEIGHTS["level"] * level_score)
    reasons = [f"{d:.1f} km away"]
    if specialty_score == 1.0:
        reasons.append(f"has {needed} unit")
    elif "general" in codes:
        reasons.append(f"no dedicated {needed} unit - general emergency care only")
    reasons.append(f"{h.available_beds} beds / {h.icu_available} ICU free"
                   + ("" if h.capacity_verified else " (simulated / unverified)"))
    if h.level >= PRIORITY_LEVEL_NEED[priority]:
        reasons.append(f"{h.get_level_display()} suits a {priority} case")
    return {
        "hospital_id": h.id, "name": h.name, "distance_km": round(d, 2),
        "phone": h.phone, "capacity_verified": h.capacity_verified,
        "services": sorted(codes),
        "eta_minutes": eta_minutes(d, settings.AMBULANCE_AVG_SPEED_KMH),
        "score": round(total, 3), "reasons": reasons,
        "latitude": h.latitude, "longitude": h.longitude,
    }


def recommend_hospitals(lat, lng, emergency_type="other", priority="medium", top_n=3):
    qs = Hospital.objects.filter(is_active=True, has_emergency_unit=True).prefetch_related("specialties")
    candidates = []
    for h in qs:
        if h.available_beds == 0 and h.icu_available == 0:
            continue
        if haversine_km(lat, lng, h.latitude, h.longitude) > MAX_RADIUS_KM:
            continue
        candidates.append(score_hospital(h, lat, lng, emergency_type, priority))
    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates[:top_n]


def rank_ambulances(lat, lng, priority="medium"):
    """Available ambulances, best first, as (ambulance, distance_km, reason).
    Nearest wins, but for high/critical cases an advanced (ALS) ambulance gets a
    3 km advantage over a basic one."""
    ranked = []
    for a in Ambulance.objects.filter(status=Ambulance.Status.AVAILABLE):
        d = haversine_km(lat, lng, a.latitude, a.longitude)
        needs_als = priority in ("high", "critical")
        penalty = 3 if needs_als and a.ambulance_type != Ambulance.Type.ALS else 0
        reason = f"{d:.1f} km away, {a.get_ambulance_type_display()}"
        if needs_als and a.ambulance_type == Ambulance.Type.ALS:
            reason += " (advanced unit preferred for this priority)"
        ranked.append((d + penalty, a, round(d, 2), reason))
    ranked.sort(key=lambda r: r[0])
    return [(a, d, reason) for _, a, d, reason in ranked]


def select_ambulance(lat, lng, priority="medium"):
    """Best available ambulance (kept for backwards compatibility)."""
    ranked = rank_ambulances(lat, lng, priority)
    return (ranked[0][0], ranked[0][1]) if ranked else (None, None)
