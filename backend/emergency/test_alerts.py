"""Operator siren alerts for unhandled patient requests (scenarios 1-6 of the brief + rules)."""
import json
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from emergency import services
from emergency.models import EmergencyRequest
from patient_monitoring import test_requests as tr


class AlertTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_data", verbosity=0)

    def setUp(self):
        self.p1 = User.objects.get(username="patient1")
        self.p2 = User.objects.get(username="patient2")
        self.op = User.objects.get(username="operator")
        self.client.force_login(self.op)

    def emergency(self, patient, **extra):
        return services.create_emergency(patient, {"emergency_type": "cardiac", "latitude": -1.95, "longitude": 30.06,
                                                   "chest_pain": True, **extra})

    def feed(self):
        r = self.client.get(reverse("operator_feed"))
        self.assertEqual(r.status_code, 200)
        return r.json()

    def accept(self, key):
        return self.client.post(reverse("alert_accept"), json.dumps({"key": key}), content_type="application/json")

    def test_scenarios_1_to_6(self):
        self.assertEqual(self.feed()["alert_count"], 0)
        e1 = self.emergency(self.p1)                                   # 1. new request -> alert
        a = self.feed()["alerts"]
        self.assertEqual([x["key"] for x in a], [f"emergency-{e1.pk}"])
        self.assertEqual(a[0]["status_label"], "PENDING")
        for f in ("patient", "patient_code", "type", "submitted_at", "priority", "url"):
            self.assertTrue(a[0][f], f)
        e2 = self.emergency(self.p2)                                   # 2. second request -> two pending
        self.assertEqual(self.feed()["alert_count"], 2)
        self.assertTrue(self.accept(f"emergency-{e1.pk}").json()["ok"])  # 3. accept first -> second stays
        self.assertEqual([x["key"] for x in self.feed()["alerts"]], [f"emergency-{e2.pk}"])
        e1.refresh_from_db()
        self.assertEqual((e1.accepted_by, e1.operator_label in ("ACCEPTED", "IN PROGRESS")), (self.op, True))
        self.accept(f"emergency-{e2.pk}")                              # 4. all accepted -> silence
        self.assertEqual(self.feed()["alert_count"], 0)
        services.update_status(e1, "cancelled", self.op)
        e3 = self.emergency(self.p1)                                   # 5. new one rings again
        self.assertEqual([x["key"] for x in self.feed()["alerts"]], [f"emergency-{e3.pk}"])
        self.assertEqual(self.feed()["alerts"], self.feed()["alerts"])  # 6. refresh: same list, no duplicates

    def test_auto_dispatch_is_not_handling(self):
        e = self.emergency(self.p1)
        self.assertIn(e.status, ("pending", "assigned"))  # assigned when an ambulance was free
        self.assertEqual(self.feed()["alert_count"], 1)

    def test_cancel_or_complete_stops_alert(self):
        e = self.emergency(self.p1)
        services.update_status(e, "cancelled", self.p1, "patient cancelled")
        self.assertEqual(self.feed()["alert_count"], 0)

    def test_test_requests_ring_until_accepted_or_cancelled(self):
        req = tr.create_request(self.p1, ["temperature"])
        e = self.emergency(self.p2)
        keys = [x["key"] for x in self.feed()["alerts"]]
        self.assertEqual(set(keys), {f"test-{req.reference}", f"emergency-{e.pk}"})
        self.accept(f"test-{req.reference}")
        req.refresh_from_db()
        self.assertEqual(req.status, "accepted")
        self.assertEqual([x["key"] for x in self.feed()["alerts"]], [f"emergency-{e.pk}"])
        req2 = tr.create_request(self.p2, ["spo2"])
        tr.cancel(req2, self.p2)
        self.assertNotIn(f"test-{req2.reference}", [x["key"] for x in self.feed()["alerts"]])

    def test_accept_twice_and_closed_requests(self):
        e = self.emergency(self.p1)
        self.accept(f"emergency-{e.pk}")
        self.assertEqual(self.accept(f"emergency-{e.pk}").status_code, 200)  # idempotent (two operators)
        self.assertEqual(e.logs.filter(note__startswith="Accepted by operator").count(), 1)
        services.update_status(EmergencyRequest.objects.get(pk=e.pk), "cancelled", self.op)
        self.assertEqual(self.accept(f"emergency-{e.pk}").status_code, 400)
        self.assertEqual(self.accept("bogus").status_code, 400)

    def test_only_operators(self):
        self.emergency(self.p1)
        self.client.force_login(self.p1)
        self.assertEqual(self.client.get(reverse("operator_feed")).status_code, 403)
        self.assertEqual(self.accept("emergency-1").status_code, 403)
        page = self.client.get(reverse("patient_home")).content.decode()
        self.assertNotIn("operator-alerts.js", page)   # patients never get the siren
        self.assertNotIn("data-siren", page)

    def test_operator_page_and_sound_file(self):
        page = self.client.get(reverse("dashboard")).content.decode()
        self.assertIn("operator-alerts", page)
        self.assertIn("ambulance-siren", page)
        self.assertIn(reverse("alert_accept"), page)
        wav = Path(settings.BASE_DIR) / "static" / "audio" / "ambulance-siren.wav"
        self.assertEqual(wav.read_bytes()[:4], b"RIFF")

    def test_detail_page_accept_button(self):
        e = self.emergency(self.p1)
        url = reverse("emergency_detail", args=[e.pk])
        self.assertContains(self.client.get(url), "Accept request (stop alarm)")
        self.client.post(reverse("emergency_action", args=[e.pk]), {"action": "accept"})
        e.refresh_from_db()
        self.assertIsNotNone(e.accepted_at)
        self.assertEqual(self.feed()["alert_count"], 0)
