from django.db.models import Q
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action, api_view
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from ai_recommendation.priority import predict_priority
from ai_recommendation.recommender import recommend_hospitals
from . import services
from .models import EmergencyRequest
from .serializers import (DispatchSerializer, EmergencyCreateSerializer, EmergencySerializer,
                          PriorityOverrideSerializer, StatusUpdateSerializer)

STAFF = ("admin", "operator")


class EmergencyViewSet(mixins.CreateModelMixin, mixins.ListModelMixin,
                       mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        return EmergencyCreateSerializer if self.action == "create" else EmergencySerializer

    def get_queryset(self):
        u = self.request.user
        qs = EmergencyRequest.objects.select_related(
            "patient", "assigned_hospital", "assigned_ambulance").prefetch_related("logs")
        if u.role in STAFF or u.is_superuser:
            pass
        elif u.role == "hospital":
            if not u.is_approved or not u.hospital_id:
                return qs.none()
            qs = qs.filter(assigned_hospital_id=u.hospital_id)
        elif u.role == "paramedic":
            qs = qs.filter(assigned_ambulance__driver=u)
        else:
            qs = qs.filter(patient=u)
        if self.request.query_params.get("active") == "1":
            qs = qs.exclude(status__in=["completed", "cancelled"])
        return qs

    def create(self, request, *args, **kwargs):
        if request.user.role != "patient":
            raise PermissionDenied("Only patient accounts can request emergency assistance")
        ser = EmergencyCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            em = services.create_emergency(request.user, dict(ser.validated_data))
        except services.DuplicateEmergency as dup:
            return Response({"detail": str(dup), "existing": EmergencySerializer(dup.existing).data},
                            status=status.HTTP_409_CONFLICT)
        return Response(EmergencySerializer(em).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def status(self, request, pk=None):
        em = self.get_object()
        ser = StatusUpdateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        new = ser.validated_data["status"]
        u = request.user
        if u.role == "patient" and new != "cancelled":
            raise PermissionDenied("Patients can only cancel")
        if u.role == "hospital" and new not in ("arrived", "completed"):
            raise PermissionDenied("Hospital staff can only confirm arrival / completion")
        try:
            services.update_status(em, new, u, ser.validated_data["note"])
        except ValueError as e:
            raise ValidationError(str(e))
        return Response(EmergencySerializer(em).data)

    @action(detail=True, methods=["post"])
    def override_priority(self, request, pk=None):
        if request.user.role not in STAFF:
            raise PermissionDenied()
        em = self.get_object()
        ser = PriorityOverrideSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        em.priority = ser.validated_data["priority"]
        em.priority_overridden = True
        em.recommendations = recommend_hospitals(em.latitude, em.longitude, em.emergency_type, em.priority)
        em.save()
        services.log_status(em, f"Priority overridden to {em.priority}", request.user)
        return Response(EmergencySerializer(em).data)

    @action(detail=True, methods=["post"], url_path="dispatch")
    def redispatch(self, request, pk=None):
        """Operator (re)dispatch, optionally choosing a specific hospital."""
        if request.user.role not in STAFF:
            raise PermissionDenied()
        em = self.get_object()
        ser = DispatchSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        services.dispatch(em, ser.validated_data.get("hospital_id"), request.user)
        return Response(EmergencySerializer(em).data)


@api_view(["POST"])
def ai_preview(request):
    """Preview AI priority + hospital suggestions without creating a request."""
    d = request.data
    try:
        lat, lng = float(d["latitude"]), float(d["longitude"])
    except (KeyError, ValueError, TypeError):
        raise ValidationError("latitude and longitude are required")
    priority, conf = predict_priority(d)
    return Response({"priority": priority, "confidence": conf,
                     "hospitals": recommend_hospitals(lat, lng, d.get("emergency_type", "other"), priority)})
