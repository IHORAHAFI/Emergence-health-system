from django.db.models import Q
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ("id", "channel", "title", "message", "emergency", "emergency_ref", "test_request_ref",
                  "is_read", "created_at")

    emergency_ref = serializers.CharField(source="emergency.reference", read_only=True, default=None)
    test_request_ref = serializers.CharField(source="test_request.reference", read_only=True, default=None)


class NotificationViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = NotificationSerializer

    def get_queryset(self):
        u = self.request.user
        q = Q(recipient=u)
        if u.hospital_id:
            q |= Q(hospital_id=u.hospital_id)
        qs = Notification.objects.filter(q).select_related("emergency", "test_request")
        if self.request.query_params.get("unread") == "1":
            qs = qs.filter(is_read=False)
        return qs

    @action(detail=False, methods=["post"], url_path="read-all")
    def read_all(self, request):
        n = self.get_queryset().filter(is_read=False).update(is_read=True)
        return Response({"updated": n})

    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        n = self.get_object()
        n.is_read = True
        n.save(update_fields=["is_read"])
        return Response({"status": "read"})
