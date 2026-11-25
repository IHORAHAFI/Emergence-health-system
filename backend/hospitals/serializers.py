from rest_framework import serializers

from .models import Hospital, Specialty


class SpecialtySerializer(serializers.ModelSerializer):
    class Meta:
        model = Specialty
        fields = ("id", "code", "name")


class HospitalSerializer(serializers.ModelSerializer):
    specialties = SpecialtySerializer(many=True, read_only=True)
    level_display = serializers.CharField(source="get_level_display", read_only=True)
    distance_km = serializers.FloatField(read_only=True, required=False)

    class Meta:
        model = Hospital
        fields = ("id", "name", "address", "phone", "latitude", "longitude", "level", "level_display",
                  "specialties", "total_beds", "available_beds", "icu_available",
                  "has_emergency_unit", "is_active", "distance_km")


class HospitalCapacitySerializer(serializers.ModelSerializer):
    class Meta:
        model = Hospital
        fields = ("available_beds", "icu_available", "has_emergency_unit")
