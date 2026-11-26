"""Values every page needs: unread notification count and the emergency hotline."""
from django.conf import settings
from django.db.models import Q


def navigation(request):
    ctx = {"hotline": getattr(settings, "EMERGENCY_HOTLINE", "912"), "unread_count": 0}
    u = getattr(request, "user", None)
    if u is not None and u.is_authenticated:
        from notifications.models import Notification
        q = Q(recipient=u)
        if u.hospital_id:
            q |= Q(hospital_id=u.hospital_id, recipient=None)
        ctx["unread_count"] = Notification.objects.filter(q, channel="in_app", is_read=False).count()
        if u.is_superuser or u.role in ("operator", "admin"):
            from patient_monitoring.models import MedicalTestRequest
            ctx["pending_tests"] = MedicalTestRequest.objects.filter(status="pending").count()
            from emergency import siren
            ctx["siren_url"] = siren_url()
            ctx["siren_problem"] = siren.verify().get("problem", "")
    return ctx


def siren_url():
    """URL of the VERIFIED siren file, or "" (the dashboard then shows a visible error - never crash the page)."""
    from emergency import siren
    return siren.url()
