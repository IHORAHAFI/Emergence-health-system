"""Patient-initiated medical test workflow: patient -> operator -> IoT simulation -> results back to the patient.

Covers the seven required scenarios (TEST 1..7) plus the business rules."""
from datetime import timedelta

from asgiref.sync import async_to_sync
from django.core.management import call_command
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from notifications.models import Notification
from . import simulation, test_requests as svc
from .models import MedicalTestRequest, SimulationSession, VitalSignReading


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_data", verbosity=0)

    def setUp(self):
        self.a = User.objects.get(username="patient1")
        self.b = User.objects.get(username="patient2")
        self.op = User.objects.get(username="operator")

    def request_as(self, user, test):
        self.client.force_login(user)
        r = self.client.post(reverse("patient_tests"), {"test": test})
        self.client.logout()
        return r

    def run_minutes(self, session, n):
        for _ in range(n):
            SimulationSession.objects.filter(pk=session.pk).update(last_tick_at=timezone.now() - timedelta(seconds=60))
            simulation.tick(session, max_steps=1)
        session.refresh_from_db()

    def accept_and_start(self, req, scenario="normal"):
        svc.accept(req, self.op)
        return svc.start(req, self.op, scenario)


class ScenarioTests(Base):
    def test_1_temperature_for_patient_a(self):
        self.request_as(self.a, "temperature")
        req = MedicalTestRequest.objects.get(patient=self.a)
        self.assertEqual((req.status, req.tests), ("pending", ["temperature"]))
        self.assertRegex(req.reference, r"^TST-\d{8}-[0-9A-F]{4}$")
        # operator is notified (in-app record + feed used by the audible alarm)
        self.assertTrue(Notification.objects.filter(recipient=self.op, test_request=req).exists())
        feed = svc.operator_feed()
        self.assertEqual(feed["pending"][0]["reference"], req.reference)
        self.assertEqual(feed["pending"][0]["patient_code"], f"P-{self.a.id:05d}")
        session = self.accept_and_start(req)
        self.assertEqual(list(session.devices.values_list("kind", flat=True)), ["thermometer"])
        self.run_minutes(session, 3)
        rs = VitalSignReading.objects.filter(test_request=req)
        self.assertTrue(rs.exists())
        for r in rs:
            self.assertEqual(r.patient, self.a)
            self.assertEqual(r.source, "simulated")
            self.assertIsNotNone(r.temperature_c)
            self.assertIsNone(r.heart_rate_bpm)  # only the requested measurement
        # result visible to patient A
        self.client.force_login(self.a)
        d = self.client.get(reverse("test_data", args=[req.reference])).json()
        self.assertIn("temperature_c", d["summary"])
        self.assertTrue(d["summary"]["temperature_c"]["simulated"])

    def test_2_heart_rate_only_reaches_patient_b(self):
        self.request_as(self.b, "heart_rate")
        req = MedicalTestRequest.objects.get(patient=self.b)
        session = self.accept_and_start(req)
        self.assertEqual(list(session.devices.values_list("kind", flat=True)), ["heart_rate"])
        self.run_minutes(session, 2)
        self.assertTrue(VitalSignReading.objects.filter(test_request=req, patient=self.b).exists())
        self.assertFalse(VitalSignReading.objects.filter(patient=self.a).exists())
        self.assertFalse(VitalSignReading.objects.filter(test_request=req).exclude(patient=self.b).exists())

    def test_3_all_vital_signs(self):
        self.request_as(self.a, "all")
        req = MedicalTestRequest.objects.get(patient=self.a)
        self.assertTrue(req.is_complete_set)
        self.assertEqual(req.test_names, "Complete vital signs")
        session = self.accept_and_start(req)
        self.assertEqual(session.devices.count(), 5)
        self.run_minutes(session, 2)
        self.client.force_login(self.a)
        d = self.client.get(reverse("test_data", args=[req.reference])).json()
        self.assertEqual(set(d["summary"]), {"temperature_c", "heart_rate_bpm", "spo2_pct", "blood_pressure",
                                             "respiratory_rate"})
        page = self.client.get(reverse("patient_test_detail", args=[req.reference]))
        self.assertContains(page, "SIMULATED")

    def test_4_two_patients_at_once_are_independent(self):
        self.request_as(self.a, "spo2")
        self.request_as(self.b, "temperature")
        self.assertEqual(svc.operator_feed()["pending_count"], 2)
        ra, rb = MedicalTestRequest.objects.get(patient=self.a), MedicalTestRequest.objects.get(patient=self.b)
        sa, sb = self.accept_and_start(ra), self.accept_and_start(rb)
        self.run_minutes(sa, 2)
        self.run_minutes(sb, 2)
        svc.complete(ra, self.op)
        rb.refresh_from_db()
        self.assertEqual(rb.status, "testing")  # completing A does not touch B
        self.assertEqual(set(VitalSignReading.objects.filter(test_request=ra).values_list("patient", flat=True)), {self.a.id})
        self.assertEqual(set(VitalSignReading.objects.filter(test_request=rb).values_list("patient", flat=True)), {self.b.id})

    def test_5_pause_and_resume(self):
        self.request_as(self.a, "heart_rate")
        req = MedicalTestRequest.objects.get(patient=self.a)
        s = self.accept_and_start(req)
        self.run_minutes(s, 2)
        simulation.control(s, "pause")
        n = req.readings.count()
        self.run_minutes(s, 3)
        self.assertEqual(req.readings.count(), n)  # nothing while paused
        simulation.control(s, "resume")
        self.run_minutes(s, 2)
        self.assertGreater(req.readings.count(), n)

    def test_6_complete_stops_simulation_and_patient_gets_results(self):
        self.request_as(self.a, "temperature")
        req = MedicalTestRequest.objects.get(patient=self.a)
        s = self.accept_and_start(req)
        self.run_minutes(s, 3)
        svc.complete(req, self.op)
        req.refresh_from_db()
        s.refresh_from_db()
        self.assertEqual((req.status, s.status), ("completed", "stopped"))
        self.assertIsNotNone(req.completed_at)
        n = req.readings.count()
        self.run_minutes(s, 3)
        self.assertEqual(req.readings.count(), n)  # a completed request never gets new measurements
        note = Notification.objects.get(recipient=self.a, test_request=req, title="Your test results are ready")
        self.assertIn("Temperature", note.message)
        self.assertIn("SIMULATED", note.message)

    def test_7_patient_cannot_see_other_patients_results(self):
        self.request_as(self.b, "temperature")
        rb = MedicalTestRequest.objects.get(patient=self.b)
        self.client.force_login(self.a)
        for name in ("patient_test_detail", "test_data"):
            self.assertEqual(self.client.get(reverse(name, args=[rb.reference])).status_code, 404)
        self.assertEqual(self.client.post(reverse("patient_test_cancel", args=[rb.reference])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("patient_tests")), rb.reference)


class RuleTests(Base):
    def test_duplicate_request_blocked(self):
        self.request_as(self.a, "temperature")
        self.request_as(self.a, "temperature")
        self.request_as(self.a, "all")  # overlaps the open temperature request
        self.assertEqual(MedicalTestRequest.objects.filter(patient=self.a).count(), 1)
        with self.assertRaises(svc.TestRequestError):
            svc.create_request(self.a, ["temperature"])
        # after completion the same test can be requested again
        req = MedicalTestRequest.objects.get(patient=self.a)
        svc.cancel(req, self.a)
        svc.create_request(self.a, ["temperature"])

    def test_no_measurements_without_active_testing(self):
        req = svc.create_request(self.a, ["temperature"])
        with self.assertRaises(svc.services.ReadingRejected):
            svc.record_test_reading(req, {"temperature_c": 37}, "simulated", None)
        svc.accept(req, self.op)
        with self.assertRaises(svc.services.ReadingRejected):
            svc.record_test_reading(req, {"temperature_c": 37}, "simulated", None)

    def test_cancelling_a_running_test_stops_it(self):
        req = svc.create_request(self.a, ["temperature"])
        s = self.accept_and_start(req)
        with self.assertRaises(svc.TestRequestError):
            svc.cancel(req, self.a)  # patient cannot cancel while testing
        svc.cancel(req, self.op)
        n = req.readings.count()
        self.run_minutes(s, 3)
        self.assertEqual(req.readings.count(), n)

    def test_only_operators_manage_requests(self):
        req = svc.create_request(self.a, ["spo2"])
        for user in (self.a, User.objects.get(username="chuk_staff"), User.objects.get(username="paramedic1")):
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse("test_requests")).status_code, 403)
            self.assertEqual(self.client.get(reverse("operator_feed")).status_code, 403)
            self.assertEqual(self.client.post(reverse("test_request_action", args=[req.reference]),
                                              {"action": "accept"}).status_code, 403)
        with self.assertRaises(svc.TestRequestError):
            svc.accept(req, self.a)

    def test_operator_pages_full_flow(self):
        self.request_as(self.a, "blood_pressure")
        req = MedicalTestRequest.objects.get(patient=self.a)
        self.client.force_login(self.op)
        page = self.client.get(reverse("test_requests"))
        self.assertContains(page, req.reference)
        self.assertContains(page, self.a.get_full_name())
        self.client.post(reverse("test_request_action", args=[req.reference]), {"action": "accept"})
        detail = self.client.get(reverse("test_request_detail", args=[req.reference]))
        self.assertContains(detail, "Start IoT test")
        self.assertNotContains(detail, "full_name")  # no virtual-patient creation form
        r = self.client.post(reverse("test_request_action", args=[req.reference]), {"action": "start", "scenario": "critical"})
        s = SimulationSession.objects.get(test_request=req)
        self.assertRedirects(r, reverse("sim_panel", args=[s.id]))
        panel = self.client.get(reverse("sim_panel", args=[s.id]))
        self.assertContains(panel, "cannot be changed here")
        self.assertContains(panel, req.patient_code)
        self.run_minutes(s, 9)  # critical: BP falls below 90 mmHg within ~7 simulated minutes
        d = self.client.post(reverse("sim_tick", args=[s.id])).json()
        self.assertEqual(d["test_request"], req.reference)
        self.assertTrue(all(set(x["fields"]) <= {"systolic_mmhg", "diastolic_mmhg"} for x in d["devices"]))
        self.assertTrue(req.alerts.exists())  # systolic < 90 crosses the threshold -> critical feed item
        self.assertTrue(any(c["reference"] == req.reference for c in svc.operator_feed()["critical"]))
        self.client.post(reverse("test_request_action", args=[req.reference]), {"action": "complete"})
        req.refresh_from_db()
        self.assertEqual(req.status, "completed")

    def test_complete_needs_a_measurement(self):
        req = svc.create_request(self.a, ["spo2"])
        svc.accept(req, self.op)
        with self.assertRaises(svc.TestRequestError):
            svc.complete(req, self.op)

    def test_patient_page_and_statuses(self):
        self.client.force_login(self.a)
        page = self.client.get(reverse("patient_tests"))
        for label in ("Temperature", "Heart rate", "Oxygen level", "Blood pressure", "Breathing rate",
                      "All vital signs", "Request all"):
            self.assertContains(page, label)
        r = self.client.post(reverse("patient_tests"), {"test": "spo2"}, follow=True)
        req = MedicalTestRequest.objects.get(patient=self.a)
        self.assertContains(r, req.reference)  # confirmation
        self.assertEqual(self.client.get(reverse("test_data", args=[req.reference])).json()["status"], "pending")
        self.assertEqual(self.client.get(reverse("patient_tests")).status_code, 200)


class NotifySocketTests(TransactionTestCase):
    def setUp(self):
        call_command("seed_data", verbosity=0)

    def _comm(self, user):
        from channels.testing import WebsocketCommunicator
        from config.asgi import application
        from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
        from django.contrib.sessions.backends.db import SessionStore
        s = SessionStore()
        s[SESSION_KEY], s[BACKEND_SESSION_KEY] = str(user.pk), "django.contrib.auth.backends.ModelBackend"
        s[HASH_SESSION_KEY] = user.get_session_auth_hash()
        s.save()
        return WebsocketCommunicator(application, "/ws/notify/", headers=[
            (b"cookie", f"sessionid={s.session_key}".encode()), (b"host", b"localhost"), (b"origin", b"http://localhost")])

    def test_operator_alerted_and_other_patient_hears_nothing(self):
        a, b, op = (User.objects.get(username=n) for n in ("patient1", "patient2", "operator"))
        c_op, c_a, c_b = self._comm(op), self._comm(a), self._comm(b)

        async def run():
            from channels.db import database_sync_to_async
            for c in (c_op, c_a, c_b):
                self.assertTrue((await c.connect())[0])
            req = await database_sync_to_async(svc.create_request)(a, ["temperature"])
            msg = await c_op.receive_json_from(timeout=5)
            self.assertEqual((msg["kind"], msg["reference"]), ("test_request", req.reference))
            await database_sync_to_async(svc.accept)(req, op)
            msg = await c_a.receive_json_from(timeout=5)
            self.assertEqual((msg["kind"], msg["status"]), ("test_update", "accepted"))
            self.assertTrue(await c_b.receive_nothing(timeout=0.5))  # patient B never gets A's events
            for c in (c_op, c_a, c_b):
                await c.disconnect()
        async_to_sync(run)()

    def test_anonymous_rejected(self):
        from channels.testing import WebsocketCommunicator
        from config.asgi import application

        async def run():
            c = WebsocketCommunicator(application, "/ws/notify/", headers=[(b"host", b"localhost"), (b"origin", b"http://localhost")])
            self.assertFalse((await c.connect())[0])
        async_to_sync(run)()
