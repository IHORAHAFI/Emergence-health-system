from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from emergency.models import EmergencyRequest
from . import clinical, services
from .models import AccessAuditLog, VitalSignReading


def _em(ref):
    return get_object_or_404(EmergencyRequest.objects.select_related(
        "patient", "assigned_hospital", "assigned_ambulance"), reference=ref)


@login_required
def measure(request, ref):
    """Patient / crew page: connect Bluetooth devices, enter manually, or simulate."""
    em = _em(ref)
    if not services.can_submit(request.user, em):
        return HttpResponseForbidden("You cannot send measurements for this emergency.")
    return render(request, "monitoring/measure.html", {
        "em": em, "consent": services.has_consent(em) or request.user.role == "paramedic",
        "consent_text": services.CONSENT_TEXT, "readings": em.vitals.select_related("device")[:10],
        "readings_json": [r.as_dict() for r in em.vitals.select_related("device").prefetch_related("reviews")[:40]],
        "consciousness": VitalSignReading.Consciousness.choices,
        "vital_cards": [("temperature_c", "Temperature"), ("heart_rate_bpm", "Heart rate"), ("spo2_pct", "SpO₂"),
                        ("systolic_mmhg", "Systolic BP"), ("diastolic_mmhg", "Diastolic BP"),
                        ("respiratory_rate", "Respiratory rate")],
        "ble_kinds": [("thermometer", "bi-thermometer-half", "Thermometer"),
                      ("pulse_oximeter", "bi-lungs", "Pulse oximeter"),
                      ("blood_pressure", "bi-activity", "Blood pressure monitor"),
                      ("heart_rate", "bi-heart-pulse", "Heart-rate sensor")]})


@login_required
@require_POST
def consent(request, ref):
    em = _em(ref)
    if request.user.role != "patient" or em.patient_id != request.user.id:
        return HttpResponseForbidden()
    if request.POST.get("decision") == "revoke":
        services.revoke_consent(em, request.user)
    else:
        services.give_consent(em, request.user)
    return redirect("vitals_measure", ref=ref)


def _staff_ok(user):
    if user.is_superuser or user.role in ("admin", "operator"):
        return True
    return user.role == "hospital" and user.is_approved


@login_required
def board(request):
    """Live board of active emergencies with their latest vital signs."""
    if not _staff_ok(request.user):
        return HttpResponseForbidden("Authorized medical staff only.")
    qs = EmergencyRequest.objects.exclude(status__in=["completed", "cancelled"]).select_related(
        "patient", "assigned_hospital", "assigned_ambulance")
    if request.user.role == "hospital":
        qs = qs.filter(assigned_hospital_id=request.user.hospital_id)
    rows = []
    for em in qs:
        latest = em.vitals.first()
        rows.append((em, latest))
    order = {r: i for i, r in enumerate(reversed(clinical.RISK_ORDER))}
    rows.sort(key=lambda t: (order.get(t[1].risk if t[1] else "none", 9), -t[0].id))
    services.audit(request.user, None, "vitals_board_viewed", request=request)
    return render(request, "monitoring/board.html", {"rows": rows, "labels": clinical.RISK_LABEL})


def _alerts_for(user):
    from .models import EmergencyAlert
    qs = EmergencyAlert.objects.select_related("emergency", "test_request__patient", "device", "acknowledged_by")
    if user.role == "hospital" and not user.is_superuser:
        qs = qs.filter(emergency__assigned_hospital_id=user.hospital_id)
    return qs


@login_required
def alerts(request):
    """Open vital-sign alerts + the configurable thresholds that produce them."""
    from django.contrib import messages
    from django.core.paginator import Paginator
    from .models import AlertThreshold
    if not _staff_ok(request.user):
        return HttpResponseForbidden("Authorized medical staff only.")
    is_admin = request.user.is_superuser or request.user.role == "admin"
    if request.method == "POST":
        if request.POST.get("action") == "ack":
            a = get_object_or_404(_alerts_for(request.user), pk=request.POST.get("alert"))
            services.acknowledge_alert(a, request.user)
            messages.success(request, "Alert acknowledged.")
        elif request.POST.get("action") == "thresholds" and is_admin:
            try:
                for t in AlertThreshold.objects.all():
                    low, high = request.POST.get(f"low_{t.id}", ""), request.POST.get(f"high_{t.id}", "")
                    t.low = float(low) if low.strip() else None
                    t.high = float(high) if high.strip() else None
                    if t.low is not None and t.high is not None and t.low >= t.high:
                        raise ValueError(f"{t.get_parameter_display()} ({t.age_group}): low must be below high.")
                    t.enabled = request.POST.get(f"on_{t.id}") == "on"
                    t.save()
                services.audit(request.user, None, "thresholds_changed")
                messages.success(request, "Thresholds saved.")
            except ValueError as e:
                messages.error(request, str(e) if "low must" in str(e) else "Thresholds must be numbers.")
        return redirect("vitals_alerts")
    qs = _alerts_for(request.user)
    show = request.GET.get("show", "open")
    if show == "open":
        qs = qs.filter(status="open")
    return render(request, "monitoring/alerts.html", {
        "page": Paginator(qs, 25).get_page(request.GET.get("page")), "show": show, "params": f"show={show}",
        "thresholds": AlertThreshold.objects.all(), "is_admin": is_admin,
        "open_count": _alerts_for(request.user).filter(status="open").count()})


@login_required
def detail(request, ref):
    em = _em(ref)
    if request.user.role == "patient" or not services.can_view(request.user, em):
        return HttpResponseForbidden("Authorized medical staff only.")
    services.audit(request.user, em, "vitals_viewed", request=request)
    readings = list(em.vitals.select_related("device").prefetch_related("reviews__reviewer")[:100])
    return render(request, "monitoring/detail.html", {
        "em": em, "readings": readings, "latest": readings[0] if readings else None,
        "can_review": services.can_review(request.user, em), "labels": clinical.RISK_LABEL,
        "risks": clinical.RISK_ORDER[1:], "consent": services.has_consent(em),
        "audit": AccessAuditLog.objects.filter(emergency=em).select_related("user")[:15]
        if request.user.is_superuser or request.user.role in ("admin", "operator") else None})
