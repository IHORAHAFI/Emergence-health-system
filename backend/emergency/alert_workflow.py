"""One alert workflow for EVERY patient service request (web or mobile - both use the same Django APIs).

Per request (its own counter and timestamps in the database, never a shared or browser timer):

    00:00  request saved                -> ALERT 1 (siren on every operator dashboard) + "request received"
    02:00  still unhandled              -> ALERT 2 + "still waiting" to the patient
    04:00  still unhandled              -> ALERT 3, and AI TAKEOVER immediately (once): the request is processed,
                                           the patient answered, operators + staff notified
           -> the request STAYS OPEN until an authorised human accepts responsibility
    interval = ALERT_INTERVAL_SECONDS (120 in production; tests move the clock instead)
    Accepting (never just viewing) cancels the remaining alerts and the takeover for THAT request only.

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


def _tell_patient(kind, obj, title, text):
    from notifications.services import notify_user
    try:
        notify_user(obj.patient, title, text, kind.emergency_of(obj))
    except Exception:
        log.exception("patient notification failed")


def _name(kind, obj):
    if kind.key == "test":
        return f"test request {obj.reference} ({obj.test_names})"
    if kind.key == "service":
        return f"request #{obj.pk} ({obj.get_kind_display().lower()})"
    return f"emergency request {obj.reference}"


def start(obj, now=None):
    """ALERT 1 - called right after a request is saved (web or mobile). Idempotent: a repeated or duplicate
    event for the same request never creates a second schedule."""
    kind = kind_of(obj)
    now = now or timezone.now()
    if kind.model().objects.filter(pk=obj.pk, alert_count=0).update(alert_count=1, last_alert_at=now):
        obj.alert_count, obj.last_alert_at = 1, now
        _audit(kind, obj, "operator_alert_1")
        _nudge(kind, obj, "new_request")
        if kind.key != "emergency":  # emergencies already send "Request received" (emergency.services)
            _tell_patient(kind, obj, "Your request has been received",
                          f"Your {_name(kind, obj)} was received. An operator has been alerted.")
        return True
    return False


def _take_over(kind, obj):
    """AI takeover, right after alert 3. A failure never hides the request: staff are told to handle it."""
    try:
        if kind.takeover(obj):
            _audit(kind, obj, "ai_takeover")
            _nudge(kind, obj, "ai_takeover")
            return True
    except Exception:
        log.exception("AI takeover failed for %s %s", kind.key, obj.pk)
        _audit(kind, obj, "ai_takeover_failed")
        try:
            from notifications.services import notify_responders
            notify_responders(f"AI TAKEOVER FAILED - {_name(kind, obj)}",
                              f"The assistant could not process {_name(kind, obj)} for "
                              f"{obj.patient.get_full_name() or obj.patient.username} after 3 unanswered alerts. "
                              "A human must handle it now.", kind.emergency_of(obj))
        except Exception:
            log.exception("could not notify staff about the failed takeover")
    return False


def cancelled(obj, operator):
    """Audit: an authorised human accepted -> remaining alerts and the AI takeover will not happen."""
    kind = kind_of(obj)
    obj.refresh_from_db()
    taken = getattr(obj, kind.takeover_field) is not None
    _audit(kind, obj, "alert_schedule_cancelled",
           f"accepted by {operator.username} after alert {obj.alert_count}"
           + ("" if taken else "; AI takeover cancelled"))
    _nudge(kind, obj, "accepted")


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
        # alerts 2 and 3 - each a conditional UPDATE, so no alert is ever counted twice
        for obj in kind.unhandled().filter(alert_count__gte=1, alert_count__lt=MAX_ALERTS, last_alert_at__lte=due):
            if M.objects.filter(pk=obj.pk, alert_count=obj.alert_count).update(
                    alert_count=F("alert_count") + 1, last_alert_at=now):
                steps += 1
                n = obj.alert_count + 1
                _audit(kind, obj, f"operator_alert_{n}")
                _nudge(kind, obj, "alert")
                if n == 2:
                    _tell_patient(kind, obj, "Your request is waiting for assistance",
                                  f"Your {_name(kind, obj)} is still waiting for a staff member. The team has been "
                                  "alerted again.")
        # AI takeover immediately after alert 3 (also catches anything missed during a restart) - exactly once
        for obj in kind.unhandled().filter(alert_count__gte=MAX_ALERTS, **{f"{kind.takeover_field}__isnull": True}):
            obj.refresh_from_db()
            if obj.alert_count >= MAX_ALERTS and getattr(obj, kind.takeover_field) is None:
                steps += _take_over(kind, obj)
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
        # alert 3 is followed at once by the AI takeover, so the next step after alert 2 is "alert 3 + takeover"
        "next_alert_at": nxt.isoformat() if nxt and stage == "alerting" else None,
        "takeover_at": (nxt.isoformat() if nxt and obj.alert_count == MAX_ALERTS - 1 and not taken else None),
        "ai_takeover": taken, "interval_seconds": int(interval().total_seconds()),
        "last_alert_at": obj.last_alert_at.isoformat() if obj.last_alert_at else None,
    }
