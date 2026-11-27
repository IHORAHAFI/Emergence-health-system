"""Quick measurements from the mobile app (see patient_monitoring/quick_measurements.py).

POST /api/measurements/   patient sends one result; automatic, no Submit button in the app.
     201 created | 200 already stored (retry of the same client_id, nothing duplicated) | 400 invalid | 409 id clash
     Response: {"measurement": {...}, "saved": true, "duplicate": bool, "dashboard_notified": bool}
GET  /api/measurements/   patient: my latest 50
"""
from rest_framework import serializers, status
from rest_framework.decorators import api_view
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from patient_monitoring import quick_measurements as qm
from patient_monitoring.models import PatientMeasurement as PM


class MeasurementSerializer(serializers.Serializer):
    client_id = serializers.UUIDField()
    kind = serializers.ChoiceField(choices=PM.Kind.choices)
    status = serializers.ChoiceField(choices=PM.Status.choices)
    value = serializers.FloatField(required=False, allow_null=True)
    source = serializers.ChoiceField(choices=PM.Source.choices)
    method = serializers.ChoiceField(choices=PM.Method.choices)
    measured_at = serializers.DateTimeField()
    duration_s = serializers.FloatField(required=False, allow_null=True, min_value=0, max_value=600)
    failure_reason = serializers.CharField(required=False, allow_blank=True, max_length=200)
    device = serializers.DictField(required=False)
    quality = serializers.DictField(required=False)
    emergency = serializers.CharField(required=False, allow_blank=True, max_length=40)

    def validate_device(self, v):
        if len(str(v)) > 2000:
            raise serializers.ValidationError("Device details are too long.")
        return v

    def validate_quality(self, v):
        if len(str(v)) > 2000:
            raise serializers.ValidationError("Quality details are too long.")
        return v


@api_view(["GET", "POST"])
def measurements(request):
    if request.user.role != "patient":
        raise PermissionDenied("Only patients send their own measurements.")
    if request.method == "GET":
        rows = PM.objects.select_related("patient", "emergency").filter(patient=request.user)[:50]
        return Response([m.as_dict() for m in rows])
    ser = MeasurementSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    try:
        m, created = qm.submit(request.user, ser.validated_data)
    except qm.Rejected as e:
        return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
    except qm.Conflict as e:
        return Response({"detail": str(e)}, status=status.HTTP_409_CONFLICT)
    # Re-publish on a retry that the dashboards never heard about (e.g. the push failed the first time).
    notified = qm.publish(m) if created or m.dashboard_notified_at is None else True
    return Response({"measurement": m.as_dict(), "saved": True, "duplicate": not created,
                     "dashboard_notified": notified},
                    status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)
