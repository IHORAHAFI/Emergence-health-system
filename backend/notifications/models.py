from django.conf import settings
from django.db import models


class Notification(models.Model):
    class Channel(models.TextChoices):
        IN_APP = "in_app", "In-app"
        PUSH = "push", "Push (FCM)"
        SMS = "sms", "SMS"

    class Delivery(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        SIMULATED = "simulated", "Simulated (not actually sent)"
        NOT_CONFIGURED = "not_configured", "Not sent - provider not configured"
        FAILED = "failed", "Failed"

    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.CASCADE, related_name="notifications")
    hospital = models.ForeignKey("hospitals.Hospital", null=True, blank=True,
                                 on_delete=models.CASCADE, related_name="notifications")
    emergency = models.ForeignKey("emergency.EmergencyRequest", null=True, blank=True,
                                  on_delete=models.CASCADE, related_name="notifications")
    test_request = models.ForeignKey("patient_monitoring.MedicalTestRequest", null=True, blank=True,
                                     on_delete=models.CASCADE, related_name="notifications")
    channel = models.CharField(max_length=10, choices=Channel.choices, default=Channel.IN_APP)
    title = models.CharField(max_length=200)
    message = models.TextField()
    # `delivered` is True only for in-app records and for messages a real provider accepted.
    delivered = models.BooleanField(default=False)
    delivery_status = models.CharField(max_length=15, choices=Delivery.choices, default=Delivery.PENDING)
    destination = models.CharField(max_length=255, blank=True, help_text="Phone number / device for SMS & push")
    provider = models.CharField(max_length=30, blank=True)
    error = models.CharField(max_length=500, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.channel}] {self.title}"
