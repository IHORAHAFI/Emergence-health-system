"""Vital Signs section of the patient app / portal (see patient_monitoring/home_vitals.py).

GET /api/vitals/mine/   the logged-in patient's own camera heart rate (CAMERA_MEASURED_ESTIMATE) and simulated
                        temperature / blood pressure (SIMULATED): latest values + history, newest first.
                        The patient comes from the token / session - a patient id is never accepted from the client.
"""
from rest_framework.decorators import api_view
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from patient_monitoring import home_vitals


@api_view(["GET"])
def my_vitals(request):
    if request.user.role != "patient":
        raise PermissionDenied("Only patients can open their own vital signs here.")
    home_vitals.tick_running(request.user)  # advance the demo simulator if no background command is running
    try:
        limit = max(1, min(int(request.query_params.get("limit", 30)), 200))
    except ValueError:
        limit = 30
    return Response(home_vitals.summary(request.user, limit=limit))
