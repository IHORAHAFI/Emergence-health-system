"""REST endpoints for the mobile app's patient features.

Thin wrappers around the existing services (patient_monitoring.test_requests, medical_rag) -
no business rules are duplicated here. Every endpoint only ever returns the caller's own records.

GET  /api/tests/                       my test requests (?open=1)
POST /api/tests/                       {"tests": ["temperature", ...], "note": ""}
GET  /api/tests/catalog/               available tests
GET  /api/tests/<ref>/                 one request with summary + readings
POST /api/tests/<ref>/cancel/
POST /api/tests/<ref>/readings/        a REAL Bluetooth measurement from the patient's own device
GET  /api/vitals/latest/               my most recent readings (emergency + tests, real and simulated)
GET  /api/diseases/?q=                 approved medical library
GET  /api/diseases/<slug>/
POST /api/assistant/ask/               {"question": "..."}
"""
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.decorators import api_view
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from medical_rag.disease_views import _visible
from medical_rag.models import Disease
from medical_rag.retriever import DISCLAIMER
from patient_monitoring import services, test_requests as tr
from patient_monitoring.api import DeviceSerializer
from patient_monitoring.models import MedicalTestRequest, VitalSignReading

R = MedicalTestRequest


def _patient(request):
    if request.user.role != "patient":
        raise PermissionDenied("Only patients can use this endpoint.")
    return request.user


def _own(request, ref):
    return get_object_or_404(R, reference=ref, patient=_patient(request))


def _error(e, code=status.HTTP_400_BAD_REQUEST):
    return Response({"detail": str(e)}, status=code)


# ------------------------------------------------------------------ medical tests
@api_view(["GET"])
def test_catalog(request):
    return Response([{"code": c, "label": t[0], "unit": t[2], "fields": t[3]} for c, t in R.TESTS.items()])


@api_view(["GET", "POST"])
def tests(request):
    user = _patient(request)
    if request.method == "POST":
        codes = request.data.get("tests") or []
        if not isinstance(codes, list):
            return _error("tests must be a list of test codes.")
        try:
            req = tr.create_request(user, codes, str(request.data.get("note", "")))
        except tr.TestRequestError as e:
            return _error(e)
        return Response(tr.as_dict(req, with_readings=False), status=status.HTTP_201_CREATED)
    qs = R.objects.filter(patient=user)
    if request.query_params.get("open") == "1":
        qs = qs.filter(status__in=R.OPEN)
    return Response([tr.as_dict(r, with_readings=False) for r in qs[:100]])


@api_view(["GET"])
def test_detail(request, ref):
    return Response(tr.as_dict(_own(request, ref)))


@api_view(["POST"])
def test_cancel(request, ref):
    try:
        req = tr.cancel(_own(request, ref), request.user)
    except tr.TestRequestError as e:
        return _error(e)
    return Response(tr.as_dict(req, with_readings=False))


class BluetoothReadingSerializer(serializers.Serializer):
    temperature_c = serializers.FloatField(required=False, allow_null=True)
    heart_rate_bpm = serializers.IntegerField(required=False, allow_null=True)
    spo2_pct = serializers.IntegerField(required=False, allow_null=True)
    systolic_mmhg = serializers.IntegerField(required=False, allow_null=True)
    diastolic_mmhg = serializers.IntegerField(required=False, allow_null=True)
    respiratory_rate = serializers.IntegerField(required=False, allow_null=True)
    device = DeviceSerializer()


@api_view(["POST"])
def test_reading(request, ref):
    """Real measurement from a Bluetooth device the patient connected. Only accepted while the test is running."""
    req = _own(request, ref)
    ser = BluetoothReadingSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    data = dict(ser.validated_data)
    device = data.pop("device")
    if data.get("systolic_mmhg") is not None and data.get("diastolic_mmhg") is not None \
            and data["systolic_mmhg"] <= data["diastolic_mmhg"]:
        return _error("Systolic pressure must be higher than diastolic - please re-measure.")
    try:
        r = tr.record_test_reading(req, data, VitalSignReading.Source.BLUETOOTH, device, entered_by=request.user)
    except services.ReadingRejected as e:
        return _error(e)
    return Response(r.as_dict(), status=status.HTTP_201_CREATED)


@api_view(["GET"])
def latest_vitals(request):
    user = _patient(request)
    qs = (VitalSignReading.objects.filter(patient=user)
          .select_related("device", "emergency", "test_request").prefetch_related("reviews__reviewer"))
    out = []
    for r in qs[: min(int(request.query_params.get("limit", 30)), 200)]:
        d = r.as_dict()
        d["emergency"] = r.emergency.reference if r.emergency_id else None
        d["test_request"] = r.test_request.reference if r.test_request_id else None
        out.append(d)
    return Response(out)


# ------------------------------------------------------------------ medical information
def _disease(d, full=False):
    lines = lambda s: [x.strip() for x in (s or "").splitlines() if x.strip()]  # noqa: E731
    base = {"name": d.name, "slug": d.slug, "category": d.category, "category_display": d.get_category_display(),
            "reviewed": d.is_reviewed, "summary": d.description[:160]}
    if full:
        base.update(description=d.description, causes=d.causes, symptoms=lines(d.symptoms),
                    risk_factors=lines(d.risk_factors), diagnosis=d.diagnosis, treatment=d.treatment,
                    prevention=lines(d.prevention), warning_signs=lines(d.warning_signs),
                    source_name=d.source_name, source_url=d.source_url,
                    reviewed_at=d.reviewed_at.isoformat() if d.reviewed_at else None)
    return base


@api_view(["GET"])
def diseases(request):
    qs = _visible(request.user)
    q = request.query_params.get("q", "").strip()
    if q:
        from django.db.models import Q
        qs = qs.filter(Q(name__icontains=q) | Q(symptoms__icontains=q) | Q(description__icontains=q))
    return Response([_disease(d) for d in qs.order_by("name")[:100]])


@api_view(["GET"])
def disease_detail(request, slug):
    return Response(_disease(get_object_or_404(_visible(request.user), slug=slug), full=True))


@api_view(["POST"])
def assistant_ask(request):
    from medical_rag import service
    from medical_rag.views import related_entries
    d = request.data
    q = str(d.get("question", ""))[:1000]
    try:
        result = service.ask(request.user, q, conversation_id=d.get("conversation_id"),
                             save_history=d.get("save_history", True) not in (False, "false", "0"),
                             include_vitals=d.get("include_vitals") in (True, "true", "1"),
                             emergency_ref=(d.get("emergency_ref") or "").strip() or None,
                             history=d.get("history") if isinstance(d.get("history"), list) else None,
                             request=request)
    except service.AssistantError as e:
        return _error(e, status.HTTP_403_FORBIDDEN)
    result["library"] = [{**e, "slug": e["url"].rstrip("/").split("/")[-1]} for e in related_entries(q, request.user)]
    result.setdefault("disclaimer", DISCLAIMER)
    return Response(result)


@api_view(["POST"])
def assistant_feedback(request):
    """POST {response_id, rating: helpful|unhelpful|unclear|suggestion, suggestion?, message_id?, comment?}"""
    from medical_rag import service
    try:
        fb = service.feedback(request.user, request.data)
    except service.AssistantError as e:
        return _error(e, status.HTTP_400_BAD_REQUEST)
    return Response({"saved": True, "duplicate": fb is None}, status=status.HTTP_201_CREATED if fb else 200)


@api_view(["GET", "DELETE"])
def assistant_history(request, pk=None):
    from medical_rag import service
    try:
        if request.method == "DELETE":
            return Response({"deleted": service.delete_conversations(request.user, pk)})
        return Response(service.conversation_detail(request.user, pk) if pk else
                        {"conversations": service.conversations(request.user)})
    except service.AssistantError as e:
        return _error(e, status.HTTP_404_NOT_FOUND)


__all__ = ["Disease"]
