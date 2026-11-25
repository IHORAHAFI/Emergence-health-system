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


class StatusLog(models.Model):
    emergency = models.ForeignKey(EmergencyRequest, on_delete=models.CASCADE, related_name="logs")
    status = models.CharField(max_length=20)
    note = models.CharField(max_length=255, blank=True)
    by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["at"]
