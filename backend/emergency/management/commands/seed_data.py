"""Seed demo data (Kigali, Rwanda). Coordinates are approximate and for demo only."""
from django.core.management.base import BaseCommand

from accounts.models import PatientProfile, User
from ambulances.models import Ambulance
from hospitals.models import Hospital, Specialty

SPECIALTIES = ["general", "cardiology", "trauma", "pulmonology", "neurology", "maternity",
               "burns", "toxicology", "pediatrics"]

HOSPITALS = [
    # name, lat, lng, level, beds, free, icu, specialties
    ("CHUK - University Teaching Hospital of Kigali", -1.9564, 30.0601, 4, 500, 25, 4,
     ["general", "cardiology", "trauma", "neurology", "burns", "toxicology", "pulmonology"]),
    ("King Faisal Hospital", -1.9441, 30.0937, 4, 160, 12, 3, ["general", "cardiology", "neurology", "pulmonology"]),
    ("Rwanda Military Hospital", -1.9648, 30.1306, 4, 300, 18, 3, ["general", "trauma", "burns", "maternity"]),
    ("Kibagabaga District Hospital", -1.9306, 30.1137, 2, 150, 10, 1, ["general", "maternity", "pediatrics"]),
    ("Muhima District Hospital", -1.9465, 30.0560, 2, 120, 8, 0, ["general", "maternity"]),
    ("Masaka District Hospital", -1.9989, 30.1800, 2, 140, 9, 1, ["general", "maternity", "trauma"]),
    ("Kacyiru Police Hospital", -1.9440, 30.0880, 3, 100, 6, 1, ["general", "trauma"]),
]


class Command(BaseCommand):
    help = "Create demo hospitals, ambulances and users"

    def handle(self, *a, **kw):
        specs = {c: Specialty.objects.get_or_create(code=c, defaults={"name": c.title()})[0] for c in SPECIALTIES}
        hospitals = []
        for name, lat, lng, lvl, beds, free, icu, sp in HOSPITALS:
            h, _ = Hospital.objects.update_or_create(name=name, defaults=dict(
                latitude=lat, longitude=lng, level=lvl, total_beds=beds, available_beds=free,
                icu_available=icu, phone="+250788000000"))
            h.specialties.set([specs[s] for s in sp])
            hospitals.append(h)

        def mk(username, role, pw, **extra):
            u, created = User.objects.get_or_create(username=username, defaults={"role": role, **extra})
            if created:
                u.set_password(pw)
                u.save()
            return u

        admin = mk("admin", "admin", "admin12345", is_staff=True, is_superuser=True, first_name="System")
        mk("operator", "operator", "operator123", first_name="Emergency", last_name="Operator")
        mk("chuk_staff", "hospital", "hospital123", hospital=hospitals[0], first_name="CHUK", last_name="ER")
        p = mk("patient1", "patient", "patient123", first_name="Jean", last_name="Uwase", phone="+250781111111")
        PatientProfile.objects.get_or_create(user=p, defaults={"blood_group": "O+",
                                                                 "emergency_contact_name": "Family",
                                                                 "emergency_contact_phone": "+250782222222"})
        p2 = mk("patient2", "patient", "patient123", first_name="Aline", last_name="Mukamana", phone="+250783333333")
        PatientProfile.objects.get_or_create(user=p2, defaults={"blood_group": "A+", "gender": "female"})
        for i, h in enumerate(hospitals[:6]):
            driver = mk(f"paramedic{i + 1}", "paramedic", "paramedic123", first_name="Paramedic", last_name=str(i + 1))
            if not driver.phone:
                driver.phone = f"+25078800010{i}"
                driver.save(update_fields=["phone"])
            amb, created = Ambulance.objects.get_or_create(plate_number=f"RAD {100 + i}A", defaults=dict(
                ambulance_type="als" if i % 2 == 0 else "bls", base_hospital=h, driver=driver,
                contact_phone=driver.phone, latitude=h.latitude + 0.004, longitude=h.longitude + 0.004,
                status="available", location_is_simulated=True))
            if not created and not amb.assignments.filter(released_at__isnull=True).exists():
                # re-seeding resets idle demo ambulances to their base, never ones on a job
                amb.latitude, amb.longitude = h.latitude + 0.004, h.longitude + 0.004
                if amb.status != "offline":
                    amb.status = "available"
                amb.save()
        mk("nurse_pending", "hospital", "hospital123", hospital=hospitals[1], first_name="Pending",
           last_name="Nurse", is_approved=False, professional_id="RN-TEST-001")
        self.stdout.write(self.style.SUCCESS(
            "Seeded. Logins: admin/admin12345, operator/operator123, chuk_staff/hospital123, "
            "patient1/patient123, patient2/patient123, paramedic1..6/paramedic123, nurse_pending/hospital123 (awaiting approval)  "
            "(change these before any deployment!). Hospital capacity figures are SIMULATED."))
