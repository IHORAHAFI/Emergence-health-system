from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from ai_recommendation.geo import haversine_km
from .models import Hospital
from .serializers import HospitalCapacitySerializer, HospitalSerializer


class IsStaffOrReadOnly(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        return request.user.is_authenticated and request.user.role in ("admin", "operator", "hospital")


class HospitalViewSet(viewsets.ModelViewSet):
    queryset = Hospital.objects.filter(is_active=True).prefetch_related("specialties")
    serializer_class = HospitalSerializer
    permission_classes = [permissions.IsAuthenticated, IsStaffOrReadOnly]

    @action(detail=False, methods=["get"])
    def nearby(self, request):
        """GET /api/hospitals/nearby/?lat=-1.95&lng=30.06&radius=20"""
        try:
            lat = float(request.query_params["lat"])
            lng = float(request.query_params["lng"])
        except (KeyError, ValueError):
            raise ValidationError("lat and lng query parameters are required")
        radius = float(request.query_params.get("radius", 30))
        results = []
        for h in self.get_queryset():
            h.distance_km = round(haversine_km(lat, lng, h.latitude, h.longitude), 2)
            if h.distance_km <= radius:
                results.append(h)
        results.sort(key=lambda h: h.distance_km)
        return Response(HospitalSerializer(results, many=True).data)

    @action(detail=True, methods=["patch"])
    def capacity(self, request, pk=None):
        """Hospital staff update their own bed availability."""
        hospital = self.get_object()
        u = request.user
        if u.role == "hospital" and u.hospital_id != hospital.id:
            raise PermissionDenied("You can only update your own hospital")
        ser = HospitalCapacitySerializer(hospital, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(HospitalSerializer(hospital).data)
