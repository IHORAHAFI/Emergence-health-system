"""Adds the emergency reference number (filled in for existing rows), location consent/source
fields, contact phone and triage method. Non-destructive: existing data is preserved.

(Reconstructed from the compiled migration that was already applied to db.sqlite3.)
"""
import secrets

from django.db import migrations, models


def fill_references(apps, schema_editor):
    EmergencyRequest = apps.get_model("emergency", "EmergencyRequest")
    used = set()
    for em in EmergencyRequest.objects.filter(reference__isnull=True):
        while True:
            ref = f"EMG-{em.created_at:%Y%m%d}-{secrets.token_hex(3).upper()}"
            if ref not in used and not EmergencyRequest.objects.filter(reference=ref).exists():
                break
        used.add(ref)
        em.reference = ref
        em.save(update_fields=["reference"])


class Migration(migrations.Migration):

    dependencies = [("emergency", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="emergencyrequest", name="reference",
            field=models.CharField(max_length=24, null=True, editable=False),
        ),
        migrations.RunPython(fill_references, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="emergencyrequest", name="reference",
            field=models.CharField(max_length=24, unique=True, editable=False,
                                   help_text="Unique emergency reference number, e.g. EMG-20261122-7F3A2C"),
        ),
        migrations.AddField(
            model_name="emergencyrequest", name="location_source",
            field=models.CharField(max_length=10, default="gps", choices=[
                ("gps", "Device GPS (patient consented)"), ("manual", "Selected manually on map"),
                ("simulated", "Simulated (demo)")]),
        ),
        migrations.AddField(
            model_name="emergencyrequest", name="location_consent",
            field=models.BooleanField(default=False, help_text="Patient explicitly agreed to share this location"),
        ),
        migrations.AddField(
            model_name="emergencyrequest", name="location_description",
            field=models.CharField(max_length=255, blank=True, help_text="Landmark / address typed by the patient"),
        ),
        migrations.AddField(
            model_name="emergencyrequest", name="contact_phone",
            field=models.CharField(max_length=20, blank=True),
        ),
        migrations.AddField(
            model_name="emergencyrequest", name="triage_method",
            field=models.CharField(max_length=20, default="ml_model",
                                   help_text="ml_model, rule_fallback (AI unavailable) or operator"),
        ),
    ]
