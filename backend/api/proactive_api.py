"""REST endpoints for the proactive / accessible assistant and caregivers.

Patient
  GET/PATCH  /api/assistant/preferences/               voice, accessibility, check-in, location settings
  POST/DELETE /api/home-monitoring/consent/             switch home monitoring on / off
  POST       /api/home-monitoring/readings/            REAL reading from the patient's own Bluetooth device
  POST       /api/monitoring/location/                 last location (only stored if the patient allowed it)
  GET        /api/monitoring/events/?open=1            my events (also advances the prompt schedule)
  POST       /api/monitoring/events/                   {"kind": possible_fall|patient_report|contact_request, ...}
  POST       /api/monitoring/events/<id>/respond/      {"answer": ok|not_ok|help, "symptoms": [], "pain", "text"}
Caregiver links
  GET        /api/caregivers/                          patient: my caregivers / caregiver: my patients
  POST       /api/caregivers/invite/                   patient creates a one-time code
  POST       /api/caregivers/accept/                   caregiver enters the code
  PATCH/DELETE /api/caregivers/<id>/                   patient changes permissions / revokes
  GET        /api/caregivers/patients/<pid>/           caregiver: what this patient allowed them to see
  POST       /api/caregivers/patients/<pid>/emergency/ caregiver requests emergency help for the patient
  POST       /api/caregivers/patients/<pid>/tests/     caregiver requests a test for the patient
  POST       /api/caregivers/patients/<pid>/report/    caregiver reports symptoms
Staff
  GET        /api/staff/events/?open=1                 monitoring events with AI summary (operators)
  POST       /api/staff/events/<id>/close/             a human takes responsibility / closes it
"""
import secrets

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.decorators import api_view
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from accounts.models import CaregiverLink, User
from patient_monitoring import proactive, services
from patient_monitoring.api import DeviceSerializer
from patient_monitoring.models import AssistantPreferences, MonitoringEvent


def _patient(request):
    if request.user.role != "patient":
        raise PermissionDenied("Only patients can do this.")
    return request.user


def _err(msg, code=status.HTTP_400_BAD_REQUEST):
    return Response({"detail": str(msg)}, status=code)


def _link(caregiver, patient_id, permission=None):
    link = CaregiverLink.objects.filter(caregiver=caregiver, patient_id=patient_id, status="active").first()
    if link is None or (permission and not getattr(link, permission)):
        raise PermissionDenied("This patient has not given you permission for this.")
    return link


# ------------------------------------------------------------------ preferences
class PrefsSerializer(serializers.ModelSerializer):
    class Meta:
        model = AssistantPreferences
        exclude = ("id", "patient", "last_latitude", "last_longitude", "last_location_at")

    def validate_checkin_times(self, v):
        out = []
        for t in v or []:
            try:
                hh, mm = (int(x) for x in str(t).split(":"))
                assert 0 <= hh < 24 and 0 <= mm < 60
            except (ValueError, AssertionError):
                raise serializers.ValidationError(f"Use HH:MM times, got {t!r}.")
            out.append(f"{hh:02d}:{mm:02d}")
        if len(out) > 6:
            raise serializers.ValidationError("At most 6 check-ins a day.")
        return sorted(set(out))

    def validate_voice_rate(self, v):
        return min(max(float(v), 0.1), 1.0)

    def validate_text_scale(self, v):
        return min(max(float(v), 0.8), 2.0)


@api_view(["GET", "PATCH"])
def preferences(request):
    p = proactive.prefs(_patient(request))
    if request.method == "PATCH":
        ser = PrefsSerializer(p, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
    data = PrefsSerializer(p).data
    data["home_monitoring"] = proactive.home_consent(p.patient) is not None
    data["home_consent_text"] = proactive.HOME_CONSENT_TEXT
    return Response(data)


@api_view(["POST", "DELETE"])
def home_consent(request):
    u = _patient(request)
    if request.method == "DELETE":
        proactive.revoke_home_consent(u)
        return Response({"home_monitoring": False})
    proactive.give_home_consent(u)
    return Response({"home_monitoring": True, "text": proactive.HOME_CONSENT_TEXT})


class HomeReadingSerializer(serializers.Serializer):
    measured_at = serializers.DateTimeField(required=False)
    temperature_c = serializers.FloatField(required=False, allow_null=True)
    heart_rate_bpm = serializers.IntegerField(required=False, allow_null=True)
    spo2_pct = serializers.IntegerField(required=False, allow_null=True)
    systolic_mmhg = serializers.IntegerField(required=False, allow_null=True)
    diastolic_mmhg = serializers.IntegerField(required=False, allow_null=True)
    respiratory_rate = serializers.IntegerField(required=False, allow_null=True)
    device = DeviceSerializer()


@api_view(["POST"])
def home_reading(request):
    """REAL measurement from the patient's own device (simulated values never come through this endpoint)."""
    u = _patient(request)
    ser = HomeReadingSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    data = dict(ser.validated_data)
    device = data.pop("device")
    try:
        r, ev = proactive.record_home_reading(u, data, "bluetooth", device, entered_by=u)
    except services.ReadingRejected as e:
        return _err(e)
    return Response({"reading": r.as_dict(), "event": proactive.as_dict(ev) if ev else None},
                    status=status.HTTP_201_CREATED)


@api_view(["POST"])
def location(request):
    u = _patient(request)
    p = proactive.prefs(u)
    if not p.share_location_on_escalation:
        return Response({"stored": False, "detail": "Location sharing for emergencies is switched off."})
    try:
        lat, lng = float(request.data["latitude"]), float(request.data["longitude"])
        assert -90 <= lat <= 90 and -180 <= lng <= 180
    except (KeyError, TypeError, ValueError, AssertionError):
        return _err("latitude / longitude required.")
    p.last_latitude, p.last_longitude, p.last_location_at = lat, lng, timezone.now()
    p.save(update_fields=["last_latitude", "last_longitude", "last_location_at", "updated_at"])
    return Response({"stored": True})


# ------------------------------------------------------------------ events
def _event_for(request, pk, permission="can_respond_checkins"):
    ev = get_object_or_404(MonitoringEvent.objects.select_related("patient", "emergency"), pk=pk)
    u = request.user
    if u.pk != ev.patient_id:
        if u.role != "caregiver":
            raise PermissionDenied()
        _link(u, ev.patient_id, permission)
    return ev


@api_view(["GET", "POST"])
def events(request):
    u = request.user
    if request.method == "POST":
        p = _patient(request)
        kind = request.data.get("kind")
        if kind not in ("possible_fall", "patient_report", "contact_request"):
            return _err("kind must be possible_fall, patient_report or contact_request.")
        ev = proactive.report(p, kind, request.data.get("symptoms") or [], str(request.data.get("text", ""))[:500], by=p)
        return Response(proactive.as_dict(ev), status=status.HTTP_201_CREATED)
    try:
        proactive.tick()  # the app's poll also moves the schedule forward
    except Exception:
        pass
    if u.role == "caregiver":
        ids = CaregiverLink.objects.filter(caregiver=u, status="active", can_respond_checkins=True).values_list(
            "patient_id", flat=True)
        qs = MonitoringEvent.objects.filter(patient_id__in=list(ids))
    else:
        qs = MonitoringEvent.objects.filter(patient=_patient(request))
    if request.query_params.get("open") == "1":
        qs = qs.filter(status__in=MonitoringEvent.OPEN)
    out = []
    for ev in qs.select_related("emergency", "patient")[:50]:
        d = proactive.as_dict(ev)
        if u.role == "caregiver":
            d["patient"] = ev.patient.get_full_name() or ev.patient.username
            d["patient_id"] = ev.patient_id
        out.append(d)
    return Response(out)


@api_view(["POST"])
def event_respond(request, pk):
    ev = _event_for(request, pk)
    answer = request.data.get("answer")
    if answer not in ("ok", "not_ok", "help"):
        return _err("answer must be ok, not_ok or help.")
    pain = request.data.get("pain")
    try:
        pain = None if pain in (None, "") else max(0, min(10, int(pain)))
    except (TypeError, ValueError):
        return _err("pain must be 0-10.")
    out = proactive.respond(ev, request.user, answer, request.data.get("symptoms") or [], pain,
                            str(request.data.get("text", ""))[:500], str(request.data.get("channel", "buttons"))[:20])
    ev.refresh_from_db()
    return Response({**out, "event": proactive.as_dict(ev)})


# ------------------------------------------------------------------ caregivers
def _link_dict(l, for_patient):
    other = l.caregiver if for_patient else l.patient
    return {"id": l.pk, "status": l.status, "relationship": l.relationship,
            "invite_code": l.invite_code if for_patient and l.status == "invited" else None,
            "name": (other.get_full_name() or other.username) if other else None,
            "patient_id": l.patient_id,
            **{f: getattr(l, f) for f in ("can_request_emergency", "can_request_tests", "can_respond_checkins",
                                          "can_view_vitals", "receives_notifications")}}


@api_view(["GET"])
def caregiver_links(request):
    u = request.user
    if u.role == "patient":
        qs = CaregiverLink.objects.filter(patient=u).exclude(status="revoked").select_related("caregiver")
        return Response([_link_dict(l, True) for l in qs])
    if u.role == "caregiver":
        qs = CaregiverLink.objects.filter(caregiver=u, status="active").select_related("patient")
        return Response([_link_dict(l, False) for l in qs])
    raise PermissionDenied()


PERMS = ("can_request_emergency", "can_request_tests", "can_respond_checkins", "can_view_vitals",
         "receives_notifications")


@api_view(["POST"])
def caregiver_invite(request):
    u = _patient(request)
    if CaregiverLink.objects.filter(patient=u).exclude(status="revoked").count() >= 5:
        return _err("At most 5 caregivers.")
    code = secrets.token_hex(4).upper()
    l = CaregiverLink.objects.create(patient=u, invite_code=code, relationship=str(request.data.get("relationship", ""))[:50],
                                     **{p: bool(request.data.get(p, getattr(CaregiverLink, p).field.default)) for p in PERMS})
    services.audit(u, None, "caregiver_invited", f"link {l.pk}")
    return Response(_link_dict(l, True), status=status.HTTP_201_CREATED)


@api_view(["POST"])
def caregiver_accept(request):
    u = request.user
    if u.role != "caregiver":
        raise PermissionDenied("Register a caregiver account to accept an invitation.")
    code = str(request.data.get("code", "")).strip().upper()
    l = CaregiverLink.objects.filter(invite_code=code, status="invited").first()
    if l is None:
        return _err("This code is not valid or was already used.")
    l.caregiver, l.status, l.accepted_at = u, "active", timezone.now()
    l.invite_code = f"USED-{l.pk}-{secrets.token_hex(2)}"[:12]  # one-time code
    l.save()
    services.audit(u, None, "caregiver_link_accepted", f"patient {l.patient_id}")
    from notifications.services import notify_user
    notify_user(l.patient, "Caregiver connected", f"{u.get_full_name() or u.username} can now help you, with the "
                                                   "permissions you chose. You can change or remove this any time.")
    return Response(_link_dict(l, False))


@api_view(["PATCH", "DELETE"])
def caregiver_link(request, pk):
    u = _patient(request)
    l = get_object_or_404(CaregiverLink, pk=pk, patient=u)
    if request.method == "DELETE":
        l.status, l.revoked_at = "revoked", timezone.now()
        l.save()
        services.audit(u, None, "caregiver_revoked", f"link {l.pk}")
        return Response({"status": "revoked"})
    for p in PERMS:
        if p in request.data:
            setattr(l, p, bool(request.data[p]))
    l.save()
    services.audit(u, None, "caregiver_permissions_changed", f"link {l.pk}")
    return Response(_link_dict(l, True))


@api_view(["GET"])
def caregiver_patient(request, pid):
    link = _link(request.user, pid)
    p = link.patient
    data = {"patient_id": p.pk, "name": p.get_full_name() or p.username, "permissions": _link_dict(link, False)}
    from emergency.models import EmergencyRequest
    em = EmergencyRequest.objects.filter(patient=p).exclude(status__in=["completed", "cancelled"]).first()
    data["active_emergency"] = {"id": em.pk, "reference": em.reference, "status": em.get_status_display(),
                                "label": em.operator_label} if em else None
    if link.can_view_vitals:
        from patient_monitoring.models import VitalSignReading
        data["vitals"] = [r.as_dict() for r in VitalSignReading.objects.filter(patient=p).order_by("-measured_at")[:20]]
        services.audit(request.user, None, "caregiver_viewed_vitals", f"patient {p.pk}")
    return Response(data)


@api_view(["POST"])
def caregiver_emergency(request, pid):
    from emergency import services as em_services
    link = _link(request.user, pid, "can_request_emergency")
    c = request.user
    try:
        lat, lng = float(request.data["latitude"]), float(request.data["longitude"])
    except (KeyError, TypeError, ValueError):
        return _err("The location where help is needed is required (latitude / longitude).")
    data = {"emergency_type": request.data.get("emergency_type", "other"),
            "description": f"[Reported by caregiver {c.get_full_name() or c.username}] "
                           f"{str(request.data.get('description', ''))[:900]}",
            "latitude": lat, "longitude": lng, "location_source": "gps", "location_consent": True,
            "location_description": f"Location of caregiver {c.get_full_name() or c.username}",
            "contact_phone": c.phone or link.patient.phone, "is_simulated": False,
            "breathing_difficulty": bool(request.data.get("breathing_difficulty")),
            "chest_pain": bool(request.data.get("chest_pain")),
            "conscious": request.data.get("conscious", True) not in (False, "false")}
    try:
        em = em_services.create_emergency(link.patient, data)
    except em_services.DuplicateEmergency as dup:
        return Response({"detail": str(dup), "id": dup.existing.pk, "reference": dup.existing.reference},
                        status=status.HTTP_409_CONFLICT)
    em_services.log_status(em, f"Requested by caregiver {c.get_full_name() or c.username}", c)
    from notifications.services import notify_user
    notify_user(link.patient, f"Emergency {em.reference} requested for you",
                f"Your caregiver {c.get_full_name() or c.username} asked for emergency help for you.", em)
    return Response({"id": em.pk, "reference": em.reference, "status": em.get_status_display()},
                    status=status.HTTP_201_CREATED)


@api_view(["POST"])
def caregiver_tests(request, pid):
    from patient_monitoring import test_requests as tr
    link = _link(request.user, pid, "can_request_tests")
    c = request.user
    try:
        req = tr.create_request(link.patient, list(request.data.get("tests") or []),
                                f"Requested by caregiver {c.get_full_name() or c.username}")
    except tr.TestRequestError as e:
        return _err(e)
    return Response(tr.as_dict(req, with_readings=False), status=status.HTTP_201_CREATED)


@api_view(["POST"])
def caregiver_report(request, pid):
    link = _link(request.user, pid, "can_respond_checkins")
    ev = proactive.report(link.patient, "caregiver_report", request.data.get("symptoms") or [],
                          str(request.data.get("text", ""))[:500], by=request.user)
    return Response(proactive.as_dict(ev), status=status.HTTP_201_CREATED)


# ------------------------------------------------------------------ staff
def _staff(request):
    u = request.user
    if not (u.is_superuser or u.role in ("operator", "admin")):
        raise PermissionDenied()
    return u


@api_view(["GET"])
def staff_events(request):
    _staff(request)
    qs = MonitoringEvent.objects.select_related("patient", "emergency")
    if request.query_params.get("open") == "1":
        qs = qs.filter(status__in=MonitoringEvent.OPEN)
    return Response([proactive.as_dict(ev, for_staff=True) for ev in qs[:100]])


@api_view(["POST"])
def staff_event_close(request, pk):
    u = _staff(request)
    ev = get_object_or_404(MonitoringEvent, pk=pk)
    note = str(request.data.get("note", ""))[:200]
    proactive._log(ev, "operator", f"Closed by {u.get_full_name() or u.username}. {note}".strip())
    ev.status, ev.closed_at = MonitoringEvent.Status.CLOSED, timezone.now()
    ev.save()
    services.audit(u, ev.emergency, "monitoring_event_closed", f"#{ev.pk}")
    return Response(proactive.as_dict(ev, for_staff=True))


__all__ = ["User"]
