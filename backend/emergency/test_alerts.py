"""Operator siren alerts for unhandled patient requests (scenarios 1-6 of the brief + rules)."""
import json
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from emergency import escalation, services
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
        self.assertIn(reverse("alert_accept"), page)

    def test_detail_page_accept_button(self):
        e = self.emergency(self.p1)
        url = reverse("emergency_detail", args=[e.pk])
        self.assertContains(self.client.get(url), "Accept request (stop alarm)")
        self.client.post(reverse("emergency_action", args=[e.pk]), {"action": "accept"})
        e.refresh_from_db()
        self.assertIsNotNone(e.accepted_at)
        self.assertEqual(self.feed()["alert_count"], 0)


class ScheduleAndEscalationTests(TestCase):
    """2-minute siren schedule per request, AI escalation after the 3rd alert (exactly once)."""

    @classmethod
    def setUpTestData(cls):
        call_command("seed_data", verbosity=0)

    def setUp(self):
        from datetime import timedelta
        self.td = timedelta
        self.p1 = User.objects.get(username="patient1")
        self.p2 = User.objects.get(username="patient2")
        self.op = User.objects.get(username="operator")

    def mild(self, patient):
        """A non-severe request (severe ones escalate immediately)."""
        e = services.create_emergency(patient, {"emergency_type": "other", "latitude": -1.95, "longitude": 30.06,
                                                "conscious": True, "pain_level": 2})
        e.refresh_from_db()
        if escalation.severe_reasons(e):  # the AI triage may still rate it critical - make it mild for this test
            EmergencyRequest.objects.filter(pk=e.pk).update(priority="medium", escalated_at=None, escalation={})
            e.refresh_from_db()
        return e

    def at(self, e, seconds):
        return e.last_alert_at + self.td(seconds=seconds)

    def notes(self, user, prefix):
        from notifications.models import Notification
        return Notification.objects.filter(recipient=user, channel="in_app", title__startswith=prefix).count()

    def test_three_alerts_then_escalation_exactly_once(self):
        e = self.mild(self.p1)
        self.assertEqual(e.alert_count, 1)                         # alert 1 on arrival
        t0 = e.last_alert_at
        escalation.tick(t0 + self.td(seconds=119))
        e.refresh_from_db()
        self.assertEqual(e.alert_count, 1)                         # not yet 2 minutes
        escalation.tick(t0 + self.td(seconds=120))
        e.refresh_from_db()
        self.assertEqual((e.alert_count, e.escalated_at), (2, None))
        escalation.tick(t0 + self.td(seconds=240))                 # 3rd alert -> AI escalation
        e.refresh_from_db()
        self.assertEqual(e.alert_count, 3)
        self.assertEqual(e.escalation_reason, "no_operator")
        self.assertEqual(self.notes(self.p1, f"Emergency {e.reference}: help is being arranged"), 1)
        self.assertEqual(self.notes(self.op, f"ESCALATED {e.reference}"), 1)
        self.assertEqual(self.notes(self.p2, f"Emergency {e.reference}"), 0)   # only the right patient
        escalation.tick(t0 + self.td(seconds=360))                 # keeps alerting, never re-escalates
        escalation.tick(t0 + self.td(seconds=480))
        e.refresh_from_db()
        self.assertEqual(e.alert_count, 5)
        self.assertEqual(self.notes(self.op, f"ESCALATED {e.reference}"), 1)
        self.assertEqual(self.notes(self.p1, f"Emergency {e.reference}: help is being arranged"), 1)
        self.assertNotIn(e.status, ("completed", "cancelled"))     # stays open for a human
        self.assertIsNone(e.accepted_at)

    def test_concurrent_ticks_do_not_double_count(self):
        e = self.mild(self.p1)
        later = self.at(e, 121)
        escalation.tick(later)
        escalation.tick(later)                                    # second poll at the same moment
        e.refresh_from_db()
        self.assertEqual(e.alert_count, 2)

    def test_independent_counters_and_accept(self):
        e1 = self.mild(self.p1)
        e2 = self.mild(self.p2)
        services.accept_emergency(e1, self.op)
        t = max(e1.last_alert_at, e2.last_alert_at)
        escalation.tick(t + self.td(seconds=121))
        escalation.tick(t + self.td(seconds=242))
        e1.refresh_from_db(); e2.refresh_from_db()
        self.assertEqual((e1.alert_count, e1.escalated_at), (1, None))   # accepted: siren stopped
        self.assertEqual(e2.alert_count, 3)
        self.assertIsNotNone(e2.escalated_at)

    def test_severe_symptoms_escalate_immediately(self):
        e = services.create_emergency(self.p1, {"emergency_type": "cardiac", "latitude": -1.95, "longitude": 30.06,
                                                "chest_pain": True, "breathing_difficulty": True})
        e.refresh_from_db()
        self.assertEqual((e.alert_count, e.escalation_reason), (1, "severe_symptoms"))
        msg = e.escalation["patient_message"]
        self.assertIn("call", msg)
        self.assertIn("not a diagnosis", msg)
        self.assertTrue(e.escalation["guidance_sources"])        # guidance came from the knowledge base

    def test_escalation_reads_vitals_and_never_starts_tests(self):
        from patient_monitoring import services as mon
        from patient_monitoring.models import MedicalTestRequest, SimulationSession
        e = self.mild(self.p1)
        mon.give_consent(e, self.p1)
        mon.record_reading(e, self.p1, {"heart_rate_bpm": 120, "spo2_pct": 91}, "manual")
        escalation.escalate(e, "no_operator")
        e.refresh_from_db()
        self.assertEqual(e.escalation["vitals"][0]["label"], "REAL")
        self.assertFalse(e.escalation["test"]["started"])
        self.assertFalse(MedicalTestRequest.objects.filter(patient=self.p1).exists())
        self.assertFalse(SimulationSession.objects.exists())
        self.assertIsNone(escalation.escalate(e, "no_operator"))  # second call does nothing

    def test_escalation_without_consent(self):
        e = self.mild(self.p1)
        escalation.escalate(e, "no_operator")
        e.refresh_from_db()
        self.assertEqual(e.escalation["vitals"], [])
        self.assertIn("not consented", e.escalation["vitals_note"])

    def test_feed_exposes_schedule_and_refresh_keeps_count(self):
        e = self.mild(self.p1)
        escalation.tick(self.at(e, 121))
        self.client.force_login(self.op)
        a1 = self.client.get(reverse("operator_feed")).json()["alerts"][0]
        a2 = self.client.get(reverse("operator_feed")).json()["alerts"][0]   # "refresh"
        self.assertEqual((a1["alert_count"], a2["alert_count"]), (2, 2))
        self.assertTrue(a1["next_alert_at"])
        self.assertFalse(a1["escalated"])

    def test_page_works_without_siren_file(self):
        self.client.force_login(self.op)
        page = self.client.get(reverse("dashboard"))
        self.assertEqual(page.status_code, 200)
        self.assertIn('data-siren=""', page.content.decode())   # file not added yet -> panel says so
