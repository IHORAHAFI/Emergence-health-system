from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        PATIENT = "patient", "Patient"
        PARAMEDIC = "paramedic", "Ambulance personnel"
        HOSPITAL = "hospital", "Healthcare professional"
        OPERATOR = "operator", "Emergency operator"
        ADMIN = "admin", "Administrator"

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.PATIENT)
    phone = models.CharField(max_length=20, blank=True)
    fcm_token = models.CharField(max_length=255, blank=True)
    hospital = models.ForeignKey(
        "hospitals.Hospital", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="staff", help_text="For healthcare professional accounts",
    )
    # Healthcare professionals who self-register must be approved by an administrator
    # before they can see any patient information.
    is_approved = models.BooleanField(default=True)
    professional_id = models.CharField(max_length=50, blank=True,
                                       help_text="Licence / staff number for healthcare professionals")

    @property
    def is_staff_role(self):
        return self.role in {self.Role.OPERATOR, self.Role.ADMIN, self.Role.HOSPITAL}

    @property
    def is_admin_role(self):
        return self.role == self.Role.ADMIN or self.is_superuser


class PatientProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="patient_profile")
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=10, blank=True)
    blood_group = models.CharField(max_length=5, blank=True)
    allergies = models.TextField(blank=True)
    chronic_conditions = models.TextField(blank=True)
    emergency_contact_name = models.CharField(max_length=100, blank=True)
    emergency_contact_phone = models.CharField(max_length=20, blank=True)
    emergency_contact_relationship = models.CharField(max_length=50, blank=True)
    home_address = models.CharField(max_length=255, blank=True)
    notify_emergency_contact = models.BooleanField(
        default=False, help_text="Patient consents to an SMS being sent to the emergency contact")

    def __str__(self):
        return f"Profile of {self.user.username}"

    @property
    def age(self):
        if not self.date_of_birth:
            return None
        from datetime import date
        d = date.today()
        b = self.date_of_birth
        return d.year - b.year - ((d.month, d.day) < (b.month, b.day))
