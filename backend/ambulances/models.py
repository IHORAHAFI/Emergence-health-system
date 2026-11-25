from django.conf import settings
from django.db import models


class Ambulance(models.Model):
    class Status(models.TextChoices):
        AVAILABLE = "available", "Available"
        ASSIGNED = "assigned", "Assigned (reserved for an emergency)"
        DISPATCHED = "dispatched", "Dispatched (crew notified)"
        ON_THE_WAY = "on_the_way", "On the way to patient"
        ARRIVED = "arrived", "Arrived at patient"
        BUSY = "busy", "Transporting patient"
        OFFLINE = "offline", "Out of service"

    class Type(models.TextChoices):
        BLS = "bls", "Basic ambulance (Basic Life Support)"
        ALS = "als", "Advanced ambulance (Advanced Life Support)"

    plate_number = models.CharField(max_length=20, unique=True, help_text="Ambulance identification number")
    ambulance_type = models.CharField(max_length=5, choices=Type.choices, default=Type.BLS)
    base_hospital = models.ForeignKey("hospitals.Hospital", null=True, blank=True,
                                      on_delete=models.SET_NULL, related_name="ambulances")
    driver = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name="ambulance",
                                  help_text="Responsible crew member (paramedic account)")
    contact_phone = models.CharField(max_length=20, blank=True)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.AVAILABLE)
    latitude = models.FloatField()
    longitude = models.FloatField()
    # True while the position comes from the demo simulator, not from a real GPS device.
    location_is_simulated = models.BooleanField(default=True)
    last_location_update = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["plate_number"]

    def __str__(self):
        return f"{self.plate_number} ({self.get_status_display()})"

    # Simple labels for dashboards; the detailed 7-step status is still stored.
    SIMPLE = {"available": ("Available", "success"), "assigned": ("Assigned", "warning"),
              "dispatched": ("Assigned", "warning"), "on_the_way": ("En route", "primary"),
              "arrived": ("On scene", "info"), "busy": ("En route to hospital", "primary"),
              "offline": ("Unavailable", "secondary")}

    @property
    def simple_status(self):
        return self.SIMPLE.get(self.status, (self.get_status_display(), "secondary"))[0]

    @property
    def simple_color(self):
        return self.SIMPLE.get(self.status, ("", "secondary"))[1]


class AmbulanceAssignment(models.Model):
    """Dispatch record. The partial unique constraints make it impossible at the
    DATABASE level for one ambulance to hold two open assignments at once."""

    class Outcome(models.TextChoices):
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"
        RELEASED = "released", "Released by operator"

    ambulance = models.ForeignKey(Ambulance, on_delete=models.CASCADE, related_name="assignments")
    emergency = models.ForeignKey("emergency.EmergencyRequest", on_delete=models.CASCADE, related_name="assignments")
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    assigned_at = models.DateTimeField(auto_now_add=True)
    distance_km = models.FloatField(null=True, blank=True)
    eta_minutes = models.FloatField(null=True, blank=True)
    start_latitude = models.FloatField()
    start_longitude = models.FloatField()
    released_at = models.DateTimeField(null=True, blank=True)
    outcome = models.CharField(max_length=10, choices=Outcome.choices, default=Outcome.ACTIVE)

    class Meta:
        ordering = ["-assigned_at"]
        constraints = [
            models.UniqueConstraint(fields=["ambulance"], condition=models.Q(released_at__isnull=True),
                                    name="one_open_assignment_per_ambulance"),
            models.UniqueConstraint(fields=["emergency"], condition=models.Q(released_at__isnull=True),
                                    name="one_open_assignment_per_emergency"),
        ]

    def __str__(self):
        return f"{self.ambulance.plate_number} -> {self.emergency.reference} ({self.outcome})"
