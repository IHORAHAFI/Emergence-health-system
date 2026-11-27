"""Arduino / USB-serial IoT readings (see patient_monitoring/iot_ingest.py).

POST /api/iot/ingest/        serial bridge only (header X-Bridge-Token = settings.ARDUINO_BRIDGE_TOKEN)
     {"port": "COM3", "messages": [{...one JSON line from the Arduino + "received_at"...}, ...]}
     -> {"results": [{"result": "saved"|"duplicate"|"status"|"rejected", "detail": "..."}]}
POST /api/iot/connection/    serial bridge only: {"device_id", "connected": bool, "port"}
GET  /api/iot/my-readings/   patient (JWT): readings from devices assigned to me only
"""
import hmac

from django.conf import settings
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from patient_monitoring import iot_ingest

MAX_BATCH = 200


def _bridge_ok(request):
    expected = getattr(settings, "ARDUINO_BRIDGE_TOKEN", "")
    given = request.headers.get("X-Bridge-Token", "")
    return bool(expected) and hmac.compare_digest(given.encode(), expected.encode())


def _deny():
    if not getattr(settings, "ARDUINO_BRIDGE_TOKEN", ""):
        return Response({"detail": "Serial bridge disabled: set ARDUINO_BRIDGE_TOKEN in backend/.env."},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE)
    return Response({"detail": "Invalid bridge token."}, status=status.HTTP_403_FORBIDDEN)


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def ingest(request):
    if not _bridge_ok(request):
        return _deny()
    msgs = request.data.get("messages")
    if not isinstance(msgs, list) or not 0 < len(msgs) <= MAX_BATCH:
        return Response({"detail": f"messages must be a list of 1..{MAX_BATCH} objects."}, status=400)
    port = str(request.data.get("port") or "")
    return Response({"results": iot_ingest.ingest_batch(msgs, port)})


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def connection(request):
    if not _bridge_ok(request):
        return _deny()
    try:
        dev = iot_ingest.set_connection(request.data.get("device_id"), request.data.get("connected"),
                                        request.data.get("port", ""))
    except iot_ingest.Rejected as e:
        return Response({"detail": str(e)}, status=400)
    return Response({"device": iot_ingest.device_dict(dev)})


@api_view(["GET"])
def my_readings(request):
    if request.user.role != "patient":
        raise PermissionDenied("Only patients can do this.")
    return Response(iot_ingest.patient_feed(request.user))
