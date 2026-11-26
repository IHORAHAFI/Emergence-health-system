"""Mandatory test table (brief section 15) for the shared alert workflow + AI takeover, all request kinds.
Time is moved forward with tick(now=...) so the 2-minute schedule runs in milliseconds."""
import json
import shutil
import struct
import tempfile
import wave
from datetime import timedelta
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from notifications.models import Notification
from patient_monitoring import proactive, test_requests as tr
from patient_monitoring.models import MedicalDevice, MedicalTestRequest, MonitoringEvent, SimulationSession
from . import alert_workflow as wf, scheduler, siren, services
from .models import EmergencyRequest

I = 120  # production interval


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_data", verbosity=0)

    def setUp(self):
        self.a = User.objects.get(username="patient1")
        self.b = User.objects.get(username="patient2")
        self.op = User.objects.get(username="operator")

    def contact(self, patient, text="Please call me about my blood pressure"):
        return proactive.report(patient, "contact_request", [], text, by=patient)

    def at(self, obj, seconds):
        obj.refresh_from_db()
        return obj.last_alert_at + timedelta(seconds=seconds)

    def run_to(self, obj, *offsets):
        """tick() at the given seconds after the request's FIRST alert (fixed per request)."""
        if not hasattr(self, "_t0"):
            self._t0 = {}
        t0 = self._t0.setdefault((type(obj), obj.pk), type(obj).objects.get(pk=obj.pk).last_alert_at)
        for s in offsets:
            wf.tick(t0 + timedelta(seconds=s))
        obj.refresh_from_db()
        return obj

    def notes(self, user, prefix):
        return Notification.objects.filter(recipient=user, channel="in_app", title__startswith=prefix).count()

    def feed(self):
        self.client.force_login(self.op)
        return self.client.get(reverse("operator_feed")).json()

    def keys(self):
        return [a["key"] for a in self.feed()["alerts"]]


class TimelineTests(Base):
    def test_full_timeline_service_request(self):
        ev = self.contact(self.a)
        self.assertEqual(ev.alert_count, 1)                                   # alert 1 immediately
        a = next(x for x in self.feed()["alerts"] if x["key"] == f"service-{ev.pk}")
        self.assertEqual((a["alert_count"], a["stage"], a["patient"]), (1, "alerting", "Jean Uwase"))
        self.assertTrue(a["next_alert_at"])
        self.assertEqual(self.run_to(ev, I - 1).alert_count, 1)               # not before 2 minutes
        self.assertEqual(self.run_to(ev, I).alert_count, 2)                   # alert 2
        ev = self.run_to(ev, 2 * I)
        self.assertEqual((ev.alert_count, ev.ai_takeover_at), (3, None))      # alert 3, no takeover yet
        self.assertEqual(self.run_to(ev, 2 * I + 60).ai_takeover_at, None)    # still waiting
        ev = self.run_to(ev, 3 * I)                                           # +2 more minutes -> AI takeover
        self.assertIsNotNone(ev.ai_takeover_at)
        self.assertEqual(ev.status, "ai_responded")                          # NOT completed
        self.assertIsNone(ev.accepted_at)
        self.assertEqual(self.notes(self.a, "Update from your assistant"), 1)  # correct patient answered
        self.assertEqual(self.notes(self.b, "Update from your assistant"), 0)
        self.assertEqual(self.notes(self.op, "AI TAKEOVER"), 1)               # operator told
        a = next(x for x in self.feed()["alerts"] if x["key"] == f"service-{ev.pk}")
        self.assertEqual(a["stage"], "ai_takeover")                          # still visible, waiting for a human
        self.run_to(ev, 4 * I, 5 * I)                                         # no 4th alert, no 2nd takeover
        ev.refresh_from_db()
        self.assertEqual(ev.alert_count, 3)
        self.assertEqual(self.notes(self.op, "AI TAKEOVER"), 1)

    def test_accept_before_alert_2(self):
        ev = self.contact(self.a)
        tr.accept_alert(f"service-{ev.pk}", self.op)
        ev = self.run_to(ev, I, 2 * I, 3 * I)
        self.assertEqual((ev.alert_count, ev.ai_takeover_at), (1, None))
        self.assertNotIn(f"service-{ev.pk}", self.keys())
        self.assertEqual(self.notes(self.a, "A staff member is handling your request"), 1)

    def test_accept_before_alert_3_cancels_takeover(self):
        ev = self.contact(self.a)
        self.run_to(ev, I)
        tr.accept_alert(f"service-{ev.pk}", self.op)
        ev = self.run_to(ev, 2 * I, 3 * I, 4 * I)
        self.assertEqual((ev.alert_count, ev.ai_takeover_at), (2, None))

    def test_accept_after_alert_3_before_takeover(self):
        ev = self.contact(self.a)
        self.run_to(ev, I, 2 * I)
        tr.accept_alert(f"service-{ev.pk}", self.op)
        ev = self.run_to(ev, 3 * I, 4 * I)
        self.assertEqual((ev.alert_count, ev.ai_takeover_at), (3, None))

    def test_independent_timers(self):
        a = self.contact(self.a)
        b = self.contact(self.b)
        MonitoringEvent.objects.filter(pk=b.pk).update(last_alert_at=a.last_alert_at + timedelta(seconds=60))
        t0 = a.last_alert_at
        wf.tick(t0 + timedelta(seconds=I))               # A due, B not yet
        a.refresh_from_db(); b.refresh_from_db()
        self.assertEqual((a.alert_count, b.alert_count), (2, 1))
        tr.accept_alert(f"service-{a.pk}", self.op)       # handling A never stops B
        wf.tick(t0 + timedelta(seconds=I + 60))
        b.refresh_from_db()
        self.assertEqual(b.alert_count, 2)

    def test_refresh_does_not_reset_counts(self):
        ev = self.contact(self.a)
        self.run_to(ev, I)
        f1 = [x for x in self.feed()["alerts"] if x["key"] == f"service-{ev.pk}"][0]
        f2 = [x for x in self.feed()["alerts"] if x["key"] == f"service-{ev.pk}"][0]
        self.assertEqual((f1["alert_count"], f2["alert_count"]), (2, 2))

    def test_server_restart_recovers_without_burst(self):
        ev = self.contact(self.a)
        # server down for 10 minutes: the first tick after restart sends ONE late alert, not a burst
        wf.tick(ev.last_alert_at + timedelta(minutes=10))
        ev.refresh_from_db()
        self.assertEqual(ev.alert_count, 2)
        self.assertIsNone(ev.ai_takeover_at)

    def test_concurrent_ticks_one_takeover(self):
        ev = self.contact(self.a)
        self.run_to(ev, I, 2 * I)
        t = self.at(ev, I)
        wf.tick(t)
        wf.tick(t)                                          # second operator / process at the same moment
        self.assertEqual(self.notes(self.a, "Update from your assistant"), 1)

    def test_scheduler_run_once_and_process_detection(self):
        ev = self.contact(self.a)
        MonitoringEvent.objects.filter(pk=ev.pk).update(last_alert_at=ev.last_alert_at - timedelta(seconds=I))
        scheduler.run_once()                                # the background thread's step
        ev.refresh_from_db()
        self.assertEqual(ev.alert_count, 2)
        self.assertTrue(scheduler.is_server_process(["/usr/bin/daphne", "config.asgi:application"]))
        self.assertFalse(scheduler.is_server_process(["manage.py", "test"]))


class TestRequestTakeoverTests(Base):
    def req(self, tests=("heart_rate",), patient=None):
        return tr.create_request(patient or self.a, list(tests))

    def test_mobile_request_uses_same_workflow(self):
        api = APIClient()
        api.force_authenticate(self.a)
        ref = api.post("/api/tests/", {"tests": ["blood_pressure"]}, format="json").data["reference"]
        req = MedicalTestRequest.objects.get(reference=ref)
        self.assertEqual(req.alert_count, 1)                  # alert 1 at once, same as the web
        self.assertIn(f"test-{ref}", self.keys())
        staff = APIClient()
        staff.force_authenticate(self.op)                    # mobile operator sees the same alert
        self.assertIn(f"test-{ref}", [a["key"] for a in staff.get("/api/staff/alerts/").data["alerts"]])

    def test_takeover_starts_simulation_labelled_simulated(self):
        req = self.req()
        req = self.run_to(req, I, 2 * I, 3 * I)
        self.assertEqual((req.status, req.ai_response["action"]), ("testing", "started_simulation"))
        self.assertIsNone(req.assigned_operator)              # the AI did not "accept" it
        self.assertEqual(SimulationSession.objects.filter(test_request=req).count(), 1)
        self.assertIn("SIMULATED", req.ai_response["patient_message"])
        self.assertIn(f"test-{req.reference}", self.keys())   # still waiting for a human
        wf.tick(self.at(req, 10 * I))                          # never a second test
        self.assertEqual(SimulationSession.objects.filter(test_request=req).count(), 1)
        self.assertIsNone(req.completed_at)
        tr.accept_alert(f"test-{req.reference}", self.op)      # a human takes responsibility
        req.refresh_from_db()
        self.assertEqual(req.assigned_operator, self.op)
        self.assertNotIn(f"test-{req.reference}", self.keys())

    def test_takeover_uses_real_device_when_available(self):
        MedicalDevice.objects.create(patient=self.a, kind="heart_rate", name="Polar H10", device_identifier="AA:01")
        req = self.run_to(self.req(), I, 2 * I, 3 * I)
        self.assertEqual(req.ai_response["action"], "opened_for_real_device")
        self.assertFalse(SimulationSession.objects.filter(test_request=req).exists())
        self.assertFalse(req.readings.exists())                # nothing invented

    @override_settings(AI_TAKEOVER_ALLOW_SIMULATION=False)
    def test_ai_cannot_perform_service(self):
        req = self.run_to(self.req(), I, 2 * I, 3 * I)
        self.assertEqual((req.status, req.ai_response["action"]), ("pending", "none"))
        self.assertIn("No measurement has been taken", req.ai_response["patient_message"])
        self.assertIn(f"test-{req.reference}", self.keys())   # human assistance still pending

    def test_accept_before_takeover_stops_it(self):
        req = self.req()
        self.run_to(req, I, 2 * I)
        tr.accept_alert(f"test-{req.reference}", self.op)
        req = self.run_to(req, 3 * I)
        self.assertEqual((req.status, req.ai_takeover_at), ("accepted", None))


class EmergencyWorkflowTests(Base):
    def em(self, patient, **extra):
        e = services.create_emergency(patient, {"emergency_type": "other", "latitude": -1.95, "longitude": 30.06, **extra})
        EmergencyRequest.objects.filter(pk=e.pk).update(priority="medium")  # keep it non-severe for timing tests
        e.refresh_from_db()
        return e

    def test_emergency_takeover_two_minutes_after_alert_3(self):
        e = self.em(self.a)
        if e.escalated_at:
            self.skipTest("AI triage rated this test emergency severe")
        e = self.run_to(e, I, 2 * I)
        self.assertEqual((e.alert_count, e.escalated_at), (3, None))
        e = self.run_to(e, 3 * I)
        self.assertEqual(e.escalation_reason, "no_operator")
        self.assertEqual(self.notes(self.a, f"Emergency {e.reference}: help is being arranged"), 1)
        self.assertIsNone(e.accepted_at)

    def test_severe_escalates_immediately_but_still_three_alerts(self):
        e = services.create_emergency(self.a, {"emergency_type": "cardiac", "latitude": -1.95, "longitude": 30.06,
                                               "chest_pain": True})
        e.refresh_from_db()
        self.assertEqual(e.escalation_reason, "severe_symptoms")   # no waiting for three alerts
        e = self.run_to(e, I, 2 * I, 3 * I, 4 * I)
        self.assertEqual(e.alert_count, 3)

    def test_only_operators_accept(self):
        e = self.em(self.a)
        with self.assertRaises(tr.TestRequestError):
            tr.accept_alert(f"emergency-{e.pk}", self.a)            # a patient cannot accept
        self.client.force_login(self.a)
        self.assertEqual(self.client.post(reverse("alert_accept"), json.dumps({"key": f"emergency-{e.pk}"}),
                                          content_type="application/json").status_code, 403)

    def test_viewing_is_not_accepting(self):
        ev = proactive.report(self.a, "contact_request", [], "hello", by=self.a)
        self.client.force_login(self.op)
        self.assertEqual(self.client.get(reverse("service_request_detail", args=[ev.pk])).status_code, 200)
        ev.refresh_from_db()
        self.assertIsNone(ev.accepted_at)
        r = self.client.post(reverse("service_request_action", args=[ev.pk]), {"action": "close"})
        ev.refresh_from_db()
        self.assertNotEqual(ev.status, "closed")                     # a reason is required
        self.client.post(reverse("service_request_action", args=[ev.pk]), {"action": "close", "reason": "called patient"})
        ev.refresh_from_db()
        self.assertEqual(ev.status, "closed")


class SirenFileTests(TestCase):
    """The REAL Police Wail file is not in the repository yet; these tests use a temporary WAV to check that the
    verification, the URL and the HTTP serving work - and that a missing file gives a visible error."""

    @classmethod
    def setUpTestData(cls):
        call_command("seed_data", verbosity=0)

    def test_missing_file_is_reported(self):
        siren._cache.clear()
        with override_settings(ALERT_SIREN_FILE="audio/does_not_exist.mp3"):
            r = siren.verify()
            self.assertFalse(r["ok"])
            self.assertIn("not found", r["problem"])
            self.assertEqual(siren.url(), "")
            self.client.force_login(User.objects.get(username="operator"))
            page = self.client.get(reverse("dashboard")).content.decode()
            self.assertIn('data-siren=""', page)
            self.assertIn("not found", page)                          # visible reason for the dashboard

    def test_real_audio_file_is_verified_and_served(self):
        d = Path(tempfile.mkdtemp())
        try:
            (d / "audio").mkdir()
            with wave.open(str(d / "audio" / "test_siren.wav"), "wb") as w:  # 6.97 s of silence
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
                w.writeframes(struct.pack("<h", 0) * int(8000 * 6.97))
            (d / "audio" / "fake.mp3").write_bytes(b"not audio" * 500)
            with override_settings(STATICFILES_DIRS=[d], ALERT_SIREN_FILE="audio/test_siren.wav",
                                   STORAGES={"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                                             "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}):
                siren._cache.clear()
                r = siren.verify()
                self.assertTrue(r["ok"], r)
                self.assertAlmostEqual(r["seconds"], 6.97, places=1)
                self.assertTrue(siren.url().endswith("audio/test_siren.wav"))
                with override_settings(ALERT_SIREN_FILE="audio/fake.mp3"):
                    siren._cache.clear()
                    self.assertIn("not an MP3", siren.verify()["problem"])  # wrong format detected
        finally:
            siren._cache.clear()
            shutil.rmtree(d)
