from django.core.management.base import BaseCommand, CommandError

from accounts.models import User
from patient_monitoring import proactive, services


class Command(BaseCommand):
    help = ("DEVELOPMENT: send one SIMULATED home reading for a patient who switched on home monitoring "
            "(always labelled SIMULATED - never presented as a real measurement).")

    def add_arguments(self, p):
        p.add_argument("username")
        p.add_argument("--scenario", default="hypoxia", choices=list(services.SCENARIOS))

    def handle(self, *a, **o):
        try:
            patient = User.objects.get(username=o["username"], role="patient")
        except User.DoesNotExist:
            raise CommandError("No patient with that username.")
        values = services.simulate_values(o["scenario"], "all")
        try:
            r, ev = proactive.record_home_reading(
                patient, values, "simulated",
                {"device_identifier": "SIM-home", "name": "Virtual home monitor", "kind": "simulator",
                 "manufacturer": "simulation"})
        except services.ReadingRejected as e:
            raise CommandError(str(e))
        self.stdout.write(f"SIMULATED reading stored: {services.vitals_text(r)}")
        self.stdout.write(f"Monitoring event: #{ev.pk} {ev.severity} {ev.status}" if ev else "No event (not concerning).")
