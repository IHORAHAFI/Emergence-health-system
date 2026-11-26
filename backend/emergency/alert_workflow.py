"""One alert workflow for EVERY patient service request (web or mobile - both use the same Django APIs).

Per request (its own counter and timestamps in the database, never a shared or browser timer):

    t0            request saved -> ALERT 1 (siren on every operator dashboard)
    t0 + 1*I      still unhandled -> ALERT 2
    t0 + 2*I      still unhandled -> ALERT 3
    t0 + 3*I      still unhandled -> AI TAKEOVER (once) -> patient answered, operators + staff notified
                  -> the request STAYS OPEN until an authorised human accepts responsibility
    I = ALERT_INTERVAL_SECONDS (120 in production; set e.g. 10 for development)

Requests covered (existing models, nothing duplicated):
    emergency   EmergencyRequest   unhandled = open and accepted_at is empty   (auto-dispatch is NOT handling)
    test        MedicalTestRequest unhandled = open and no operator assigned   (accept = an operator takes it)
    service     MonitoringEvent    unhandled = contact / symptom / caregiver request, open, not accepted

Reliability:
    * every step is a conditional UPDATE (alert_count=n -> n+1, takeover marker NULL -> now), so several
      operators, several server processes and the background scheduler can all call tick() at the same time
      without duplicate alerts, duplicate AI takeovers or duplicate tests;
    * nothing lives in memory: after a server restart the next tick continues from the database
      (late alerts are sent once, not replayed in a burst);
    * tick() runs from the background scheduler (emergency/scheduler.py), every operator poll, the mobile staff
      API and `python manage.py run_alert_monitor`.
Severe emergencies are escalated to humans IMMEDIATELY when created (escalation.start) - they do not wait.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.db.models import F
from django.utils import timezone

log = logging.getLogger(__name__)
MAX_ALERTS = 3


def interval():
    return timedelta(seconds=int(getattr(settings, "ALERT_INTERVAL_SECONDS", 120)))


# ------------------------------------------------------------------ the three kinds of request
class Kind:
    key = ""
    takeover_field = "ai_takeover_at"

    def model(self):
        raise NotImplementedError

    def unhandled(self):
        raise NotImplementedError

    def takeover(self, obj):
        raise NotImplementedError

    def emergency_of(self, obj):
        return None


class EmergencyKind(Kind):
    key = "emergency"
    takeover_field = "escalated_at"  # the existing AI escalation (emergency.escalation) is the takeover

    def model(self):
        from .models import EmergencyRequest
        return EmergencyRequest

    def unhandled(self):
        return self.model().objects.filter(accepted_at__isnull=True).exclude(status__in=["completed", "cancelled"])

    def takeover(self, em):
        from . import escalation
        return escalation.escalate(em, "no_operator") is not None

    def emergency_of(self, em):
        return em


class TestKind(Kind):
    key = "test"

    def model(self):
        from patient_monitoring.models import MedicalTestRequest
        return MedicalTestRequest

    def unhandled(self):
        R = self.model()
        return R.objects.filter(status__in=R.OPEN, assigned_operator__isnull=True)

    def takeover(self, req):
        from . import ai_takeover
        return ai_takeover.take_over_test(req)

    def emergency_of(self, req):
        return req.emergency


class ServiceKind(Kind):
    key = "service"

    def model(self):
        from patient_monitoring.models import MonitoringEvent
        return MonitoringEvent

    def unhandled(self):
        E = self.model()
        # an event already escalated into an emergency rings through that emergency instead (no double siren)
        return E.objects.filter(kind__in=E.SERVICE_KINDS, status__in=E.OPEN, accepted_at__isnull=True).exclude(
            status=E.Status.ESCALATED)

    def takeover(self, ev):
        from . import ai_takeover
        return ai_takeover.take_over_service(ev)

    def emergency_of(self, ev):
        return ev.emergency


KINDS = [EmergencyKind(), TestKind(), ServiceKind()]
BY_KEY = {k.key: k for k in KINDS}


def kind_of(obj):
    for k in KINDS:
        if isinstance(obj, k.model()):
            return k
    raise ValueError(f"not a service request: {obj!r}")


# ------------------------------------------------------------------ steps
def _audit(kind, obj, action, detail=""):
    from patient_monitoring.services import audit
    try:
        audit(None, kind.emergency_of(obj), action, f"{kind.key} {getattr(obj, 'reference', obj.pk)} {detail}".strip())
    except Exception:
        log.exception("audit failed")


def _nudge(kind, obj, event):
    from patient_monitoring.test_requests import push
    push("operators", event, None, reference=getattr(obj, "reference", None) or f"{kind.key}-{obj.pk}")


def start(obj, now=None):
    """ALERT 1 - called right after a request is saved (web or mobile). Idempotent."""
    kind = kind_of(obj)
    now = now or timezone.now()
    if kind.model().objects.filter(pk=obj.pk, alert_count=0).update(alert_count=1, last_alert_at=now):
        obj.alert_count, obj.last_alert_at = 1, now
        _audit(kind, obj, "operator_alert_1")
        _nudge(kind, obj, "new_request")
        return True
    return False


def tick(now=None):
    """Advance every unhandled request's own schedule. Returns the number of steps taken."""
    now = now or timezone.now()
    due = now - interval()
    steps = 0
    for kind in KINDS:
        M = kind.model()
        # requests saved before the schedule existed, or whose start was missed: alert 1 now
        for obj in kind.unhandled().filter(alert_count=0):
            steps += start(obj, now)
        # alerts 2 and 3
        for obj in kind.unhandled().filter(alert_count__gte=1, alert_count__lt=MAX_ALERTS, last_alert_at__lte=due):
            if M.objects.filter(pk=obj.pk, alert_count=obj.alert_count).update(
                    alert_count=F("alert_count") + 1, last_alert_at=now):
                steps += 1
                _audit(kind, obj, f"operator_alert_{obj.alert_count + 1}")
                _nudge(kind, obj, "alert")
        # AI takeover one interval after alert 3 - exactly once
        for obj in kind.unhandled().filter(alert_count__gte=MAX_ALERTS, last_alert_at__lte=due,
                                           **{f"{kind.takeover_field}__isnull": True}):
            try:
                if kind.takeover(obj):
                    steps += 1
                    _audit(kind, obj, "ai_takeover")
                    _nudge(kind, obj, "ai_takeover")
            except Exception:
                log.exception("AI takeover failed for %s %s", kind.key, obj.pk)
    return steps


def schedule(obj, now=None):
    """What the dashboards show: alert number, when the next alert / the takeover is due, takeover state."""
    kind = kind_of(obj)
    now = now or timezone.now()
    taken = getattr(obj, kind.takeover_field) is not None
    nxt = (obj.last_alert_at + interval()) if obj.last_alert_at else None
    stage = ("ai_takeover" if taken else "takeover_due" if obj.alert_count >= MAX_ALERTS else "alerting")
    return {
        "alert_count": obj.alert_count, "max_alerts": MAX_ALERTS, "stage": stage,
        "next_alert_at": nxt.isoformat() if nxt and stage == "alerting" else None,
        "takeover_at": nxt.isoformat() if nxt and stage == "takeover_due" else None,
        "ai_takeover": taken, "interval_seconds": int(interval().total_seconds()),
    }
