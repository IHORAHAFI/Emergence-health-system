"""WebSocket: ws://<host>/ws/vitals/<reference>/  or  /ws/vitals/  (all emergencies the user may see).

Authentication uses the normal Django session cookie (AuthMiddlewareStack).
Every connection is permission-checked and audited before any data is sent."""
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from emergency.models import EmergencyRequest
from . import services


class VitalsConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")
        ref = self.scope["url_route"]["kwargs"].get("ref")
        self.em_id = None
        if not user or not user.is_authenticated:
            return await self.close(code=4401)
        if ref:
            em = await self._get(ref)
            if not em or not await database_sync_to_async(services.can_view)(user, em):
                return await self.close(code=4403)
            self.em_id = em.id
            await database_sync_to_async(services.audit)(user, em, "vitals_live_opened")
            await self.channel_layer.group_add(f"vitals_{em.id}", self.channel_name)
        else:
            if not (user.is_superuser or user.role in ("admin", "operator", "hospital")):
                return await self.close(code=4403)
            if user.role == "hospital" and not user.is_approved:
                return await self.close(code=4403)
            await self.channel_layer.group_add("vitals_all", self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        group = f"vitals_{self.em_id}" if getattr(self, "em_id", None) else "vitals_all"
        await self.channel_layer.group_discard(group, self.channel_name)

    async def vitals_update(self, event):
        # the "all" feed is filtered per user so hospital staff only get their own patients
        if not self.em_id and not await self._allowed(event["emergency"]):
            return
        await self.send_json({"emergency": event["emergency"], "reading": event["reading"]})

    @database_sync_to_async
    def _get(self, ref):
        return EmergencyRequest.objects.select_related("assigned_ambulance").filter(reference=ref).first()

    @database_sync_to_async
    def _allowed(self, ref):
        em = EmergencyRequest.objects.select_related("assigned_ambulance").filter(reference=ref).first()
        return bool(em and services.can_view(self.scope["user"], em))


class NotifyConsumer(AsyncJsonWebsocketConsumer):
    """ws://<host>/ws/notify/ - tells open pages that something changed so they reload their data.

    Operators/admins join "operators"; a patient joins only "patient_<own id>". The message carries
    no medical data - pages fetch it through permission-checked endpoints (and also poll as a fallback)."""

    async def connect(self):
        user = self.scope.get("user")
        if not user or not user.is_authenticated:
            return await self.close(code=4401)
        if user.is_superuser or user.role in ("operator", "admin"):
            self.group = "operators"
        elif user.role == "patient":
            self.group = f"patient_{user.id}"
        else:
            return await self.close(code=4403)
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        if getattr(self, "group", None):
            await self.channel_layer.group_discard(self.group, self.channel_name)

    async def notify_event(self, event):
        await self.send_json({k: v for k, v in event.items() if k != "type"})
