"""REST API for devices / apps sending vital signs.

POST /api/monitoring/<ref>/consent/            patient gives consent (DELETE revokes)
POST /api/monitoring/<ref>/readings/           send one reading (JSON, see ReadingSerializer)
POST /api/monitoring/<ref>/readings/simulate/  generate a SIMULATED reading (demo)
GET  /api/monitoring/<ref>/readings/           history (authorized viewers; audited)
POST /api/monitoring/readings/<id>/review/     clinician confirms / overrides
"""
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.decorators import api_view
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from emergency.models import EmergencyRequest
from . import services
from .models import ClinicalReview, MedicalDevice, VitalSignReading


class DeviceSerializer(serializers.Serializer):
    device_identifier = serializers.CharField(max_length=128)
    name = serializers.CharField(max_length=100, required=False, allow_blank=True)
    kind = serializers.ChoiceField(choices=MedicalDevice.Kind.choices, required=False)
    manufacturer = serializers.CharField(max_length=100, required=False, allow_blank=True)


class ReadingSerializer(serializers.Serializer):
    source = serializers.ChoiceField(choices=["bluetooth", "manual"], default="manual")
    measured_at = serializers.DateTimeField(required=False)
    temperature_c = serializers.DecimalField(max_digits=4, decimal_places=1, required=False, allow_null=True)
    heart_rate_bpm = serializers.IntegerField(required=False, allow_null=True)
    spo2_pct = serializers.IntegerField(required=False, allow_null=True)
    systolic_mmhg = serializers.IntegerField(required=False, allow_null=True)
    diastolic_mmhg = serializers.IntegerField(required=False, allow_null=True)
    respiratory_rate = serializers.IntegerField(required=False, allow_null=True)
    on_supplemental_oxygen = serializers.BooleanField(required=False, default=False)
    consciousness = serializers.ChoiceField(choices=VitalSignReading.Consciousness.choices, required=False,
                                            allow_blank=True)
    ecg_rhythm = serializers.CharField(max_length=100, required=False, allow_blank=True)
    ecg_samples = serializers.ListField(child=serializers.FloatField(), required=False, max_length=10000)
    ecg_sample_rate_hz = serializers.IntegerField(required=False, min_value=50, max_value=2000)
    device = DeviceSerializer(required=False)

    def validate(self, a):
        if a.get("source") == "bluetooth" and not a.get("device"):
            raise serializers.ValidationError("Bluetooth readings must include the device identification.")
        return a


def _em(ref):
    return get_object_or_404(EmergencyRequest.objects.select_related("assigned_ambulance", "assigned_hospital"),
                             reference=ref)


@api_view(["POST", "DELETE"])
def consent(request, ref):
    em = _em(ref)
    if request.user.role != "patient" or em.patient_id != request.user.id:
        raise PermissionDenied("Only the patient can give or revoke consent.")
    if request.method == "DELETE":
        services.revoke_consent(em, request.user)
        return Response({"consent": False})
    services.give_consent(em, request.user)
    return Response({"consent": True, "text": services.CONSENT_TEXT})


@api_view(["GET", "POST"])
def readings(request, ref):
    em = _em(ref)
    if request.method == "GET":
        if not services.can_view(request.user, em):
            raise PermissionDenied()
        services.audit(request.user, em, "vitals_viewed_api", request=request)
        return Response([r.as_dict() for r in em.vitals.prefetch_related("reviews__reviewer").select_related("device")[:200]])
    ser = ReadingSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    data = dict(ser.validated_data)
    device = data.pop("device", None)
    try:
        r = services.record_reading(em, request.user, data, data.pop("source"), device)
    except services.ReadingRejected as e:
        return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(r.as_dict(), status=status.HTTP_201_CREATED)


@api_view(["POST"])
def simulate(request, ref):
    em = _em(ref)
    values = services.simulate_values(request.data.get("scenario", "normal"), request.data.get("device", "all"))
    try:
        r = services.record_reading(em, request.user, values, VitalSignReading.Source.SIMULATED,
                                    {"device_identifier": f"SIM-{request.data.get('device', 'all')}",
                                     "name": "Demo simulator", "kind": "simulator", "manufacturer": "simulation"})
    except services.ReadingRejected as e:
        return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(r.as_dict(), status=status.HTTP_201_CREATED)


@api_view(["POST"])
def review(request, pk):
    r = get_object_or_404(VitalSignReading.objects.select_related("emergency__assigned_ambulance"), pk=pk)
    if not services.can_review(request.user, r.emergency):
        raise PermissionDenied("Only the treating clinical staff can review readings.")
    decision = request.data.get("decision")
    risk = request.data.get("override_risk", "")
    if decision not in ("confirm", "override") or (decision == "override" and risk not in services.clinical.RISK_ORDER):
        return Response({"detail": "decision must be confirm, or override with override_risk"}, status=400)
    ClinicalReview.objects.create(reading=r, reviewer=request.user, decision=decision,
                                  override_risk=risk if decision == "override" else "",
                                  note=str(request.data.get("note", ""))[:500])
    services.audit(request.user, r.emergency, f"reading_{decision}", f"reading {r.id} {risk}", request)
    services._safe(services.broadcast, r)
    return Response(r.as_dict())
