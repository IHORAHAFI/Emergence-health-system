from django.db import migrations


def load(apps, schema_editor):
    from medical_rag.disease_library import load as load_entries
    load_entries(apps.get_model("medical_rag", "Disease"))


class Migration(migrations.Migration):
    """Loads the starter entries as 'Pending clinical review' (hidden from patients until approved)."""
    dependencies = [("medical_rag", "0001_initial")]
    operations = [migrations.RunPython(load, migrations.RunPython.noop)]
