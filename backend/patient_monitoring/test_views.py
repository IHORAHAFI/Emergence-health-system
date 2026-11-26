"""Pages for the patient-initiated medical test workflow."""
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import services, simulation, test_requests as svc
from .models import MedicalTestRequest as R


def _own_request(user, ref):
    """A patient can only ever load their own request; anything else is a 404 (existence not revealed)."""
    req = get_object_or_404(R.objects.select_related("patient", "emergency", "assigned_operator"), reference=ref)
    if not svc.can_view(user, req):
        raise Http404
    return req


# ------------------------------------------------------------------ patient
@login_required
def patient_tests(request):
    """Medical Tests page: choose tests, request them, follow open requests and history."""
    if request.user.role != "patient":
        return HttpResponseForbidden("This page is for patients.")
    if request.method == "POST":
        tests = list(R.TESTS) if request.POST.get("test") == "all" else request.POST.getlist("test")
        try:
            req = svc.create_request(request.user, tests, request.POST.get("note", ""))
            messages.success(request, f"Test request {req.reference} sent ({req.test_names}). "
                                      f"An operator has been alerted - you can follow it below.")
            return redirect("patient_test_detail", ref=req.reference)
        except svc.TestRequestError as e:
            messages.warning(request, str(e))
        return redirect("patient_tests")
    mine = R.objects.filter(patient=request.user)
    open_status = {t: r.status for r in mine.filter(status__in=R.OPEN) for t in r.tests}
    open_tests = set(open_status)
    cards = [(code, label, icon, unit, code in open_tests, open_status.get(code))
             for code, (label, icon, unit, _f, _k) in R.TESTS.items()]
    return render(request, "monitoring/patient_tests.html", {
        "cards": cards, "all_open": bool(open_tests),
        "open_requests": mine.filter(status__in=R.OPEN),
        "history": Paginator(mine.exclude(status__in=R.OPEN), 10).get_page(request.GET.get("page")),
        "profile": getattr(request.user, "patient_profile", None)})


@login_required
def patient_test_detail(request, ref):
    req = _own_request(request.user, ref)
    if request.user.role != "patient" and not svc.is_operator(request.user):
        raise Http404
    return render(request, "monitoring/patient_test_results.html", {"req": req, "data": svc.as_dict(req)})


@login_required
def test_data(request, ref):
    """JSON for live result pages (polled every few seconds and refreshed on WebSocket nudges)."""
    return JsonResponse(svc.as_dict(_own_request(request.user, ref)))


@login_required
@require_POST
def patient_test_cancel(request, ref):
    req = _own_request(request.user, ref)
    try:
        svc.cancel(req, request.user)
        messages.info(request, f"Request {req.reference} cancelled.")
    except svc.TestRequestError as e:
        messages.error(request, str(e))
    if request.user.role == "patient":
        return redirect("patient_test_detail", ref=ref)
    return redirect("test_request_detail", ref=ref)


# ------------------------------------------------------------------ operator
def operator_required(view):
    @login_required
    def wrapper(request, *a, **kw):
        if not svc.is_operator(request.user):
            return HttpResponseForbidden("Patient test requests are handled by emergency operators.")
        return view(request, *a, **kw)
    return wrapper


@operator_required
def test_request_list(request):
    qs = R.objects.select_related("patient", "emergency", "assigned_operator")
    status = request.GET.get("status", "open")
    if status == "open":
        qs = qs.filter(status__in=R.OPEN)
    elif status in dict(R.Status.choices):
        qs = qs.filter(status=status)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(reference__icontains=q) | Q(patient__username__icontains=q)
                       | Q(patient__first_name__icontains=q) | Q(patient__last_name__icontains=q))
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "monitoring/test_requests.html", {
        "page": Paginator(qs, 20).get_page(request.GET.get("page")), "status": status, "q": q,
        "params": params.urlencode(), "statuses": R.Status.choices,
        "pending_count": R.objects.filter(status="pending").count()})


@operator_required
def test_request_detail(request, ref):
    req = get_object_or_404(R.objects.select_related("patient", "emergency", "assigned_operator"), reference=ref)
    services.audit(request.user, req.emergency, "test_request_viewed", req.reference, request)
    return render(request, "monitoring/test_request_detail.html", {
        "req": req, "p": req.patient, "profile": getattr(req.patient, "patient_profile", None),
        "data": svc.as_dict(req, limit=30), "scenarios": {k: v for k, v in simulation.SCENARIOS.items() if k != "custom"},
        "previous": R.objects.filter(patient=req.patient).exclude(pk=req.pk).order_by("-requested_at")[:10],
        "session": getattr(req, "session", None)})


@operator_required
@require_POST
def test_request_action(request, ref):
    req = get_object_or_404(R, reference=ref)
    action = request.POST.get("action")
    try:
        if action == "accept":
            svc.accept(req, request.user)
            messages.success(request, f"{req.reference} accepted - the patient has been told.")
        elif action == "claim":  # take over a test the AI started after unanswered alerts
            svc.accept_alert(f"test-{req.reference}", request.user)
            messages.success(request, f"You are now responsible for {req.reference}.")
        elif action == "start":
            session = svc.start(req, request.user, request.POST.get("scenario", "normal"),
                                float(request.POST.get("speed") or 1))
            return redirect("sim_panel", pk=session.id)
        elif action in ("pause", "resume"):
            session = getattr(req, "session", None)
            if session is None or req.status != "testing":
                raise svc.TestRequestError("This test is not running.")
            simulation.control(session, action, request.user)
            messages.success(request, f"{req.reference}: test {'paused' if action == 'pause' else 'resumed'}.")
        elif action == "complete":
            svc.complete(req, request.user)
            messages.success(request, f"{req.reference} completed - results sent to the patient.")
        elif action == "cancel":
            svc.cancel(req, request.user)
            messages.info(request, f"{req.reference} cancelled.")
        else:
            raise svc.TestRequestError("Unknown action.")
    except (svc.TestRequestError, ValueError) as e:
        messages.error(request, str(e))
    nxt = request.POST.get("next", "")
    if nxt.startswith("/") and not nxt.startswith("//"):  # only local pages
        return redirect(nxt)
    return redirect("test_request_detail", ref=ref)


@operator_required
def operator_feed(request):
    """Polled by every operator page (and refreshed instantly by WebSocket nudges)."""
    return JsonResponse(svc.operator_feed())


@operator_required
@require_POST
def alert_accept(request):
    """Accept button on the emergency alert panel: {"key": "emergency-12"} -> updated feed."""
    try:
        key = json.loads(request.body or b"{}").get("key", "")
    except json.JSONDecodeError:
        key = request.POST.get("key", "")
    try:
        svc.accept_alert(str(key), request.user)
    except svc.TestRequestError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    return JsonResponse({"ok": True, **svc.operator_feed()})


@operator_required
def service_request_detail(request, pk):
    """Operator page for a patient's request for a professional / symptom or caregiver report."""
    from .models import MonitoringEvent
    ev = get_object_or_404(MonitoringEvent.objects.select_related("patient", "emergency", "accepted_by"), pk=pk)
    services.audit(request.user, ev.emergency, "service_request_viewed", f"event {ev.pk}", request)  # viewing != accepting
    return render(request, "monitoring/service_request_detail.html", {"ev": ev, "p": ev.patient})


@operator_required
@require_POST
def service_request_action(request, pk):
    from django.utils import timezone
    from . import proactive
    from .models import MonitoringEvent
    ev = get_object_or_404(MonitoringEvent, pk=pk)
    action = request.POST.get("action")
    try:
        if action == "accept":
            svc.accept_alert(f"service-{ev.pk}", request.user)
            messages.success(request, "Accepted - the patient has been told.")
        elif action == "close":
            reason = request.POST.get("reason", "").strip()[:200]
            if not reason:
                raise svc.TestRequestError("Write why the request is closed (e.g. 'called the patient, advice given').")
            if not ev.accepted_at:
                svc.accept_alert(f"service-{ev.pk}", request.user)
                ev.refresh_from_db()
            proactive._log(ev, "operator", f"Closed by {request.user.get_full_name() or request.user.username}: {reason}")
            ev.status, ev.closed_at = MonitoringEvent.Status.CLOSED, timezone.now()
            ev.save()
            services.audit(request.user, ev.emergency, "service_request_closed", f"event {ev.pk}: {reason}")
            messages.success(request, "Closed.")
        else:
            raise svc.TestRequestError("Unknown action.")
    except svc.TestRequestError as e:
        messages.error(request, str(e))
    return redirect("service_request_detail", pk=ev.pk)
