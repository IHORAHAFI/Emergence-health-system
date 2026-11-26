"""Web dashboard for administrators, emergency operators and healthcare professionals."""
import random
from datetime import timedelta

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Avg, Count, F, Q
from django.db.models.functions import TruncDate
from django.utils import timezone
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import User
from ambulances.models import Ambulance, AmbulanceAssignment
from hospitals.models import Hospital
from notifications.models import Notification
from . import services
from .models import EmergencyRequest, StatusLog

STAFF = ("admin", "operator", "hospital")
DISPATCH = ("admin", "operator")
ACTIVE_EXCLUDE = ["completed", "cancelled"]


def staff_required(view, roles=STAFF):
    @login_required
    def wrapper(request, *a, **kw):
        u = request.user
        if not u.is_superuser:
            if u.role not in roles:
                return HttpResponseForbidden("You do not have access to this page.")
            if u.role == "hospital" and not u.is_approved:
                return HttpResponseForbidden("Your healthcare professional account is waiting for "
                                             "administrator approval.")
        return view(request, *a, **kw)
    return wrapper


def dispatch_required(view):
    return staff_required(view, DISPATCH)


def _is_dispatcher(u):
    return u.is_superuser or u.role in DISPATCH


def _scope(user):
    qs = EmergencyRequest.objects.select_related("patient", "assigned_hospital", "assigned_ambulance")
    if user.role == "hospital" and not user.is_superuser:
        qs = qs.filter(assigned_hospital_id=user.hospital_id) if user.hospital_id else qs.none()
    return qs


@staff_required
def dashboard(request):
    qs = _scope(request.user)
    active = qs.exclude(status__in=ACTIVE_EXCLUDE)
    u = request.user
    if u.hospital_id:
        notes = Notification.objects.filter(Q(hospital_id=u.hospital_id) | Q(recipient=u), channel="in_app")[:8]
    else:
        notes = u.notifications.filter(channel="in_app")[:8]
    amb = Ambulance.objects.all()
    stats = {
        "total": qs.count(), "pending": qs.filter(status="pending").count(),
        "active": active.count(), "critical": active.filter(priority="critical").count(),
        "completed": qs.filter(status="completed").count(),
        "amb_free": amb.filter(status="available").count(),
        "amb_busy": amb.filter(status__in=["assigned", "dispatched", "on_the_way", "arrived", "busy"]).count(),
        "hospitals": Hospital.objects.filter(is_active=True).count(),
        "patients": User.objects.filter(role="patient").count(),
        "professionals": User.objects.filter(role="hospital").count(),
        "pending_approvals": User.objects.filter(role="hospital", is_approved=False).count(),
    }
    # Chart data: all from the database, never sample values.
    status_labels = dict(EmergencyRequest.Status.choices)
    by_status = {r["status"]: r["n"] for r in qs.values("status").annotate(n=Count("id"))}
    by_prio = {r["priority"]: r["n"] for r in active.values("priority").annotate(n=Count("id"))}
    today = timezone.localdate()
    days = [today - timedelta(days=i) for i in range(13, -1, -1)]
    per_day = {r["d"]: r["n"] for r in qs.filter(created_at__date__gte=days[0])
               .annotate(d=TruncDate("created_at")).values("d").annotate(n=Count("id"))}
    def per_day_of(model_qs, field):
        counts = {r["d"]: r["n"] for r in model_qs.filter(**{f"{field}__date__gte": days[0]})
                  .annotate(d=TruncDate(field)).values("d").annotate(n=Count("id"))}
        return [counts.get(d, 0) for d in days]
    from patient_monitoring.models import EmergencyAlert, MedicalTestRequest
    charts = {
        "status": {"labels": [status_labels[s] for s in by_status], "data": list(by_status.values())},
        "priority": {"labels": ["Critical", "High", "Medium", "Low"],
                     "data": [by_prio.get(p, 0) for p in ("critical", "high", "medium", "low")]},
        "trend": {"labels": [d.strftime("%d %b") for d in days], "data": [per_day.get(d, 0) for d in days]},
        "spark": {"emergencies": [per_day.get(d, 0) for d in days],
                  "tests": per_day_of(MedicalTestRequest.objects.all(), "requested_at"),
                  "alerts": per_day_of(EmergencyAlert.objects.filter(emergency__in=qs), "created_at")},
    }
    attention = active.filter(Q(priority__in=["critical", "high"]) | Q(status="pending")).order_by(
        "status", "-created_at")[:8]
    activity = StatusLog.objects.select_related("emergency", "by").filter(emergency__in=qs).order_by("-at")[:10]
    from patient_monitoring.models import EmergencyAlert
    vital_alerts = EmergencyAlert.objects.filter(status="open", emergency__in=qs).select_related("emergency", "device")
    return render(request, "dashboard/index.html", {
        "active": active, "recent": qs.filter(status__in=ACTIVE_EXCLUDE)[:8],
        "hospitals": Hospital.objects.filter(is_active=True), "ambulances": amb,
        "notifications": notes, "stats": stats, "counts": stats, "charts": charts,
        "attention": attention, "activity": activity,
        "vital_alerts": vital_alerts[:6], "vital_alert_count": vital_alerts.count(),
        **(_test_overview() if _is_dispatcher(u) else {}),
    })


def _test_overview():
    """Patient test requests for the operator dashboard."""
    from patient_monitoring.models import MedicalTestRequest as R
    base = R.objects.select_related("patient", "emergency")
    today = timezone.localdate()
    return {"tests_pending": base.filter(status="pending").order_by("requested_at")[:8],
            "tests_running": base.filter(status__in=["accepted", "testing"]).select_related("session")[:8],
            "tests_done_today": base.filter(status="completed", completed_at__date=today).count(),
            "show_tests": True}


@staff_required
def emergencies_list(request):
    """Searchable, filterable, paginated emergency history."""
    qs = _scope(request.user)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(reference__icontains=q) | Q(patient__username__icontains=q)
                       | Q(patient__first_name__icontains=q) | Q(patient__last_name__icontains=q)
                       | Q(description__icontains=q) | Q(location_description__icontains=q))
    for f in ("status", "priority", "emergency_type"):
        if request.GET.get(f):
            qs = qs.filter(**{f: request.GET[f]})
    page = Paginator(qs, 15).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "dashboard/emergencies.html", {
        "page": page, "q": q, "params": params.urlencode(), "statuses": EmergencyRequest.Status.choices,
        "priorities": EmergencyRequest.PRIORITY_CHOICES, "types": EmergencyRequest.Type.choices})


@staff_required
def emergency_detail(request, pk):
    em = get_object_or_404(_scope(request.user), pk=pk)
    nxt = sorted(services.ALLOWED_TRANSITIONS.get(em.status, []))
    if not _is_dispatcher(request.user):
        nxt = [s for s in nxt if s in ("arrived", "completed")]
    return render(request, "dashboard/detail.html", {
        "em": em, "logs": em.logs.select_related("by"), "hospitals": Hospital.objects.filter(is_active=True),
        "next_statuses": [(s, dict(EmergencyRequest.Status.choices)[s]) for s in nxt],
        "priorities": EmergencyRequest.PRIORITY_CHOICES, "can_dispatch": _is_dispatcher(request.user),
        "assignments": em.assignments.select_related("ambulance", "assigned_by"),
        "notifications": em.notifications.select_related("recipient", "hospital")[:30],
        "available_ambulances": Ambulance.objects.filter(status="available").count(),
    })


@staff_required
@require_POST
def emergency_action(request, pk):
    em = get_object_or_404(_scope(request.user), pk=pk)
    act = request.POST.get("action")
    dispatcher = _is_dispatcher(request.user)
    try:
        if act == "status":
            new = request.POST["status"]
            if not dispatcher and new not in ("arrived", "completed"):
                raise ValueError("Healthcare professionals can only confirm arrival or completion.")
            services.update_status(em, new, request.user, request.POST.get("note", ""))
        elif act == "accept" and dispatcher:
            services.accept_emergency(em, request.user)
        elif act == "priority" and dispatcher:
            from ai_recommendation.recommender import recommend_hospitals
            em.priority = request.POST["priority"]
            em.priority_overridden = True
            em.triage_method = "operator"
            em.recommendations = recommend_hospitals(em.latitude, em.longitude, em.emergency_type, em.priority)
            em.save()
            services.log_status(em, f"Priority overridden to {em.priority}", request.user)
        elif act == "dispatch" and dispatcher:
            if em.status not in ("pending", "assigned"):
                raise ValueError("Only pending or assigned emergencies can be (re)dispatched.")
            services.dispatch(em, request.POST.get("hospital_id") or None, request.user)
            if not em.assigned_ambulance_id or em.status == "pending":
                messages.warning(request, "No ambulance is available right now - request stays pending.")
        elif act == "simulate" and dispatcher:
            left = services.simulate_step(em)
            if left is None:
                raise ValueError("Nothing to simulate: the ambulance is not moving, or it reports real GPS.")
            messages.info(request, f"SIMULATION: ambulance moved; {left:.2f} km to go.")
            return redirect("emergency_detail", pk=pk)
        else:
            raise ValueError("Action not allowed.")
        messages.success(request, "Updated.")
    except ValueError as e:
        messages.error(request, str(e))
    return redirect("emergency_detail", pk=pk)


@dispatch_required
def ambulances_page(request):
    qs = Ambulance.objects.select_related("base_hospital", "driver")
    if request.GET.get("status"):
        qs = qs.filter(status=request.GET["status"])
    rows = []
    for a in qs:
        asg = a.assignments.filter(released_at__isnull=True).select_related("emergency").first()
        rows.append((a, asg))
    return render(request, "dashboard/ambulances.html", {
        "rows": rows, "statuses": Ambulance.Status.choices,
        "history": AmbulanceAssignment.objects.select_related("ambulance", "emergency")[:15]})


@dispatch_required
@require_POST
def ambulance_action(request, pk):
    amb = get_object_or_404(Ambulance, pk=pk)
    new = request.POST.get("status")
    busy = amb.assignments.filter(released_at__isnull=True).exists()
    if new not in ("available", "offline"):
        messages.error(request, "Only Available / Out of service can be set manually; "
                                "other statuses follow the emergency workflow.")
    elif busy:
        messages.error(request, f"{amb.plate_number} is on an active emergency - complete or re-dispatch it first.")
    else:
        amb.status = new
        amb.save(update_fields=["status"])
        messages.success(request, f"{amb.plate_number} is now {amb.get_status_display()}.")
    return redirect("ambulances")


class AmbulanceForm(forms.ModelForm):
    class Meta:
        model = Ambulance
        fields = ("plate_number", "ambulance_type", "base_hospital", "driver", "contact_phone",
                  "latitude", "longitude")
        labels = {"plate_number": "Ambulance ID / plate number", "driver": "Driver / crew account",
                  "latitude": "Current latitude", "longitude": "Current longitude"}
        help_texts = {"latitude": "Usually the base hospital. Positions entered here are marked "
                                  "'not live GPS' until the crew app reports a real position."}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        taken = Ambulance.objects.exclude(pk=self.instance.pk).exclude(driver=None).values("driver_id")
        self.fields["driver"].queryset = User.objects.filter(role="paramedic", is_active=True).exclude(id__in=taken)
        self.fields["driver"].required = False
        for f in self.fields.values():
            f.widget.attrs.setdefault("class", "form-select" if isinstance(f.widget, forms.Select) else "form-control")

    def clean(self):
        c = super().clean()
        if c.get("latitude") is not None and not -90 <= c["latitude"] <= 90:
            self.add_error("latitude", "Latitude must be between -90 and 90.")
        if c.get("longitude") is not None and not -180 <= c["longitude"] <= 180:
            self.add_error("longitude", "Longitude must be between -180 and 180.")
        return c


@dispatch_required
def ambulance_edit(request, pk=None):
    """Register a new ambulance or edit an existing one (administrators and operators)."""
    amb = get_object_or_404(Ambulance, pk=pk) if pk else None
    initial = {}
    if not amb:
        h = Hospital.objects.filter(is_active=True).first()
        if h:
            initial = {"base_hospital": h, "latitude": h.latitude, "longitude": h.longitude}
    form = AmbulanceForm(request.POST or None, instance=amb, initial=initial)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        if form.has_changed() and {"latitude", "longitude"} & set(form.changed_data):
            obj.location_is_simulated = True  # typed in by staff, not reported by a GPS device
        obj.save()
        messages.success(request, f"Ambulance {obj.plate_number} {'updated' if amb else 'registered'}.")
        return redirect("ambulances")
    return render(request, "dashboard/ambulance_form.html", {"form": form, "amb": amb,
                                                             "hospitals": Hospital.objects.filter(is_active=True)})


@dispatch_required
def approvals(request):
    if request.method == "POST":
        u = get_object_or_404(User, pk=request.POST.get("user"), role="hospital")
        if request.POST.get("decision") == "approve":
            u.is_approved = True
            u.save(update_fields=["is_approved"])
            messages.success(request, f"{u.username} approved.")
        else:
            u.is_active = False
            u.save(update_fields=["is_active"])
            messages.info(request, f"{u.username} rejected and deactivated.")
        return redirect("approvals")
    return render(request, "dashboard/approvals.html", {
        "pending": User.objects.filter(role="hospital", is_approved=False, is_active=True).select_related("hospital")})


@staff_required
def hospital_capacity(request):
    if not request.user.hospital_id:
        messages.error(request, "Your account is not linked to a hospital.")
        return redirect("dashboard")
    h = request.user.hospital
    if request.method == "POST":
        try:
            h.available_beds = max(0, int(request.POST.get("available_beds", h.available_beds)))
            h.icu_available = max(0, int(request.POST.get("icu_available", h.icu_available)))
        except ValueError:
            messages.error(request, "Bed numbers must be whole numbers.")
            return redirect("hospital_capacity")
        h.has_emergency_unit = request.POST.get("has_emergency_unit") == "on"
        h.capacity_verified = True  # confirmed by the hospital's own staff
        h.capacity_updated_by = request.user
        h.save()
        messages.success(request, "Capacity updated and marked as verified by hospital staff.")
        return redirect("dashboard")
    return render(request, "dashboard/capacity.html", {"h": h})


# Kigali area bounding box for simulated emergencies
SIM_LAT, SIM_LNG = (-2.02, -1.90), (29.99, 30.17)


@dispatch_required
@require_POST
def simulate_emergency(request):
    """Create a random SIMULATED emergency to test the full workflow."""
    patient, created = User.objects.get_or_create(username="sim_patient",
                                                  defaults={"role": "patient", "first_name": "Simulated",
                                                            "last_name": "Patient", "phone": "+250780000000"})
    if created:
        patient.set_unusable_password()
        patient.save()
    t = random.choice([c[0] for c in EmergencyRequest.Type.choices])
    em = services.create_emergency(patient, {
        "emergency_type": t, "description": "Simulated emergency",
        "latitude": random.uniform(*SIM_LAT), "longitude": random.uniform(*SIM_LNG),
        "location_source": "simulated", "location_consent": True,
        "conscious": random.random() > 0.2, "breathing_difficulty": random.random() < 0.3,
        "severe_bleeding": random.random() < (0.5 if t == "trauma" else 0.1),
        "chest_pain": t == "cardiac", "pain_level": random.randint(0, 10),
        "pregnant": t == "maternity", "age": random.randint(1, 90), "is_simulated": True,
    }, allow_duplicate=True)
    messages.success(request, f"Simulated emergency {em.reference} created ({em.priority}).")
    return redirect("emergency_detail", pk=em.id)


@staff_required
def reports(request):
    qs = _scope(request.user)
    done = qs.exclude(dispatched_at=None)
    return render(request, "dashboard/reports.html", {
        "total": qs.count(),
        "by_priority": qs.values("priority").annotate(n=Count("id")).order_by("priority"),
        "by_type": qs.values("emergency_type").annotate(n=Count("id")).order_by("-n"),
        "by_status": qs.values("status").annotate(n=Count("id")),
        "avg_eta": done.aggregate(v=Avg("eta_minutes"))["v"],
        "avg_dist": done.aggregate(v=Avg("ambulance_distance_km"))["v"],
        "avg_dispatch_s": done.aggregate(v=Avg(F("dispatched_at") - F("created_at")))["v"],
        "assigned_rate": (done.count() / qs.count() * 100) if qs.count() else 0,
        "overridden": qs.filter(priority_overridden=True).count(),
        "fallback": qs.filter(triage_method="rule_fallback").count(),
    })


@staff_required
def live_locations(request):
    """Active emergencies with their location status for the operator maps (polled + WebSocket nudges)."""
    from django.http import JsonResponse
    from . import location
    now = timezone.now()
    active = _scope(request.user).exclude(status__in=ACTIVE_EXCLUDE).select_related("patient")[:200]
    return JsonResponse({"emergencies": [location.marker(e, now) for e in active],
                         "live_seconds": location.live_seconds()})
