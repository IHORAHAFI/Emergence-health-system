from rest_framework import serializers

from ambulances.serializers import AmbulanceSerializer
from hospitals.serializers import HospitalSerializer
from .models import EmergencyRequest, StatusLog


class StatusLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = StatusLog
        fields = ("status", "note", "at")


class EmergencyCreateSerializer(serializers.ModelSerializer):
    latitude = serializers.FloatField(min_value=-90, max_value=90)
    longitude = serializers.FloatField(min_value=-180, max_value=180)
    pain_level = serializers.IntegerField(min_value=0, max_value=10, default=0)

    class Meta:
        model = EmergencyRequest
        fields = ("emergency_type", "description", "latitude", "longitude", "location_accuracy_m",
                  "location_source", "location_consent", "location_description", "contact_phone",
                  "conscious", "breathing_difficulty", "severe_bleeding", "chest_pain",
                  "pain_level", "pregnant", "age")
        extra_kwargs = {"location_consent": {"default": True}}

    def validate(self, attrs):
        if attrs.get("location_source", "gps") == "gps" and not attrs.get("location_consent", True):
            raise serializers.ValidationError(
                "GPS location requires the patient's consent. Choose the location manually instead.")
        return attrs


class EmergencySerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.get_full_name", read_only=True)
    patient_phone = serializers.CharField(source="patient.phone", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    type_display = serializers.CharField(source="get_emergency_type_display", read_only=True)
    assigned_hospital = HospitalSerializer(read_only=True)
    assigned_ambulance = AmbulanceSerializer(read_only=True)
    logs = StatusLogSerializer(many=True, read_only=True)
    operator_label = serializers.CharField(read_only=True)  # PENDING / ACCEPTED / IN PROGRESS / COMPLETED
    accepted_by_name = serializers.SerializerMethodField()

    class Meta:
        model = EmergencyRequest
        fields = "__all__"

    def get_accepted_by_name(self, em):
        u = em.accepted_by
        return (u.get_full_name() or u.username) if u else None


class StatusUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=EmergencyRequest.Status.choices)
    note = serializers.CharField(required=False, allow_blank=True, default="")


class PriorityOverrideSerializer(serializers.Serializer):
    priority = serializers.ChoiceField(choices=EmergencyRequest.PRIORITY_CHOICES)


class DispatchSerializer(serializers.Serializer):
    hospital_id = serializers.IntegerField(required=False)
