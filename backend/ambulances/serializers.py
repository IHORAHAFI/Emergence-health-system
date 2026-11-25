from rest_framework import serializers

from .models import Ambulance


class AmbulanceSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    base_hospital_name = serializers.CharField(source="base_hospital.name", read_only=True, default=None)

    class Meta:
        model = Ambulance
        fields = ("id", "plate_number", "ambulance_type", "status", "status_display", "base_hospital",
                  "base_hospital_name", "driver", "latitude", "longitude", "last_location_update")


class AmbulanceLocationSerializer(serializers.Serializer):
    latitude = serializers.FloatField(min_value=-90, max_value=90)
    longitude = serializers.FloatField(min_value=-180, max_value=180)
    status = serializers.ChoiceField(choices=Ambulance.Status.choices, required=False)
