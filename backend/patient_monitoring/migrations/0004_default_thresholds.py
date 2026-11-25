from django.db import migrations

# Demonstration warning limits (editable in the app). NOT universal clinical decision rules.
ADULT = {"temperature_c": (None, 38.0), "heart_rate_bpm": (50, 120), "spo2_pct": (92, None),
         "systolic_mmhg": (90, 180), "diastolic_mmhg": (None, 120), "respiratory_rate": (10, 25)}
# Illustrative values for children 1-15 years; must be reviewed by a paediatric clinician before real use.
CHILD = {"temperature_c": (None, 38.0), "heart_rate_bpm": (60, 140), "spo2_pct": (92, None),
         "systolic_mmhg": (80, 140), "diastolic_mmhg": (None, 95), "respiratory_rate": (14, 35)}


def load(apps, schema_editor):
    T = apps.get_model("patient_monitoring", "AlertThreshold")
    for group, table in (("adult", ADULT), ("child", CHILD)):
        for p, (low, high) in table.items():
            T.objects.get_or_create(parameter=p, age_group=group, defaults={"low": low, "high": high})


class Migration(migrations.Migration):
    dependencies = [("patient_monitoring", "0003_alerts")]
    operations = [migrations.RunPython(load, migrations.RunPython.noop)]
