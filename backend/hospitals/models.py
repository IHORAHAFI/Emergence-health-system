from django.db import models


class Specialty(models.Model):
    """e.g. cardiology, trauma, maternity, pediatrics, burns, neurology."""
    code = models.SlugField(unique=True)
    name = models.CharField(max_length=100)

    def __str__(self):
        return self.name


class Hospital(models.Model):
    class Level(models.IntegerChoices):
        HEALTH_CENTER = 1, "Health center"
        DISTRICT = 2, "District hospital"
        PROVINCIAL = 3, "Provincial / referral hospital"
        NATIONAL = 4, "National referral / teaching hospital"

    name = models.CharField(max_length=200)
    address = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    emergency_phone = models.CharField(max_length=20, blank=True, help_text="Direct line of the emergency department")
    emergency_department_info = models.TextField(
        blank=True, help_text="Opening hours, entrance, triage arrangements - as provided by the hospital")
    latitude = models.FloatField()
    longitude = models.FloatField()
    level = models.IntegerField(choices=Level.choices, default=Level.DISTRICT)
    specialties = models.ManyToManyField(Specialty, blank=True, related_name="hospitals")
    total_beds = models.PositiveIntegerField(default=0)
    available_beds = models.PositiveIntegerField(default=0)
    icu_available = models.PositiveIntegerField(default=0)
    has_emergency_unit = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True, help_text="Hospital status: operating / closed")
    # Bed numbers in the demo data are simulated. Set True only when hospital staff
    # have confirmed the figures themselves.
    capacity_verified = models.BooleanField(
        default=False, help_text="True only when hospital staff have confirmed the capacity figures")
    capacity_updated_by = models.ForeignKey("accounts.User", null=True, blank=True,
                                            on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def occupancy_rate(self):
        if not self.total_beds:
            return 1.0
        return 1 - (self.available_beds / self.total_beds)
