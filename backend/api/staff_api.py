"""REST endpoints for operator, medical-staff and admin screens of the mobile app.

Thin wrappers around the existing services - every permission rule below already applies on the web dashboard:
  * operators / admins: all emergencies, the alert feed, test-request actions, statistics;
  * medical staff (role "hospital", approved): emergencies assigned to their hospital only (EmergencyViewSet scope);
  * patients: their own emergency only (messages to the team).

GET  /api/staff/alerts/                    unhandled requests + siren schedule (operators)
POST /api/staff/alerts/accept/             {"key": "emergency-12" | "test-TST-..."}
GET  /api/staff/tests/?open=1              test requests (operators)
POST /api/staff/tests/<ref>/action/        {"action": accept|start|complete|cancel, "scenario": "normal"}
GET  /api/staff/stats/                     counts for the dashboard (operators, admins)
POST /api/emergencies/<id>/accept/         (EmergencyViewSet action)
POST /api/emergencies/<id>/message/        {"text": "..."} staff <-> patient (EmergencyViewSet action)
POST /api/auth/password-reset/             {"email"} -> e-mails a reset code (same answer whether or not it exists)
POST /api/auth/password-reset/confirm/     {"uid", "token", "new_password"}
POST /api/auth/password-change/            {"old_password", "new_password"}
"""
from django.contrib.auth import get_user_model, password_validation
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import send_mail
from django.db.models import Count, Q
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import permissions, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from patient_monitoring import test_requests as tr
from patient_monitoring.models import MedicalTestRequest as R


def _operator(request):
    if not tr.is_operator(request.user):
        raise PermissionDenied("Only emergency operators and administrators can do this.")
    return request.user


def _error(msg, code=status.HTTP_400_BAD_REQUEST):
    return Response({"detail": str(msg)}, status=code)


# ------------------------------------------------------------------ alerts
def _feed():
    from accounts.context_processors import siren_url
    from django.conf import settings
    data = tr.operator_feed()  # also advances the per-request siren counters
    from emergency import siren
    data["siren_url"] = siren_url()
    data["siren_problem"] = siren.verify().get("problem", "")
    data["alert_interval_seconds"] = int(getattr(settings, "ALERT_INTERVAL_SECONDS", 120))
    return data


@api_view(["GET"])
def alerts(request):
    _operator(request)
    return Response(_feed())


@api_view(["POST"])
def alert_accept(request):
    op = _operator(request)
    try:
        tr.accept_alert(str(request.data.get("key", "")), op)
    except tr.TestRequestError as e:
        return _error(e)
    return Response(_feed())


# ------------------------------------------------------------------ tests (operator workflow)
@api_view(["GET"])
def tests(request):
    _operator(request)
    qs = R.objects.select_related("patient")
    if request.query_params.get("open") == "1":
        qs = qs.filter(status__in=R.OPEN)
    out = []
    for r in qs[:100]:
        d = tr.as_dict(r, with_readings=False)
        d["patient"] = r.patient.get_full_name() or r.patient.username
        d["patient_code"] = r.patient_code
        out.append(d)
    return Response(out)


@api_view(["POST"])
def test_action(request, ref):
    op = _operator(request)
    req = R.objects.filter(reference=ref).first()
    if req is None:
        return _error("Test request not found.", status.HTTP_404_NOT_FOUND)
    act = request.data.get("action")
    try:
        if act == "accept":
            tr.accept(req, op)
        elif act == "start":  # starts the existing IoT session: virtual devices produce SIMULATED values
            tr.start(req, op, str(request.data.get("scenario", "normal")))
        elif act == "complete":
            tr.complete(req, op)
        elif act == "cancel":
            tr.cancel(req, op)
        else:
            return _error("Unknown action.")
    except tr.TestRequestError as e:
        return _error(e)
    req.refresh_from_db()
    return Response(tr.as_dict(req))


# ------------------------------------------------------------------ statistics
@api_view(["GET"])
def stats(request):
    u = request.user
    if not (u.is_superuser or u.role in ("operator", "admin")):
        raise PermissionDenied()
    from emergency.models import EmergencyRequest
    from notifications.models import Notification
    from patient_monitoring.models import MedicalDevice
    User = get_user_model()
    em = EmergencyRequest.objects
    return Response({
        "emergencies_by_status": dict(em.values_list("status").annotate(n=Count("id"))),
        "unaccepted_open": em.filter(accepted_at__isnull=True).exclude(status__in=["completed", "cancelled"]).count(),
        "escalated_open": em.filter(escalated_at__isnull=False).exclude(status__in=["completed", "cancelled"]).count(),
        "tests_by_status": dict(R.objects.values_list("status").annotate(n=Count("id"))),
        "users_by_role": dict(User.objects.values_list("role").annotate(n=Count("id"))),
        "staff_awaiting_approval": User.objects.filter(role="hospital", is_approved=False).count(),
        "devices": {"real": MedicalDevice.objects.filter(is_simulated=False).count(),
                    "simulated": MedicalDevice.objects.filter(is_simulated=True).count()},
        "notifications_failed": Notification.objects.filter(delivery_status="failed").count(),
        "admin_url": "/admin/",
    })


# ------------------------------------------------------------------ password
@api_view(["POST"])
@permission_classes([permissions.AllowAny])
def password_reset(request):
    email = str(request.data.get("email", "")).strip()
    User = get_user_model()
    for user in User.objects.filter(email__iexact=email, is_active=True)[:1] if email else []:
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        send_mail("Password reset - Smart Emergency",
                  f"Use this code in the app to set a new password.\n\nUser ID code: {uid}\nReset code: {token}\n\n"
                  "If you did not ask for this, ignore this e-mail.", None, [user.email])
    # same answer either way, so the endpoint does not reveal which e-mails have accounts
    return Response({"detail": "If an account uses this e-mail, a reset code has been sent."})


@api_view(["POST"])
@permission_classes([permissions.AllowAny])
def password_reset_confirm(request):
    User = get_user_model()
    try:
        user = User.objects.get(pk=force_str(urlsafe_base64_decode(str(request.data.get("uid", "")))))
    except (User.DoesNotExist, ValueError, TypeError, OverflowError):
        return _error("This reset code is not valid.")
    if not default_token_generator.check_token(user, str(request.data.get("token", ""))):
        return _error("This reset code is not valid or has expired.")
    return _set_password(user, request.data.get("new_password", ""))


@api_view(["POST"])
def password_change(request):
    if not request.user.check_password(str(request.data.get("old_password", ""))):
        return _error("Your current password is not correct.")
    return _set_password(request.user, request.data.get("new_password", ""))


def _set_password(user, new):
    try:
        password_validation.validate_password(str(new), user)
    except DjangoValidationError as e:
        return _error(" ".join(e.messages))
    user.set_password(str(new))
    user.save(update_fields=["password"])
    return Response({"detail": "Password changed. Please log in with your new password."})


__all__ = ["Q"]
