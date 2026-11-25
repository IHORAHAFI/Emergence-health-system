from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.response import Response

from hospitals.views import IsStaffOrReadOnly
from .models import Ambulance
from .serializers import AmbulanceLocationSerializer, AmbulanceSerializer


class AmbulanceViewSet(viewsets.ModelViewSet):
    queryset = Ambulance.objects.select_related("base_hospital", "driver")
    serializer_class = AmbulanceSerializer
    permission_classes = [permissions.IsAuthenticated, IsStaffOrReadOnly]

    @action(detail=False, methods=["get"])
    def mine(self, request):
        amb = Ambulance.objects.filter(driver=request.user).first()
        if not amb:
            raise NotFound("No ambulance assigned to you")
        return Response(AmbulanceSerializer(amb).data)

    @action(detail=False, methods=["post"], url_path="mine/location")
    def update_location(self, request):
        """Paramedic app posts GPS position periodically."""
        amb = Ambulance.objects.filter(driver=request.user).first()
        if not amb:
            raise NotFound("No ambulance assigned to you")
        ser = AmbulanceLocationSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        amb.latitude = ser.validated_data["latitude"]
        amb.longitude = ser.validated_data["longitude"]
        if "status" in ser.validated_data:
            amb.status = ser.validated_data["status"]
        amb.save()
        return Response(AmbulanceSerializer(amb).data)
