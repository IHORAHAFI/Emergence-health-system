"""Simulation control panel (administrators and emergency operators only)."""
import json

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from emergency.models import EmergencyRequest
from . import simulation
from .models import SimulationSession, VirtualDevice, VirtualPatient


def operator_required(view):
    @login_required
    def wrapper(request, *a, **kw):
        u = request.user
        if not (u.is_superuser or u.role in ("admin", "operator")):
            return HttpResponseForbidden("The simulator is for administrators and emergency operators.")
        return view(request, *a, **kw)
    return wrapper


class VirtualPatientForm(forms.ModelForm):
    class Meta:
        model = VirtualPatient
        fields = ("full_name", "age", "gender", "weight_kg", "height_cm", "medical_history")
        widgets = {"medical_history": forms.Textarea(attrs={"rows": 2})}
        labels = {"weight_kg": "Weight (kg)", "height_cm": "Height (cm)"}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        for f in self.fields.values():
            f.widget.attrs.setdefault("class", "form-select" if isinstance(f.widget, forms.Select) else "form-control")

    def clean_age(self):
        v = self.cleaned_data["age"]
        if not 0 <= v <= 110:
            raise forms.ValidationError("Age must be between 0 and 110.")
        return v


VITAL_LABELS = [("temperature_c", "Temperature (°C)"), ("heart_rate_bpm", "Heart rate (bpm)"),
                ("spo2_pct", "SpO₂ (%)"), ("systolic_mmhg", "Systolic (mmHg)"),
                ("diastolic_mmhg", "Diastolic (mmHg)"), ("respiratory_rate", "Respiratory rate (/min)")]


@operator_required
def simulator_home(request):
    form = VirtualPatientForm(request.POST or None)
    if request.method == "POST":
        scenario = request.POST.get("scenario", "normal")
        vp_id = request.POST.get("virtual_patient")
        try:
            if vp_id:
                vp = get_object_or_404(VirtualPatient, pk=vp_id)
            elif form.is_valid():
                vp = simulation.create_virtual_patient(form.cleaned_data, request.user)
            else:
                raise ValueError("Please correct the virtual patient details.")
            custom = {}
            if scenario == "custom":
                custom = {k: request.POST.get(f"custom_{k}") for k, _ in VITAL_LABELS}
                custom = {k: v for k, v in custom.items() if v not in (None, "")}
                if not custom:
                    raise ValueError("Enter at least one target value for the custom scenario.")
                custom.update(ramp=int(request.POST.get("custom_ramp") or 10),
                              sudden=request.POST.get("custom_sudden") == "on")
            em = None
            if request.POST.get("emergency"):
                em = get_object_or_404(EmergencyRequest, pk=request.POST["emergency"])
            s = simulation.create_session(vp, scenario, request.user, emergency=em, custom=custom,
                                          speed=float(request.POST.get("speed") or 1))
            messages.success(request, f"Simulation ready for {vp.full_name} on emergency {s.emergency.reference}. "
                                      f"Press Start.")
            return redirect("sim_panel", pk=s.id)
        except ValueError as e:
            messages.error(request, str(e))
    return render(request, "monitoring/simulator_home.html", {
        "form": form, "scenarios": simulation.SCENARIOS, "vital_labels": VITAL_LABELS,
        "patients": VirtualPatient.objects.all()[:50],
        "sessions": SimulationSession.objects.filter(test_request__isnull=True).select_related(
            "virtual_patient", "emergency")[:30],
        "sim_emergencies": EmergencyRequest.objects.filter(is_simulated=True).exclude(
            status__in=["completed", "cancelled"])[:30],
    })


@operator_required
def panel(request, pk):
    s = get_object_or_404(SimulationSession.objects.select_related(
        "virtual_patient", "emergency", "test_request__patient"), pk=pk)
    return render(request, "monitoring/simulator_panel.html", {
        "s": s, "vp": s.virtual_patient, "req": s.test_request, "scenario": simulation.SCENARIOS[s.scenario],
        "state_json": simulation.state(s), "vital_labels": VITAL_LABELS})


def _json_body(request):
    try:
        return json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return {}


@operator_required
def state(request, pk):
    s = get_object_or_404(SimulationSession, pk=pk)
    return JsonResponse(simulation.state(s))


@operator_required
@require_POST
def tick(request, pk):
    """Called by the control panel about once a second while it is open."""
    s = get_object_or_404(SimulationSession.objects.select_related("emergency", "started_by"), pk=pk)
    simulation.tick(s)
    s.refresh_from_db()
    return JsonResponse(simulation.state(s))


@operator_required
@require_POST
def control(request, pk):
    s = get_object_or_404(SimulationSession, pk=pk)
    body = _json_body(request)
    try:
        if body.get("action") == "speed":
            simulation.set_speed(s, body.get("speed", 1))
        else:
            simulation.control(s, body.get("action", ""), request.user)
    except (ValueError, TypeError) as e:
        return JsonResponse({"detail": str(e)}, status=400)
    s.refresh_from_db()
    return JsonResponse(simulation.state(s))


@operator_required
@require_POST
def device(request, pk, device_pk):
    d = get_object_or_404(VirtualDevice.objects.select_related("session"), pk=device_pk, session_id=pk)
    try:
        simulation.device_action(d, _json_body(request).get("action", ""))
    except ValueError as e:
        return JsonResponse({"detail": str(e)}, status=400)
    return JsonResponse(simulation.state(d.session))
