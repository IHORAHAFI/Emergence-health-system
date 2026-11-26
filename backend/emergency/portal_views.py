"""Web pages for patients and ambulance crews (the mobile app offers the same via /api/)."""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ai_recommendation.geo import haversine_km
from ai_recommendation.recommender import recommend_hospitals
from ambulances.models import Ambulance
from hospitals.models import Hospital
from notifications.models import Notification
from . import services
from .forms import EmergencyRequestForm
from .models import EmergencyRequest


def role_required(*roles):
    def deco(view):
        @login_required
        def wrapper(request, *a, **kw):
            if request.user.role not in roles:
                return HttpResponseForbidden("You do not have access to this page.")
            return view(request, *a, **kw)
        return wrapper
    return deco


def landing(request):
    return render(request, "portal/landing.html", {
        "hospital_count": Hospital.objects.filter(is_active=True).count(),
        "ambulance_free": Ambulance.objects.filter(status="available").count(),
        "hotline": settings.EMERGENCY_HOTLINE,
        "services": [
            ("bi-truck-front", "Ambulance dispatch", "Nearest free ambulance, tracked live."),
            ("bi-geo-alt", "Location", "GPS with your permission, or tap the map."),
            ("bi-hospital", "Best hospital", "Ranked by distance, services and capacity."),
            ("bi-activity", "Vital signs", "Bluetooth and IoT devices, live to the team."),
            ("bi-bell", "Alerts", "In-app and SMS updates."),
            ("bi-shield-check", "Private", "Only your care team sees your data."),
        ],
    })


VITAL_CARDS = [  # key, label, icon, unit, fields
    ("temperature_c", "Temperature", "bi-thermometer-half", "°C", ("temperature_c",)),
    ("heart_rate_bpm", "Heart rate", "bi-heart-pulse", "bpm", ("heart_rate_bpm",)),
    ("spo2_pct", "Oxygen (SpO₂)", "bi-lungs", "%", ("spo2_pct",)),
    ("blood_pressure", "Blood pressure", "bi-activity", "mmHg", ("systolic_mmhg", "diastolic_mmhg")),
    ("respiratory_rate", "Breathing", "bi-wind", "/min", ("respiratory_rate",)),
]


def _vitals(user, limit=60):
    """Latest value + trend of each vital sign from the patient's own stored readings (real or simulated)."""
    from patient_monitoring import services as mon
    from patient_monitoring.models import VitalSignReading
    rows = list(VitalSignReading.objects.filter(patient=user).order_by("-measured_at")[:limit])[::-1]
    cards = []
    for key, label, icon, unit, fields in VITAL_CARDS:
        have = [r for r in rows if getattr(r, fields[0]) is not None]
        if not have:
            cards.append({"key": key, "label": label, "icon": icon, "unit": unit, "value": None})
            continue
        last = have[-1]
        value = f"{last.systolic_mmhg}/{last.diastolic_mmhg}" if key == "blood_pressure" else (
            float(last.temperature_c) if key == "temperature_c" else getattr(last, key))
        flags = mon.threshold_flags(last)
        cards.append({"key": key, "label": label, "icon": icon, "unit": unit, "value": value, "at": last.measured_at,
                      "simulated": last.is_simulated, "abnormal": any(f in flags for f in fields),
                      "series": [float(getattr(r, fields[0])) for r in have[-20:]]})
    return cards


def _latest_test(user):
    """Most recent medical test request and its latest values, for the patient dashboard."""
    from patient_monitoring import test_requests as tests
    t = user.test_requests.first()
    return {"latest_test": t, "latest_results": tests.summary(t) if t else {},
            "open_tests": user.test_requests.filter(status__in=["pending", "accepted", "testing"]).count()}


@role_required("patient")
def patient_home(request):
    qs = request.user.emergencies.select_related("assigned_hospital", "assigned_ambulance",
                                                 "assigned_ambulance__driver")
    return render(request, "portal/patient_home.html", {
        "active": qs.exclude(status__in=["completed", "cancelled"]).first(),
        "history": qs.filter(status__in=["completed", "cancelled"])[:10],
        "total_requests": qs.count(),
        **_latest_test(request.user),
        "vitals": (v := _vitals(request.user)),
        "vm": {c["key"]: c for c in v},
        "recent_tests": request.user.test_requests.all()[:5],
        "notifications": request.user.notifications.filter(channel="in_app")[:5],
        "profile": getattr(request.user, "patient_profile", None),
        "hotline": settings.EMERGENCY_HOTLINE,
    })


@role_required("patient")
def request_emergency(request):
    existing = services.find_duplicate(request.user)
    if existing and request.method == "GET":
        messages.info(request, f"You already have an active emergency ({existing.reference}).")
        return redirect("track", ref=existing.reference)
    profile = getattr(request.user, "patient_profile", None)
    form = EmergencyRequestForm(request.POST or None, initial={
        "contact_phone": request.user.phone,
        "emergency_type": "other",  # nothing to choose under stress; staff can reclassify
        "patient_name": request.user.get_full_name(),
        "gender": (profile.gender or "").lower() if profile else "",
        "age": profile.age if profile else None})
    if request.method == "POST" and form.is_valid():
        try:
            em = services.create_emergency(request.user, form.to_emergency_data())
        except services.DuplicateEmergency as dup:
            messages.warning(request, f"{dup} Your earlier request is still being handled.")
            return redirect("track", ref=dup.existing.reference)
        messages.success(request, f"Emergency request sent. Your reference number is {em.reference}.")
        return redirect("track", ref=em.reference)
    return render(request, "portal/request.html", {
        "form": form, "hotline": settings.EMERGENCY_HOTLINE,
        "severity": [form[n] for n in ("conscious", "breathing_difficulty", "severe_bleeding", "chest_pain",
                                       "seizure", "head_injury", "pregnant")],
        "hospitals": Hospital.objects.filter(is_active=True)})


def _can_view(user, em):
    if user.role in ("admin", "operator") or user.is_superuser:
        return True
    if user.role == "patient":
        return em.patient_id == user.id
    if user.role == "paramedic":
        return em.assigned_ambulance is not None and em.assigned_ambulance.driver_id == user.id
    if user.role == "hospital":
        return user.is_approved and user.hospital_id and em.assigned_hospital_id == user.hospital_id
    return False


@login_required
def track(request, ref):
    em = get_object_or_404(EmergencyRequest.objects.select_related(
        "assigned_hospital", "assigned_ambulance", "patient"), reference=ref)
    if not _can_view(request.user, em):
        return HttpResponseForbidden("You cannot view this emergency.")
    return render(request, "portal/track.html", {"em": em, "hotline": settings.EMERGENCY_HOTLINE})


@login_required
def track_json(request, ref):
    em = get_object_or_404(EmergencyRequest.objects.select_related(
        "assigned_hospital", "assigned_ambulance"), reference=ref)
    if not _can_view(request.user, em):
        return JsonResponse({"error": "forbidden"}, status=403)
    a, h = em.assigned_ambulance, em.assigned_hospital
    return JsonResponse({
        "reference": em.reference, "status": em.status, "status_display": em.get_status_display(),
        "priority": em.priority, "eta_minutes": em.eta_minutes,
        "patient": [em.latitude, em.longitude],
        "ambulance": a and {"plate": a.plate_number, "type": a.get_ambulance_type_display(),
                            "pos": [a.latitude, a.longitude], "status": a.get_status_display(),
                            "simulated": a.location_is_simulated,
                            "phone": a.contact_phone or (a.driver.phone if a.driver else "")},
        "hospital": h and {"name": h.name, "pos": [h.latitude, h.longitude], "phone": h.phone},
        "recommendations": em.recommendations,
        "logs": [{"status": l.status, "note": l.note, "at": l.at.isoformat()} for l in em.logs.all()],
    })


@role_required("patient")
@require_POST
def cancel_emergency(request, ref):
    em = get_object_or_404(EmergencyRequest, reference=ref, patient=request.user)
    try:
        services.update_status(em, "cancelled", request.user, "Cancelled by patient")
        messages.success(request, "Your request was cancelled.")
    except ValueError as e:
        messages.error(request, str(e))
    return redirect("patient_home")


@login_required
def hospitals_page(request):
    return render(request, "portal/hospitals.html", {"types": EmergencyRequest.Type.choices})


@login_required
def hospitals_json(request):
    """Nearby hospitals + AI ranking for a location the user chose to share."""
    try:
        lat, lng = float(request.GET["lat"]), float(request.GET["lng"])
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise ValueError
    except (KeyError, ValueError):
        return JsonResponse({"error": "valid lat and lng are required"}, status=400)
    etype = request.GET.get("type", "other")
    nearby = []
    for h in Hospital.objects.filter(is_active=True).prefetch_related("specialties"):
        d = haversine_km(lat, lng, h.latitude, h.longitude)
        nearby.append({"id": h.id, "name": h.name, "distance_km": round(d, 2), "phone": h.phone,
                       "address": h.address, "pos": [h.latitude, h.longitude],
                       "emergency_unit": h.has_emergency_unit, "level": h.get_level_display(),
                       "services": [s.name for s in h.specialties.all()],
                       "beds": h.available_beds, "capacity_verified": h.capacity_verified})
    nearby.sort(key=lambda x: x["distance_km"])
    try:
        ranked, ai_ok = recommend_hospitals(lat, lng, etype, "high"), True
    except Exception:
        ranked, ai_ok = [], False
    return JsonResponse({"nearby": nearby[:15], "recommended": ranked, "ai_available": ai_ok})


@login_required
def notifications_page(request):
    u = request.user
    q = Q(recipient=u)
    if u.role == "hospital" and u.is_approved and u.hospital_id:
        q |= Q(hospital_id=u.hospital_id)
    qs = Notification.objects.filter(q).select_related("emergency")
    if request.GET.get("channel"):
        qs = qs.filter(channel=request.GET["channel"])
    page = Paginator(qs, 20).get_page(request.GET.get("page"))
    history = u.emergencies.order_by("-created_at")[:20] if u.role == "patient" else None  # patients: emergency history lives here
    return render(request, "portal/notifications.html", {"page": page, "unread": qs.filter(is_read=False).count(),
                                                         "history": history})


@login_required
@require_POST
def notifications_mark_read(request):
    Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
    return redirect("notifications")


# ------------------------------------------------------------- ambulance crew
@role_required("paramedic")
def paramedic_home(request):
    amb = Ambulance.objects.filter(driver=request.user).first()
    em = None
    if amb:
        em = amb.emergencies.exclude(status__in=["completed", "cancelled"]).select_related(
            "assigned_hospital", "patient").first()
    labels = dict(EmergencyRequest.Status.choices)
    nxt = sorted(services.ALLOWED_TRANSITIONS.get(em.status, [])) if em else []
    return render(request, "portal/paramedic_home.html", {
        "amb": amb, "em": em, "next_statuses": [(s, labels[s]) for s in nxt]})


@role_required("paramedic")
@require_POST
def paramedic_action(request):
    amb = Ambulance.objects.filter(driver=request.user).first()
    if not amb:
        return HttpResponseForbidden("No ambulance assigned to you")
    act = request.POST.get("action")
    if act == "status":
        em = get_object_or_404(EmergencyRequest, pk=request.POST.get("emergency"), assigned_ambulance=amb)
        try:
            services.update_status(em, request.POST["status"], request.user, "Updated by crew")
            messages.success(request, "Status updated.")
        except ValueError as e:
            messages.error(request, str(e))
    elif act == "availability" and not amb.emergencies.exclude(status__in=["completed", "cancelled"]).exists():
        amb.status = "offline" if amb.status == "available" else "available"
        amb.save(update_fields=["status"])
        messages.success(request, f"Ambulance is now {amb.get_status_display()}.")
    elif act == "location":
        try:
            amb.latitude, amb.longitude = float(request.POST["lat"]), float(request.POST["lng"])
            amb.location_is_simulated = False  # real device GPS from now on
            amb.save()
            messages.success(request, "Live GPS position sent.")
        except (KeyError, ValueError):
            messages.error(request, "Invalid position.")
    return redirect("paramedic_home")


@login_required
def request_contact(request):
    """Patient asks for a doctor / health professional (web). Same backend workflow as the mobile app:
    proactive.report -> operator siren alerts 1-3 -> AI takeover if nobody answers (emergency/alert_workflow.py)."""
    from patient_monitoring import proactive
    from patient_monitoring.models import MonitoringEvent
    if request.user.role != "patient":
        return HttpResponseForbidden("This page is for patients.")
    open_req = MonitoringEvent.objects.filter(patient=request.user, kind="contact_request",
                                              status__in=MonitoringEvent.OPEN).first()
    if request.method == "POST":
        if open_req:
            messages.info(request, "Your earlier request is still open - a health professional will contact you.")
            return redirect("request_contact")
        text = request.POST.get("reason", "").strip()[:500]
        if not text:
            messages.warning(request, "Please write in a few words what you need help with.")
            return redirect("request_contact")
        proactive.report(request.user, "contact_request", [], text, by=request.user)
        messages.success(request, "Your request was sent. An operator has been alerted and a health professional "
                                  "will contact you.")
        return redirect("request_contact")
    return render(request, "portal/request_contact.html", {"open_req": open_req})
