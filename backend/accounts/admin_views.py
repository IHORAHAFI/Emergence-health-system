"""Administration pages: users, patients and activity log."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from emergency.models import StatusLog
from emergency.web_views import dispatch_required
from notifications.models import Notification
from patient_monitoring.models import AccessAuditLog
from .models import User


def admin_required(view):
    @login_required
    def wrapper(request, *a, **kw):
        if not request.user.is_admin_role:
            return HttpResponseForbidden("Only administrators can manage user accounts.")
        return view(request, *a, **kw)
    return wrapper


def _params(request):
    p = request.GET.copy()
    p.pop("page", None)
    return p.urlencode()


@admin_required
def user_list(request):
    qs = User.objects.select_related("hospital").order_by("-date_joined")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q)
                       | Q(email__icontains=q) | Q(phone__icontains=q))
    if request.GET.get("role"):
        qs = qs.filter(role=request.GET["role"])
    state = request.GET.get("state")
    if state == "inactive":
        qs = qs.filter(is_active=False)
    elif state == "pending":
        qs = qs.filter(is_approved=False, is_active=True)
    counts = {r["role"]: r["n"] for r in User.objects.values("role").annotate(n=Count("id"))}
    return render(request, "admin_panel/users.html", {
        "page": Paginator(qs, 20).get_page(request.GET.get("page")), "q": q, "params": _params(request),
        "roles": User.Role.choices, "role_counts": [(v, l, counts.get(v, 0)) for v, l in User.Role.choices]})


@admin_required
@require_POST
def user_action(request, pk):
    u = get_object_or_404(User, pk=pk)
    act = request.POST.get("action")
    if u == request.user and act in ("deactivate", "role"):
        messages.error(request, "You cannot deactivate yourself or change your own role.")
    elif u.is_superuser and not request.user.is_superuser:
        messages.error(request, "Only a superuser can change a superuser account.")
    elif act == "activate":
        u.is_active = True
        u.save(update_fields=["is_active"])
        messages.success(request, f"{u.username} activated.")
    elif act == "deactivate":
        u.is_active = False
        u.save(update_fields=["is_active"])
        messages.info(request, f"{u.username} deactivated - they can no longer log in.")
    elif act == "approve":
        u.is_approved = True
        u.save(update_fields=["is_approved"])
        messages.success(request, f"{u.username} approved.")
    elif act == "role" and request.POST.get("role") in dict(User.Role.choices):
        u.role = request.POST["role"]
        u.save(update_fields=["role"])
        messages.success(request, f"{u.username} is now {u.get_role_display()}.")
    else:
        messages.error(request, "Unknown action.")
    return redirect(request.POST.get("next") or "user_list")


@dispatch_required
def patient_list(request):
    """Registered patients (admins and emergency operators only - contains personal data)."""
    qs = User.objects.filter(role="patient").exclude(username="sim_patient").select_related(
        "patient_profile").annotate(n_emergencies=Count("emergencies")).order_by("last_name", "username")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q)
                       | Q(phone__icontains=q))
    return render(request, "admin_panel/patients.html", {
        "page": Paginator(qs, 20).get_page(request.GET.get("page")), "q": q, "params": _params(request)})


@dispatch_required
def patient_detail(request, pk):
    p = get_object_or_404(User.objects.select_related("patient_profile"), pk=pk, role="patient")
    return render(request, "admin_panel/patient_detail.html", {
        "p": p, "profile": getattr(p, "patient_profile", None),
        "emergencies": p.emergencies.select_related("assigned_hospital", "assigned_ambulance")[:30]})


@dispatch_required
def activity_log(request):
    tab = request.GET.get("tab", "emergency")
    if tab == "access":
        qs = AccessAuditLog.objects.select_related("user", "emergency")
    elif tab == "notifications":
        qs = Notification.objects.select_related("recipient", "hospital", "emergency")
        if request.GET.get("status"):
            qs = qs.filter(delivery_status=request.GET["status"])
    else:
        tab = "emergency"
        qs = StatusLog.objects.select_related("emergency", "by").order_by("-at")
    return render(request, "admin_panel/activity.html", {
        "tab": tab, "page": Paginator(qs, 25).get_page(request.GET.get("page")), "params": _params(request)})
