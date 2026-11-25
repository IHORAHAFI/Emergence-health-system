from django.conf import settings
from django.db import models
from django.utils.text import slugify


class Disease(models.Model):
    """Medical information entry. Entries are summaries of a cited public source and are
    only shown to patients after a healthcare professional has marked them as reviewed."""

    class Category(models.TextChoices):
        INFECTIOUS = "infectious", "Infectious diseases"
        CARDIOVASCULAR = "cardiovascular", "Cardiovascular diseases"
        RESPIRATORY = "respiratory", "Respiratory diseases"
        NEUROLOGICAL = "neurological", "Neurological diseases"
        INJURIES = "injuries", "Injuries and accidents"
        CHILDHOOD = "childhood", "Childhood diseases"
        METABOLIC = "metabolic", "Metabolic and chronic diseases"

    class Review(models.TextChoices):
        PENDING = "pending", "Pending clinical review"
        APPROVED = "approved", "Clinically reviewed"

    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    category = models.CharField(max_length=20, choices=Category.choices)
    description = models.TextField()
    causes = models.TextField(blank=True)
    symptoms = models.TextField(blank=True, help_text="Signs and symptoms, one per line")
    risk_factors = models.TextField(blank=True, help_text="One per line")
    diagnosis = models.TextField(blank=True)
    treatment = models.TextField(blank=True, help_text="General information only - never individual instructions")
    prevention = models.TextField(blank=True, help_text="One per line")
    warning_signs = models.TextField(blank=True, help_text="Emergency warning signs, one per line")
    source_name = models.CharField(max_length=120)
    source_url = models.URLField()
    review_status = models.CharField(max_length=10, choices=Review.choices, default=Review.PENDING)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:140]
        super().save(*args, **kwargs)

    @property
    def is_reviewed(self):
        return self.review_status == self.Review.APPROVED

    @staticmethod
    def lines(text):
        return [l.strip(" -•\t") for l in (text or "").splitlines() if l.strip(" -•\t")]

    def symptom_list(self):
        return self.lines(self.symptoms)

    def risk_list(self):
        return self.lines(self.risk_factors)

    def prevention_list(self):
        return self.lines(self.prevention)

    def warning_list(self):
        return self.lines(self.warning_signs)
