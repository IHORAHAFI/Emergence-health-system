"""SIMULATED ambulance journey API (session or JWT auth).

GET  /api/journeys/                         open journeys the user may see (current positions)
GET  /api/journeys/<id>/                    one journey (advanced to "now" first)
GET  /api/journeys/by-emergency/<ref>/      latest journey of an emergency (null when none)
POST /api/journeys/start/      {emergency}  operator: create the journey (409 if one already exists)
POST /api/journeys/<id>/action/ {action}    operator: pause | resume | reset | retry | cancel
POST /api/journeys/<id>/pickup-location/ {latitude, longitude}   operator: confirm the patient's position

Progress is never written by clients: the server computes it from the stored road route and time.
"""
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from emergency import journey
from emergency.models import EmergencyRequest
from patient_monitoring.services import can_view

J = journey.J


def _get(request, pk):
    return get_object_or_404(journey.visible_to(request.user), pk=pk)


def _out(j):
    return journey.state(journey.advance(j))


@api_view(["GET"])
def journeys(request):
    qs = journey.visible_to(request.user).filter(is_open=True)
    return Response([_out(j) for j in qs[:50]])


@api_view(["GET"])
def journey_detail(request, pk):
    return Response(_out(_get(request, pk)))


@api_view(["GET"])
def journey_for_emergency(request, ref):
    em = get_object_or_404(EmergencyRequest.objects.select_related("assigned_ambulance"), reference=ref)
    if not can_view(request.user, em):
        return Response({"detail": "Not allowed."}, status=403)
    j = journey.visible_to(request.user).filter(emergency=em).first()
    return Response(_out(j) if j else None)


def _operator(request):
    if not journey.can_control(request.user):
        return Response({"detail": "Only operators can control the simulation."}, status=403)
    return None


@api_view(["POST"])
def journey_start(request):
    if (denied := _operator(request)):
        return denied
    em = get_object_or_404(EmergencyRequest, reference=request.data.get("emergency", ""))
    try:
        j = journey.create_for(em, request.user)
    except journey.JourneyError as e:
        existing = em.journeys.filter(is_open=True).first()
        code = status.HTTP_409_CONFLICT if existing else status.HTTP_400_BAD_REQUEST
        return Response({"detail": str(e), "journey": existing and existing.pk}, status=code)
    return Response(_out(j), status=201)


@api_view(["POST"])
def journey_action(request, pk):
    if (denied := _operator(request)):
        return denied
    j = _get(request, pk)
    try:
        j = journey.act(j, request.data.get("action", ""), request.user)
    except journey.JourneyError as e:
        return Response({"detail": str(e)}, status=400)
    return Response(journey.state(j))


@api_view(["POST"])
def journey_pickup_location(request, pk):
    if (denied := _operator(request)):
        return denied
    j = _get(request, pk)
    try:
        lat, lng = float(request.data["latitude"]), float(request.data["longitude"])
        j = journey.set_pickup_location(j, lat, lng, request.user)
    except (KeyError, TypeError, ValueError):
        return Response({"detail": "latitude and longitude are required numbers."}, status=400)
    except journey.JourneyError as e:
        return Response({"detail": str(e)}, status=400)
    return Response(journey.state(j))
