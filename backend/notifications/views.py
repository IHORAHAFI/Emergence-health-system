from django.db.models import Q
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ("id", "channel", "title", "message", "emergency", "is_read", "created_at")


class NotificationViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = NotificationSerializer

    def get_queryset(self):
        u = self.request.user
        q = Q(recipient=u)
        if u.hospital_id:
            q |= Q(hospital_id=u.hospital_id)
        return Notification.objects.filter(q)

    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        n = self.get_object()
        n.is_read = True
        n.save(update_fields=["is_read"])
        return Response({"status": "read"})
