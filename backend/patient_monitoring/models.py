from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class MonitoringConsent(models.Model):
    """Explicit patient consent to collect and share vital signs.
    scope "emergency": for ONE emergency (emergency set). scope "home": ongoing home monitoring (no emergency) -
    readings from the patient's own devices are checked automatically and may trigger AI check-ins / escalation."""

    class Scope(models.TextChoices):
        EMERGENCY = "emergency", "One emergency"
        HOME = "home", "Ongoing home monitoring"

    emergency = models.OneToOneField("emergency.EmergencyRequest", null=True, blank=True, on_delete=models.CASCADE,
                                     related_name="monitoring_consent")
    scope = models.CharField(max_length=10, choices=Scope.choices, default=Scope.EMERGENCY)
    patient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    consented_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    consent_text = models.TextField()

    @property
    def active(self):
        return self.revoked_at is None


class MedicalDevice(models.Model):
    class Kind(models.TextChoices):
        THERMOMETER = "thermometer", "Digital thermometer"
        PULSE_OXIMETER = "pulse_oximeter", "Pulse oximeter"
        BLOOD_PRESSURE = "blood_pressure", "Blood pressure monitor"
        HEART_RATE = "heart_rate", "Heart rate sensor"
        RESPIRATORY = "respiratory", "Respiratory rate sensor"
        ECG = "ecg", "Portable ECG"
        SIMULATOR = "simulator", "Simulated device (demo)"

    patient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="medical_devices")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    name = models.CharField(max_length=100, help_text="Name advertised by the device")
    device_identifier = models.CharField(max_length=128, help_text="Bluetooth device id (browser-scoped) or serial")
    manufacturer = models.CharField(max_length=100, blank=True)
    is_simulated = models.BooleanField(default=False)
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["patient", "device_identifier"], name="unique_device_per_patient")]

    def __str__(self):
        return f"{self.get_kind_display()} {self.name}{' (SIMULATED)' if self.is_simulated else ''}"


class MedicalTestRequest(models.Model):
    """A patient asks for one or more vital-sign tests; an operator runs them (IoT devices or simulation)."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending - waiting for operator"
        ACCEPTED = "accepted", "Accepted - operator has received the request"
        TESTING = "testing", "Testing - measurement in progress"
        COMPLETED = "completed", "Completed - results are available"
        CANCELLED = "cancelled", "Cancelled"

    OPEN = ("pending", "accepted", "testing")
    # code: (label, icon, unit, measured fields, virtual device kind)
    TESTS = {
        "temperature": ("Body Temperature", "bi-thermometer-half", "°C", ("temperature_c",), "thermometer"),
        "heart_rate": ("Heart Rate", "bi-heart-pulse", "BPM", ("heart_rate_bpm",), "heart_rate"),
        "spo2": ("Oxygen Saturation", "bi-lungs", "SpO₂ (%)", ("spo2_pct",), "pulse_oximeter"),
        "blood_pressure": ("Blood Pressure", "bi-activity", "mmHg", ("systolic_mmhg", "diastolic_mmhg"),
                           "blood_pressure"),
        "respiratory": ("Respiratory Rate", "bi-wind", "breaths/min", ("respiratory_rate",), "respiratory"),
    }

    reference = models.CharField(max_length=24, unique=True, editable=False)
    patient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="test_requests")
    tests = models.JSONField(help_text="List of test codes, e.g. ['temperature', 'spo2']")
    test_key = models.CharField(max_length=80, editable=False, help_text="Sorted test codes (duplicate check)")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    note = models.CharField(max_length=255, blank=True, help_text="Optional note from the patient")
    emergency = models.ForeignKey("emergency.EmergencyRequest", null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="test_requests", help_text="Patient's active emergency, if any")
    assigned_operator = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                          on_delete=models.SET_NULL, related_name="handled_test_requests")
    requested_at = models.DateTimeField(auto_now_add=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="+")
    # Operator alert workflow (server-side, per request): alert 1 on arrival, alerts 2 and 3 every ALERT_INTERVAL
    # while nobody has taken responsibility, then AI takeover one interval after alert 3 (see emergency/alert_workflow).
    alert_count = models.PositiveSmallIntegerField(default=0)
    last_alert_at = models.DateTimeField(null=True, blank=True)
    ai_takeover_at = models.DateTimeField(null=True, blank=True)
    ai_response = models.JSONField(default=dict, blank=True, help_text="What the AI takeover did (audit)")

    class Meta:
        ordering = ["-requested_at"]
        constraints = [models.UniqueConstraint(
            fields=["patient", "test_key"], condition=models.Q(status__in=["pending", "accepted", "testing"]),
            name="one_open_request_per_patient_and_tests")]

    def __str__(self):
        return f"{self.reference} {self.patient.username} {self.test_names} [{self.status}]"

    def save(self, *a, **kw):
        if not self.reference:
            import secrets
            from django.utils import timezone
            while True:
                ref = f"TST-{timezone.localdate():%Y%m%d}-{secrets.token_hex(2).upper()}"
                if not MedicalTestRequest.objects.filter(reference=ref).exists():
                    break
            self.reference = ref
        self.test_key = ",".join(sorted(self.tests))
        super().save(*a, **kw)

    @property
    def is_open(self):
        return self.status in self.OPEN

    @property
    def status_short(self):
        return self.get_status_display().split(" - ")[0]

    @property
    def is_complete_set(self):
        return set(self.tests) == set(self.TESTS)

    @property
    def test_names(self):
        if self.is_complete_set:
            return "Complete vital signs"
        return ", ".join(self.TESTS[t][0] for t in self.tests if t in self.TESTS)

    @property
    def fields(self):
        return [f for t in self.tests for f in self.TESTS[t][3]]

    @property
    def device_kinds(self):
        return [self.TESTS[t][4] for t in self.tests]

    @property
    def patient_code(self):
        return f"P-{self.patient_id:05d}"


class VitalSignReading(models.Model):
    class Source(models.TextChoices):
        BLUETOOTH = "bluetooth", "Bluetooth device"
        MANUAL = "manual", "Entered manually"
        SIMULATED = "simulated", "Simulated device (demo - not a real measurement)"

    class Consciousness(models.TextChoices):
        ALERT = "alert", "Alert"
        CONFUSED = "confused", "New confusion"
        VOICE = "voice", "Responds to voice"
        PAIN = "pain", "Responds to pain"
        UNRESPONSIVE = "unresponsive", "Unresponsive"

    emergency = models.ForeignKey("emergency.EmergencyRequest", null=True, blank=True, on_delete=models.CASCADE,
                                  related_name="vitals")
    test_request = models.ForeignKey(MedicalTestRequest, null=True, blank=True, on_delete=models.CASCADE,
                                     related_name="readings")
    patient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="vital_readings")
    device = models.ForeignKey(MedicalDevice, null=True, blank=True, on_delete=models.SET_NULL, related_name="readings")
    source = models.CharField(max_length=10, choices=Source.choices)
    entered_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    measured_at = models.DateTimeField(help_text="When the device took the measurement")
    received_at = models.DateTimeField(auto_now_add=True)
    # Measurements (all optional: a reading may contain only what one device measures).
    # Validators reject physiologically implausible values (likely device / typing errors).
    temperature_c = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True,
                                        validators=[MinValueValidator(25), MaxValueValidator(45)])
    heart_rate_bpm = models.PositiveSmallIntegerField(null=True, blank=True,
                                                      validators=[MinValueValidator(20), MaxValueValidator(250)])
    spo2_pct = models.PositiveSmallIntegerField(null=True, blank=True,
                                                validators=[MinValueValidator(50), MaxValueValidator(100)])
    systolic_mmhg = models.PositiveSmallIntegerField(null=True, blank=True,
                                                     validators=[MinValueValidator(50), MaxValueValidator(260)])
    diastolic_mmhg = models.PositiveSmallIntegerField(null=True, blank=True,
                                                      validators=[MinValueValidator(30), MaxValueValidator(160)])
    respiratory_rate = models.PositiveSmallIntegerField(null=True, blank=True,
                                                        validators=[MinValueValidator(4), MaxValueValidator(60)])
    on_supplemental_oxygen = models.BooleanField(default=False)
    consciousness = models.CharField(max_length=15, choices=Consciousness.choices, blank=True)
    ecg_rhythm = models.CharField(max_length=100, blank=True, help_text="Rhythm label reported by the ECG device")
    ecg_samples = models.JSONField(null=True, blank=True, help_text="Optional raw ECG samples (mV) from the device")
    ecg_sample_rate_hz = models.PositiveSmallIntegerField(null=True, blank=True)
    # Decision support output (NEWS2)
    news2_score = models.PositiveSmallIntegerField(null=True, blank=True)
    news2_partial = models.BooleanField(default=True, help_text="Some NEWS2 parameters were not measured")
    risk = models.CharField(max_length=12, blank=True)
    alerts = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["-measured_at"]
        indexes = [models.Index(fields=["emergency", "-measured_at"])]

    def __str__(self):
        ref = (self.emergency.reference if self.emergency_id else
               self.test_request.reference if self.test_request_id else "home")
        return f"{ref} @ {self.measured_at:%H:%M:%S} ({self.source})"

    @property
    def is_simulated(self):
        return self.source == self.Source.SIMULATED

    @property
    def risk_label(self):
        from .clinical import RISK_LABEL
        return RISK_LABEL.get(self.risk, self.risk)

    def as_dict(self):
        return {
            "id": self.id, "measured_at": self.measured_at.isoformat(), "received_at": self.received_at.isoformat(),
            "source": self.source, "source_display": self.get_source_display(), "simulated": self.is_simulated,
            "device": str(self.device) if self.device else None,
            "temperature_c": float(self.temperature_c) if self.temperature_c is not None else None,
            "heart_rate_bpm": self.heart_rate_bpm, "spo2_pct": self.spo2_pct,
            "systolic_mmhg": self.systolic_mmhg, "diastolic_mmhg": self.diastolic_mmhg,
            "respiratory_rate": self.respiratory_rate, "on_supplemental_oxygen": self.on_supplemental_oxygen,
            "consciousness": self.consciousness, "ecg_rhythm": self.ecg_rhythm,
            "has_ecg_trace": bool(self.ecg_samples),
            "news2_score": self.news2_score, "news2_partial": self.news2_partial, "risk": self.risk,
            "alerts": self.alerts, "reviews": [r.as_dict() for r in self.reviews.all()],
            "threshold_flags": self.threshold_flags,
        }

    @property
    def threshold_flags(self):
        from .services import threshold_flags
        return threshold_flags(self)


class ClinicalReview(models.Model):
    """A clinician confirming or overriding the automatic decision-support result."""

    class Decision(models.TextChoices):
        CONFIRM = "confirm", "Confirmed"
        OVERRIDE = "override", "Overridden"

    reading = models.ForeignKey(VitalSignReading, on_delete=models.CASCADE, related_name="reviews")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    decision = models.CharField(max_length=10, choices=Decision.choices)
    override_risk = models.CharField(max_length=12, blank=True)
    note = models.CharField(max_length=500, blank=True)
    reviewed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["reviewed_at"]

    def as_dict(self):
        return {"reviewer": self.reviewer.get_full_name() or self.reviewer.username, "decision": self.decision,
                "override_risk": self.override_risk, "note": self.note, "at": self.reviewed_at.isoformat()}


# ------------------------------------------------------------------ simulation (demo only)
class VirtualPatient(models.Model):
    """A fictional patient used for demonstrations. Never a real person."""
    patient_code = models.CharField(max_length=12, unique=True, editable=False)
    full_name = models.CharField(max_length=120)
    age = models.PositiveSmallIntegerField()
    gender = models.CharField(max_length=10, choices=[("female", "Female"), ("male", "Male"), ("other", "Other")])
    weight_kg = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    height_cm = models.PositiveSmallIntegerField(null=True, blank=True)
    medical_history = models.TextField(blank=True)
    # The virtual patient is represented in the emergency workflow by an inactive placeholder user account.
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="virtual_patient")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.patient_code} {self.full_name} (VIRTUAL)"

    def save(self, *a, **kw):
        if not self.patient_code:
            import secrets
            while True:
                code = f"VP-{secrets.token_hex(2).upper()}"
                if not VirtualPatient.objects.filter(patient_code=code).exists():
                    break
            self.patient_code = code
        super().save(*a, **kw)

    @property
    def bmi(self):
        if self.weight_kg and self.height_cm:
            return round(float(self.weight_kg) / (self.height_cm / 100) ** 2, 1)
        return None


class SimulationSession(models.Model):
    class Status(models.TextChoices):
        READY = "ready", "Ready"
        RUNNING = "running", "Running"
        PAUSED = "paused", "Paused"
        STOPPED = "stopped", "Stopped"

    virtual_patient = models.ForeignKey(VirtualPatient, null=True, blank=True, on_delete=models.CASCADE,
                                        related_name="sessions")
    emergency = models.ForeignKey("emergency.EmergencyRequest", null=True, blank=True, on_delete=models.CASCADE,
                                  related_name="simulations",
                                  help_text="Virtual-patient demo: always a SIMULATED emergency")
    test_request = models.OneToOneField(MedicalTestRequest, null=True, blank=True, on_delete=models.CASCADE,
                                        related_name="session",
                                        help_text="Patient test request: readings go to THIS request's patient")
    scenario = models.CharField(max_length=20)
    custom = models.JSONField(default=dict, blank=True, help_text="Targets for the custom scenario")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.READY)
    speed = models.FloatField(default=1.0, help_text="1.0 = one simulated minute every 2 real seconds")
    step = models.PositiveIntegerField(default=0, help_text="Simulated minutes since the start")
    current = models.JSONField(default=dict, blank=True, help_text="Latest simulated values")
    last_tick_at = models.DateTimeField(null=True, blank=True)
    started_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    stopped_at = models.DateTimeField(null=True, blank=True)

    BASE_TICK_SECONDS = 2.0

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        who = self.test_request.reference if self.test_request_id else self.virtual_patient.patient_code
        return f"Simulation {self.id} {who} {self.scenario} [{self.status}]"

    @property
    def tick_seconds(self):
        return self.BASE_TICK_SECONDS / max(self.speed, 0.1)


class VirtualDevice(models.Model):
    """A simulated IoT medical device belonging to one simulation session."""
    session = models.ForeignKey(SimulationSession, on_delete=models.CASCADE, related_name="devices")
    kind = models.CharField(max_length=20, choices=MedicalDevice.Kind.choices)
    device_id = models.CharField(max_length=24, help_text="Simulated device identifier, e.g. VTH-3F2A")
    name = models.CharField(max_length=60)
    enabled = models.BooleanField(default=True, help_text="Switched on")
    connected = models.BooleanField(default=True)
    battery_pct = models.FloatField(default=100.0)
    every_steps = models.PositiveSmallIntegerField(default=1, help_text="Transmits every N simulated minutes")
    last_values = models.JSONField(default=dict, blank=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)
    transmissions = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [models.UniqueConstraint(fields=["session", "kind"], name="one_virtual_device_kind_per_session")]

    def __str__(self):
        return f"{self.name} {self.device_id} (SIMULATED)"

    @property
    def state(self):
        if not self.enabled:
            return "off"
        if self.battery_pct <= 0:
            return "battery_empty"
        return "connected" if self.connected else "disconnected"


class SimulationEvent(models.Model):
    session = models.ForeignKey(SimulationSession, on_delete=models.CASCADE, related_name="events")
    at = models.DateTimeField(auto_now_add=True)
    step = models.PositiveIntegerField(default=0)
    level = models.CharField(max_length=10, default="info", choices=[("info", "Info"), ("warning", "Warning"),
                                                                     ("alert", "Alert"), ("error", "Error")])
    message = models.CharField(max_length=255)

    class Meta:
        ordering = ["-id"]


# ------------------------------------------------------------------ configurable alerts
class AlertThreshold(models.Model):
    """Demonstration warning limits, editable by administrators. NOT universal clinical rules."""
    class Group(models.TextChoices):
        ADULT = "adult", "Adult (16+ years)"
        CHILD = "child", "Child (1-15 years)"

    PARAMS = [("temperature_c", "Temperature (°C)"), ("heart_rate_bpm", "Heart rate (bpm)"),
              ("spo2_pct", "SpO₂ (%)"), ("systolic_mmhg", "Systolic BP (mmHg)"),
              ("diastolic_mmhg", "Diastolic BP (mmHg)"), ("respiratory_rate", "Respiratory rate (/min)")]
    parameter = models.CharField(max_length=20, choices=PARAMS)
    age_group = models.CharField(max_length=10, choices=Group.choices, default=Group.ADULT)
    low = models.FloatField(null=True, blank=True, help_text="Alert when the value is BELOW this")
    high = models.FloatField(null=True, blank=True, help_text="Alert when the value is ABOVE this")
    enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["age_group", "parameter"]
        constraints = [models.UniqueConstraint(fields=["parameter", "age_group"], name="one_threshold_per_param_group")]

    def __str__(self):
        return f"{self.get_parameter_display()} [{self.age_group}] <{self.low} >{self.high}"


class EmergencyAlert(models.Model):
    """One abnormal measurement that needs a human to look at it."""
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        ACKNOWLEDGED = "acknowledged", "Acknowledged"

    emergency = models.ForeignKey("emergency.EmergencyRequest", null=True, blank=True, on_delete=models.CASCADE,
                                  related_name="vital_alerts")
    test_request = models.ForeignKey(MedicalTestRequest, null=True, blank=True, on_delete=models.CASCADE,
                                     related_name="alerts")
    reading = models.ForeignKey(VitalSignReading, on_delete=models.CASCADE, related_name="threshold_alerts")
    device = models.ForeignKey(MedicalDevice, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    parameter = models.CharField(max_length=20)
    value = models.FloatField()
    unit = models.CharField(max_length=10)
    limit = models.CharField(max_length=40, help_text="Which threshold was crossed, e.g. '> 38.0'")
    message = models.CharField(max_length=255)
    is_simulated = models.BooleanField(default=False)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)
    acknowledged_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                        related_name="+")
    acknowledged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        ctx = self.emergency or self.test_request
        return f"{ctx.reference if ctx else 'home'} {self.message}"


class AccessAuditLog(models.Model):
    """Who viewed or changed patient health data, and when."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    emergency = models.ForeignKey("emergency.EmergencyRequest", null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="+")
    action = models.CharField(max_length=40)
    detail = models.CharField(max_length=255, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at"]


# ------------------------------------------------------------------ proactive assistant
class AssistantPreferences(models.Model):
    """Per-patient settings for the proactive / accessible assistant (set by the patient or a permitted caregiver)."""
    patient = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="assistant_prefs")
    language = models.CharField(max_length=5, default="en", help_text="en / fr / rw (speech support depends on the phone)")
    voice_rate = models.FloatField(default=0.45, help_text="Text-to-speech speed 0.1 (slow) - 1.0 (fast)")
    voice_assistant = models.BooleanField(default=True)
    spoken_responses = models.BooleanField(default=True)
    ai_voice_checkins = models.BooleanField(default=True, help_text="AI may start a spoken check-in when the app is open")
    voice_navigation = models.BooleanField(default=True)
    hands_free = models.BooleanField(default=False, help_text="Listen for help phrases while the app is open")
    fall_detection = models.BooleanField(default=False, help_text="Phone motion sensor, only while the app runs")
    text_scale = models.FloatField(default=1.0)
    high_contrast = models.BooleanField(default=False)
    simplified = models.BooleanField(default=False)
    checkins_enabled = models.BooleanField(default=False)
    checkin_times = models.JSONField(default=list, blank=True, help_text='Local times, e.g. ["08:00", "18:00"]')
    notify_caregivers = models.BooleanField(default=True)
    share_location_on_escalation = models.BooleanField(
        default=False, help_text="Patient consents that the last location the app shared is used for an automatic emergency")
    last_latitude = models.FloatField(null=True, blank=True)
    last_longitude = models.FloatField(null=True, blank=True)
    last_location_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Assistant preferences of {self.patient}"


class MonitoringEvent(models.Model):
    """Something that may need the patient's attention: a concerning home reading, a scheduled check-in, a possible
    fall, a symptom report (patient or caregiver). The AI asks the patient; no answer or a request for help escalates
    to humans. Every step is kept in `log` with its author: ai / patient / caregiver / device / system."""

    class Kind(models.TextChoices):
        VITAL_SIGNS = "vital_signs", "Concerning vital signs"
        CHECK_IN = "check_in", "Scheduled check-in"
        POSSIBLE_FALL = "possible_fall", "Possible fall (phone sensor)"
        PATIENT_REPORT = "patient_report", "Reported by the patient"
        CAREGIVER_REPORT = "caregiver_report", "Reported by a caregiver"
        CONTACT_REQUEST = "contact_request", "Patient asks to talk to a health professional"

    class Severity(models.TextChoices):
        INFO = "info", "Information"
        CONCERN = "concern", "Needs attention"
        URGENT = "urgent", "Urgent"
        SEVERE = "severe", "Severe - immediate human escalation"

    class Status(models.TextChoices):
        AWAITING = "awaiting_response", "Waiting for the patient"
        OK = "responded_ok", "Patient says they are OK"
        HELP = "needs_help", "Patient asked for help"
        NO_RESPONSE = "no_response", "No response"
        ESCALATED = "escalated", "Escalated to humans"
        AI_RESPONDED = "ai_responded", "AI responded - waiting for a human"
        CLOSED = "closed", "Closed"

    OPEN = ("awaiting_response", "needs_help", "no_response", "escalated", "ai_responded")
    # kinds that are requests for a human service (they enter the operator alert workflow)
    SERVICE_KINDS = ("contact_request", "patient_report", "caregiver_report")

    patient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="monitoring_events")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    severity = models.CharField(max_length=10, choices=Severity.choices, default=Severity.CONCERN)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.AWAITING)
    source = models.CharField(max_length=20, help_text="device / ai_schedule / phone_sensor / patient / caregiver")
    reported_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    is_simulated = models.BooleanField(default=False, help_text="Triggered by SIMULATED measurements")
    readings = models.ManyToManyField(VitalSignReading, blank=True, related_name="monitoring_events")
    reason = models.CharField(max_length=255)
    prompt = models.CharField(max_length=255, help_text="What the AI asks the patient")
    prompt_count = models.PositiveSmallIntegerField(default=0)
    last_prompt_at = models.DateTimeField(null=True, blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    response = models.JSONField(default=dict, blank=True, help_text="Last structured answer (answer, symptoms, pain...)")
    log = models.JSONField(default=list, blank=True)
    summary = models.TextField(blank=True, help_text="AI summary for operators / staff")
    emergency = models.ForeignKey("emergency.EmergencyRequest", null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="monitoring_events")
    escalated_at = models.DateTimeField(null=True, blank=True)
    staff_notified_at = models.DateTimeField(null=True, blank=True)
    caregivers_notified_at = models.DateTimeField(null=True, blank=True)
    dedupe_key = models.CharField(max_length=80, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    # Operator alert workflow (server-side, per request): alert 1 on arrival, alerts 2 and 3 every ALERT_INTERVAL
    # while nobody has taken responsibility, then AI takeover one interval after alert 3 (see emergency/alert_workflow).
    alert_count = models.PositiveSmallIntegerField(default=0)
    last_alert_at = models.DateTimeField(null=True, blank=True)
    ai_takeover_at = models.DateTimeField(null=True, blank=True)
    ai_response = models.JSONField(default=dict, blank=True, help_text="What the AI takeover did (audit)")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_kind_display()} - {self.patient} [{self.status}]"

    @property
    def is_open(self):
        return self.status in self.OPEN


class PatientMeasurement(models.Model):
    """One patient-initiated quick measurement from the mobile app (60-second workflows).

    heart_rate   phone camera PPG  -> status ESTIMATED (never "measured": a phone camera is not a medical device)
    temperature  BLE thermometer   -> status MEASURED  (value decoded from the thermometer's own measurement)
    FAILED / INCONCLUSIVE attempts are stored too (value always empty) so the operator sees that the patient tried.
    `client_id` is generated by the app before the first upload: a retry of the same measurement never creates a
    second row. The database is the source of truth; the WebSocket push only tells dashboards to re-fetch."""

    class Kind(models.TextChoices):
        HEART_RATE = "heart_rate", "Heart rate"
        TEMPERATURE = "temperature", "Body temperature"

    class Status(models.TextChoices):
        ESTIMATED = "estimated", "Estimated"
        MEASURED = "measured", "Measured"
        FAILED = "failed", "Failed"
        INCONCLUSIVE = "inconclusive", "Inconclusive"

    class Source(models.TextChoices):
        PHONE_CAMERA = "phone_camera", "Smartphone camera"
        BLE_THERMOMETER = "ble_thermometer", "Bluetooth thermometer"

    class Method(models.TextChoices):
        PPG = "ppg", "PPG"
        BLE = "ble", "BLE"

    VALID = (Status.ESTIMATED, Status.MEASURED)

    client_id = models.UUIDField(unique=True, help_text="Generated by the app; makes retries idempotent")
    patient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="quick_measurements")
    emergency = models.ForeignKey("emergency.EmergencyRequest", null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="quick_measurements")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    status = models.CharField(max_length=12, choices=Status.choices)
    value = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True,
                                help_text="Empty unless the status is estimated / measured")
    unit = models.CharField(max_length=5)
    source = models.CharField(max_length=16, choices=Source.choices)
    method = models.CharField(max_length=4, choices=Method.choices)
    device = models.JSONField(default=dict, blank=True, help_text="Phone / thermometer details reported by the app")
    quality = models.JSONField(default=dict, blank=True, help_text="Signal-quality figures (PPG) or device flags")
    failure_reason = models.CharField(max_length=200, blank=True)
    duration_s = models.FloatField(null=True, blank=True, help_text="Measurement duration reported by the app")
    measured_at = models.DateTimeField()
    received_at = models.DateTimeField(auto_now_add=True)
    dashboard_notified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-measured_at", "-id"]
        indexes = [models.Index(fields=["patient", "-measured_at"])]

    def __str__(self):
        return f"{self.get_kind_display()} {self.status} {self.value or ''} ({self.patient})"

    @property
    def is_valid(self):
        return self.status in self.VALID

    def as_dict(self):
        p = self.patient
        return {
            "id": self.id, "client_id": str(self.client_id), "kind": self.kind, "kind_display": self.get_kind_display(),
            "status": self.status, "status_display": self.get_status_display().upper(), "valid": self.is_valid,
            "value": float(self.value) if self.value is not None and self.is_valid else None, "unit": self.unit,
            "source": self.source, "source_display": self.get_source_display(),
            "method": self.method, "method_display": self.get_method_display(),
            "device": self.device, "quality": self.quality, "failure_reason": self.failure_reason,
            "duration_s": self.duration_s,
            "measured_at": self.measured_at.isoformat(), "received_at": self.received_at.isoformat(),
            "patient_id": p.id, "patient": p.get_full_name() or p.username,
            "emergency": self.emergency.reference if self.emergency_id else None,
            "emergency_id": self.emergency_id,
        }
