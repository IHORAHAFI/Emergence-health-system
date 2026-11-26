import secrets

from django.conf import settings
from django.db import models


class EmergencyRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ASSIGNED = "assigned", "Ambulance assigned"
        EN_ROUTE = "en_route", "Ambulance en route"
        ON_SCENE = "on_scene", "Ambulance on scene"
        TRANSPORTING = "transporting", "Transporting to hospital"
        ARRIVED = "arrived", "Arrived at hospital"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    class Type(models.TextChoices):
        CARDIAC = "cardiac", "Cardiac / chest pain"
        TRAUMA = "trauma", "Accident / trauma"
        RESPIRATORY = "respiratory", "Breathing problem"
        STROKE = "stroke", "Stroke"
        MATERNITY = "maternity", "Pregnancy / labour"
        BURNS = "burns", "Burns"
        POISONING = "poisoning", "Poisoning"
        OTHER = "other", "Other"

    PRIORITY_CHOICES = [("low", "Low"), ("medium", "Medium"), ("high", "High"), ("critical", "Critical")]

    class LocationSource(models.TextChoices):
        GPS = "gps", "Device GPS (patient consented)"
        MANUAL = "manual", "Selected manually on map"
        SIMULATED = "simulated", "Simulated (demo)"
        NONE = "none", "Location not available"  # e.g. automatic alert without a shared location - never invented

    reference = models.CharField(max_length=24, unique=True, editable=False,
                                 help_text="Unique emergency reference number, e.g. EMG-20261122-7F3A2C")
    patient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="emergencies")
    emergency_type = models.CharField(max_length=20, choices=Type.choices, default=Type.OTHER)
    description = models.TextField(blank=True)
    contact_phone = models.CharField(max_length=20, blank=True)
    latitude = models.FloatField()
    longitude = models.FloatField()
    location_accuracy_m = models.FloatField(null=True, blank=True)
    location_source = models.CharField(max_length=10, choices=LocationSource.choices, default=LocationSource.GPS)
    location_consent = models.BooleanField(default=False, help_text="Patient explicitly agreed to share this location")
    location_updated_at = models.DateTimeField(null=True, blank=True,
                                               help_text="When the patient's latest location was received")
    location_description = models.CharField(max_length=255, blank=True,
                                            help_text="Landmark / address typed by the patient")
    triage_method = models.CharField(max_length=20, default="ml_model",
                                     help_text="ml_model, rule_fallback (AI unavailable) or operator")
    # symptom answers used by the AI
    conscious = models.BooleanField(default=True)
    breathing_difficulty = models.BooleanField(default=False)
    severe_bleeding = models.BooleanField(default=False)
    chest_pain = models.BooleanField(default=False)
    pain_level = models.PositiveSmallIntegerField(default=0)
    pregnant = models.BooleanField(default=False)
    age = models.PositiveSmallIntegerField(null=True, blank=True)
    # who needs help + what the caller reports (stored as reported, never as a diagnosis)
    class Condition(models.TextChoices):
        STABLE = "stable", "Awake, talking, stable"
        WORSENING = "worsening", "Getting worse"
        CONFUSED = "confused", "Confused or drowsy"
        UNRESPONSIVE = "unresponsive", "Not responding"
        UNKNOWN = "unknown", "Not sure"

    patient_name = models.CharField(max_length=120, blank=True, help_text="Name of the person who needs help")
    gender = models.CharField(max_length=10, blank=True)
    symptoms = models.TextField(blank=True, help_text="Signs and symptoms as reported by the caller")
    patient_condition = models.CharField(max_length=15, choices=Condition.choices, default=Condition.UNKNOWN)
    seizure = models.BooleanField(default=False)
    head_injury = models.BooleanField(default=False)
    request_ambulance = models.BooleanField(default=True)
    request_medical_advice = models.BooleanField(default=False,
                                                 help_text="Caller also wants advice from a medical professional")
    # AI output
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default="medium")
    ai_confidence = models.FloatField(null=True, blank=True)
    priority_overridden = models.BooleanField(default=False)
    recommendations = models.JSONField(default=list, blank=True)
    # dispatch
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    assigned_hospital = models.ForeignKey("hospitals.Hospital", null=True, blank=True,
                                          on_delete=models.SET_NULL, related_name="incoming_emergencies")
    assigned_ambulance = models.ForeignKey("ambulances.Ambulance", null=True, blank=True,
                                           on_delete=models.SET_NULL, related_name="emergencies")
    ambulance_distance_km = models.FloatField(null=True, blank=True)
    eta_minutes = models.FloatField(null=True, blank=True)
    is_simulated = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    # Operator acknowledgement: the dashboard siren rings until an operator accepts (or the request is closed).
    accepted_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="accepted_emergencies")
    # Siren schedule (server-side, so a browser refresh never resets it): alert 1 on arrival, then one every
    # ALERT_INTERVAL until an operator accepts. After ALERT_ESCALATE_AFTER alerts the AI escalation runs once.
    alert_count = models.PositiveSmallIntegerField(default=0)
    last_alert_at = models.DateTimeField(null=True, blank=True)
    escalated_at = models.DateTimeField(null=True, blank=True)
    escalation_reason = models.CharField(max_length=20, blank=True, help_text="severe_symptoms or no_operator")
    escalation = models.JSONField(default=dict, blank=True, help_text="What the AI escalation did (audit)")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.reference or self.id} {self.get_emergency_type_display()} [{self.priority}] - {self.get_status_display()}"

    @property
    def operator_label(self):
        """PENDING / ACCEPTED / IN PROGRESS / COMPLETED (or CANCELLED) as shown to operators."""
        if self.status == "cancelled":
            return "CANCELLED"
        if self.status == "completed":
            return "COMPLETED"
        if not self.accepted_at:
            return "PENDING"
        return "ACCEPTED" if self.status in ("pending", "assigned") else "IN PROGRESS"

    def save(self, *args, **kwargs):
        if not self.reference:
            from django.utils import timezone
            while True:
                ref = f"EMG-{timezone.localdate():%Y%m%d}-{secrets.token_hex(3).upper()}"
                if not EmergencyRequest.objects.filter(reference=ref).exists():
                    break
            self.reference = ref
        super().save(*args, **kwargs)

    @property
    def is_active(self):
        return self.status not in (self.Status.COMPLETED, self.Status.CANCELLED)


class EmergencyLocation(models.Model):
    """Location history of one emergency (audit + route). A row is written only when the patient really moved,
    so a phone sending the same position every few seconds does not fill the table."""
    emergency = models.ForeignKey(EmergencyRequest, on_delete=models.CASCADE, related_name="location_history")
    latitude = models.FloatField()
    longitude = models.FloatField()
    accuracy_m = models.FloatField(null=True, blank=True)
    source = models.CharField(max_length=10, choices=EmergencyRequest.LocationSource.choices)
    recorded_at = models.DateTimeField(help_text="Time the device measured the position")
    received_at = models.DateTimeField(auto_now_add=True)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-recorded_at"]


class StatusLog(models.Model):
    emergency = models.ForeignKey(EmergencyRequest, on_delete=models.CASCADE, related_name="logs")
    status = models.CharField(max_length=20)
    note = models.CharField(max_length=255, blank=True)
    by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["at"]


class AmbulanceJourney(models.Model):
    """SIMULATED ambulance journey: hospital -> patient (road route) -> pickup -> hospital (road route).

    The server is the source of truth. The ambulance position is computed from the stored road route and the time
    spent in the current leg (see emergency/journey.py), so a browser refresh, a reconnect or a server restart
    continues from the same place and no competing movement loops can exist. Never a real vehicle position."""

    class Status(models.TextChoices):
        WAITING_LOCATION = "WAITING_LOCATION", "Waiting for a valid patient location"
        ROUTING = "ROUTING", "Calculating road route"
        EN_ROUTE_TO_PATIENT = "EN_ROUTE_TO_PATIENT", "En route to patient"
        ARRIVED_AT_PATIENT = "ARRIVED_AT_PATIENT", "Arrived at patient"
        PICKING_UP_PATIENT = "PICKING_UP_PATIENT", "Picking up patient"
        PATIENT_ON_BOARD = "PATIENT_ON_BOARD", "Patient on board"
        EN_ROUTE_TO_HOSPITAL = "EN_ROUTE_TO_HOSPITAL", "Transporting patient to hospital"
        ARRIVED_AT_HOSPITAL = "ARRIVED_AT_HOSPITAL", "Arrived at hospital (journey completed)"
        FAILED = "FAILED", "Stopped - needs operator attention"
        CANCELLED = "CANCELLED", "Cancelled"

    emergency = models.ForeignKey(EmergencyRequest, on_delete=models.CASCADE, related_name="journeys")
    patient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    ambulance = models.ForeignKey("ambulances.Ambulance", on_delete=models.CASCADE, related_name="journeys")
    start_hospital = models.ForeignKey("hospitals.Hospital", null=True, blank=True, on_delete=models.SET_NULL,
                                       related_name="+")
    destination_hospital = models.ForeignKey("hospitals.Hospital", null=True, blank=True, on_delete=models.SET_NULL,
                                             related_name="+")
    start_latitude = models.FloatField(null=True, blank=True)
    start_longitude = models.FloatField(null=True, blank=True)
    pickup_latitude = models.FloatField(null=True, blank=True)
    pickup_longitude = models.FloatField(null=True, blank=True)
    destination_latitude = models.FloatField(null=True, blank=True)
    destination_longitude = models.FloatField(null=True, blank=True)

    status = models.CharField(max_length=24, choices=Status.choices, default=Status.ROUTING)
    is_open = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=0, help_text="Optimistic lock: every state change increments it")
    error = models.CharField(max_length=255, blank=True)

    current_latitude = models.FloatField(null=True, blank=True)
    current_longitude = models.FloatField(null=True, blank=True)
    progress = models.FloatField(default=0, help_text="0..1 along the current leg's road route")
    route_to_patient = models.JSONField(default=dict, blank=True)
    route_to_hospital = models.JSONField(default=dict, blank=True)
    routing_source = models.CharField(max_length=40, blank=True)

    simulation_mode = models.CharField(max_length=12, default="simulated", editable=False)
    sim_speedup = models.FloatField(default=10, help_text="Simulated movement is this many times faster than the "
                                                          "routing service's real-world travel estimate")
    pickup_seconds = models.PositiveIntegerField(default=20)
    phase_started_at = models.DateTimeField(null=True, blank=True)
    paused_at = models.DateTimeField(null=True, blank=True)
    paused_seconds = models.FloatField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    arrived_patient_at = models.DateTimeField(null=True, blank=True)
    pickup_completed_at = models.DateTimeField(null=True, blank=True)
    arrived_hospital_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["emergency"], condition=models.Q(is_open=True),
                                    name="one_open_journey_per_emergency"),
            models.UniqueConstraint(fields=["ambulance"], condition=models.Q(is_open=True),
                                    name="one_open_journey_per_ambulance"),
        ]

    def __str__(self):
        return f"Journey {self.pk} {self.ambulance_id} -> {self.emergency_id} [{self.status}] (SIMULATED)"
