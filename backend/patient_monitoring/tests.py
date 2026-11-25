from unittest import mock

from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.core.management import call_command
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from emergency import services as em_services
from hospitals.models import Hospital
from notifications.models import Notification
from . import clinical, services
from .models import AccessAuditLog, MedicalDevice, VitalSignReading

NORMAL = {"temperature_c": 36.8, "heart_rate_bpm": 72, "spo2_pct": 98, "systolic_mmhg": 120,
          "diastolic_mmhg": 78, "respiratory_rate": 16, "consciousness": "alert"}


class News2Tests(TestCase):
    def test_normal_full_set_scores_zero(self):
        a = clinical.assess(NORMAL)
        self.assertEqual((a["score"], a["partial"], a["risk"]), (0, False, "low"))

    def test_textbook_bands(self):
        self.assertEqual(clinical.score_spo2(91), 3)
        self.assertEqual(clinical.score_spo2(94), 1)
        self.assertEqual(clinical.score_systolic(90), 3)
        self.assertEqual(clinical.score_systolic(220), 3)
        self.assertEqual(clinical.score_pulse(131), 3)
        self.assertEqual(clinical.score_pulse(45), 1)
        self.assertEqual(clinical.score_temperature(39.1), 2)
        self.assertEqual(clinical.score_temperature(35.0), 3)
        self.assertEqual(clinical.score_respiration(25), 3)

    def test_single_red_parameter_is_low_medium_with_explanation(self):
        a = clinical.assess({**NORMAL, "spo2_pct": 90})
        self.assertEqual((a["score"], a["risk"]), (3, "low_medium"))
        self.assertIn("SpO2", a["alerts"][0]["explanation"])

    def test_high_risk(self):
        a = clinical.assess({"spo2_pct": 88, "heart_rate_bpm": 135, "systolic_mmhg": 88, "respiratory_rate": 28,
                             "temperature_c": 36.5, "consciousness": "alert"})
        self.assertEqual(a["risk"], "high")
        self.assertGreaterEqual(a["score"], 7)

    def test_partial_set_flagged(self):
        a = clinical.assess({"temperature_c": 37.0})
        self.assertTrue(a["partial"])
        self.assertTrue(any(x["severity"] == "info" for x in a["alerts"]))

    def test_diastolic_crisis_flag(self):
        a = clinical.assess({**NORMAL, "diastolic_mmhg": 125})
        self.assertTrue(any("Diastolic" in x["parameter"] for x in a["alerts"]))


class MonitoringBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_data", verbosity=0)

    def setUp(self):
        self.patient = User.objects.get(username="patient1")
        self.em = em_services.create_emergency(self.patient, {
            "emergency_type": "respiratory", "latitude": -1.9564, "longitude": 30.0601, "breathing_difficulty": True})
        self.api = APIClient()
        self.api.force_authenticate(self.patient)
        self.url = reverse("api_vitals", args=[self.em.reference])

    def consent(self):
        self.assertEqual(self.api.post(reverse("api_vitals_consent", args=[self.em.reference])).status_code, 200)


class SubmissionTests(MonitoringBase):
    def test_consent_required(self):
        r = self.api.post(self.url, {"heart_rate_bpm": 80}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Consent", r.data["detail"])

    def test_manual_reading_saved_with_news2(self):
        self.consent()
        r = self.api.post(self.url, {"source": "manual", **NORMAL}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        reading = VitalSignReading.objects.get()
        self.assertEqual((reading.source, reading.news2_score, reading.patient, reading.emergency),
                         ("manual", 0, self.patient, self.em))
        self.assertTrue(AccessAuditLog.objects.filter(action="reading_submitted").exists())

    def test_bluetooth_reading_records_device(self):
        self.consent()
        r = self.api.post(self.url, {"source": "bluetooth", "spo2_pct": 97, "heart_rate_bpm": 70,
                                     "measured_at": "2026-11-22T10:00:00Z",
                                     "device": {"device_identifier": "abc123==", "name": "PO3", "kind": "pulse_oximeter"}},
                          format="json")
        self.assertEqual(r.status_code, 201, r.data)
        dev = MedicalDevice.objects.get()
        self.assertEqual((dev.kind, dev.is_simulated), ("pulse_oximeter", False))
        self.assertEqual(VitalSignReading.objects.get().device, dev)

    def test_bluetooth_without_device_rejected(self):
        self.consent()
        self.assertEqual(self.api.post(self.url, {"source": "bluetooth", "spo2_pct": 97}, format="json").status_code, 400)

    def test_empty_reading_rejected_device_disconnected(self):
        self.consent()
        r = self.api.post(self.url, {"source": "manual"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("no measurement", r.data["detail"])

    def test_implausible_values_rejected(self):
        self.consent()
        for bad in ({"temperature_c": 47}, {"spo2_pct": 30}, {"heart_rate_bpm": 400},
                    {"systolic_mmhg": 80, "diastolic_mmhg": 90}, {"systolic_mmhg": 120}):
            r = self.api.post(self.url, {"source": "manual", **bad}, format="json")
            self.assertEqual(r.status_code, 400, bad)
        self.assertEqual(VitalSignReading.objects.count(), 0)

    def test_future_timestamp_rejected(self):
        self.consent()
        r = self.api.post(self.url, {"heart_rate_bpm": 70, "measured_at": "2099-01-01T00:00:00Z"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_float_temperatures_always_accepted(self):
        self.consent()
        for t in (36.1, 37.3, 38.7, 39.9):
            r = services.record_reading(self.em, self.patient, {"temperature_c": t}, "simulated")
            self.assertEqual(str(r.temperature_c), str(t))

    def test_simulated_reading_clearly_labelled(self):
        self.consent()
        r = self.api.post(reverse("api_vitals_simulate", args=[self.em.reference]), {"scenario": "hypoxia"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertTrue(r.data["simulated"])
        reading = VitalSignReading.objects.get()
        self.assertEqual(reading.source, "simulated")
        self.assertTrue(reading.device.is_simulated)
        self.assertIn("[SIMULATED]", services.vitals_text(reading))

    def test_revoked_consent_blocks(self):
        self.consent()
        self.api.delete(reverse("api_vitals_consent", args=[self.em.reference]))
        self.assertEqual(self.api.post(self.url, {"heart_rate_bpm": 70}, format="json").status_code, 400)

    def test_paramedic_crew_can_submit(self):
        c = APIClient()
        c.force_authenticate(self.em.assigned_ambulance.driver)
        self.assertEqual(c.post(self.url, {"source": "manual", **NORMAL}, format="json").status_code, 201)

    def test_completed_emergency_rejects(self):
        self.consent()
        self.em.status = "completed"
        self.em.save()
        self.assertEqual(self.api.post(self.url, NORMAL, format="json").status_code, 400)


class AlertTests(MonitoringBase):
    def test_abnormal_reading_alerts_hospital_with_explanation(self):
        self.consent()
        self.api.post(self.url, {"spo2_pct": 86, "heart_rate_bpm": 132, "respiratory_rate": 28}, format="json")
        n = Notification.objects.filter(hospital=self.em.assigned_hospital, title__startswith="VITALS NEED REVIEW").first()
        self.assertIsNotNone(n)
        self.assertIn("SpO2", n.message)
        self.assertIn(self.em.reference, n.title)
        self.assertTrue(Notification.objects.filter(recipient__username="operator", title__startswith="VITALS").exists())

    def test_normal_reading_is_in_app_only(self):
        self.consent()
        before = Notification.objects.filter(channel="sms").count()
        self.api.post(self.url, NORMAL, format="json")
        self.assertTrue(Notification.objects.filter(title__startswith="New vital signs").exists())
        self.assertEqual(Notification.objects.filter(channel="sms").count(), before)

    def test_notification_failure_keeps_measurement(self):
        self.consent()
        with mock.patch.object(services, "alert_staff", side_effect=RuntimeError("SMS gateway down")), \
             mock.patch.object(services, "broadcast", side_effect=RuntimeError("ws down")):
            r = self.api.post(self.url, NORMAL, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(VitalSignReading.objects.count(), 1)

    def test_advance_hospital_notification_contains_vitals_and_time(self):
        self.consent()
        self.api.post(self.url, NORMAL, format="json")
        em_services.dispatch(self.em, Hospital.objects.get(name__startswith="King Faisal").id)
        n = Notification.objects.filter(hospital__name__startswith="King Faisal", title__startswith="Incoming").first()
        self.assertIn("Latest vitals", n.message)
        self.assertIn("SpO2 98%", n.message)
        self.assertIn(self.em.reference, n.message)
        self.assertIn("Ambulance", n.message)


class AccessTests(MonitoringBase):
    def setUp(self):
        super().setUp()
        self.consent()
        self.reading = services.record_reading(self.em, self.patient, {"spo2_pct": 90}, "manual")

    def _client(self, username):
        c = APIClient()
        c.force_authenticate(User.objects.get(username=username))
        return c

    def test_operator_views_but_cannot_review(self):
        c = self._client("operator")
        self.assertEqual(c.get(self.url).status_code, 200)
        self.assertEqual(c.post(reverse("api_vitals_review", args=[self.reading.id]),
                                {"decision": "confirm"}, format="json").status_code, 403)
        self.assertTrue(AccessAuditLog.objects.filter(action="vitals_viewed_api").exists())

    def test_assigned_hospital_staff_can_review_and_override(self):
        self.em.assigned_hospital = Hospital.objects.get(name__startswith="CHUK")
        self.em.save()
        c = self._client("chuk_staff")
        r = c.post(reverse("api_vitals_review", args=[self.reading.id]),
                   {"decision": "override", "override_risk": "medium", "note": "looks worse in person"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(self.reading.reviews.get().override_risk, "medium")
        self.assertTrue(AccessAuditLog.objects.filter(action="reading_override").exists())

    def test_other_hospital_and_unapproved_staff_denied(self):
        self.em.assigned_hospital = Hospital.objects.get(name__startswith="Masaka")
        self.em.save()
        self.assertEqual(self._client("chuk_staff").get(self.url).status_code, 403)
        self.assertEqual(self._client("nurse_pending").get(self.url).status_code, 403)

    def test_other_patient_denied(self):
        User.objects.create_user("p9", password="Xx-secure-99", role="patient")
        self.assertEqual(self._client("p9").get(self.url).status_code, 403)
        self.assertEqual(self._client("p9").post(self.url, NORMAL, format="json").status_code, 400)

    def test_web_pages(self):
        self.client.force_login(User.objects.get(username="operator"))
        self.assertEqual(self.client.get(reverse("vitals_board")).status_code, 200)
        self.assertContains(self.client.get(reverse("vitals_detail", args=[self.em.reference])), "NEWS2")
        self.client.force_login(self.patient)
        self.assertEqual(self.client.get(reverse("vitals_board")).status_code, 403)
        self.assertContains(self.client.get(reverse("vitals_measure", args=[self.em.reference])), "Bluetooth devices")


class WebSocketTests(TransactionTestCase):
    def setUp(self):
        call_command("seed_data", verbosity=0)
        self.patient = User.objects.get(username="patient1")
        self.em = em_services.create_emergency(self.patient, {"emergency_type": "other", "latitude": -1.95,
                                                              "longitude": 30.06})
        services.give_consent(self.em, self.patient)

    def _communicator(self, user, path):
        from config.asgi import application
        from django.contrib.sessions.backends.db import SessionStore
        from django.contrib.auth import SESSION_KEY, BACKEND_SESSION_KEY, HASH_SESSION_KEY
        s = SessionStore()
        s[SESSION_KEY], s[BACKEND_SESSION_KEY] = str(user.pk), "django.contrib.auth.backends.ModelBackend"
        s[HASH_SESSION_KEY] = user.get_session_auth_hash()
        s.save()
        return WebsocketCommunicator(application, path, headers=[
            (b"cookie", f"sessionid={s.session_key}".encode()), (b"host", b"localhost"), (b"origin", b"http://localhost")])

    def test_live_update_to_authorized_viewer(self):
        comm = self._communicator(User.objects.get(username="operator"), f"/ws/vitals/{self.em.reference}/")

        async def run():
            connected, _ = await comm.connect()
            self.assertTrue(connected)
            from channels.db import database_sync_to_async
            await database_sync_to_async(services.record_reading)(self.em, self.patient, {"heart_rate_bpm": 140}, "manual")
            msg = await comm.receive_json_from(timeout=5)
            self.assertEqual(msg["reading"]["heart_rate_bpm"], 140)
            await comm.disconnect()
        async_to_sync(run)()

    def test_unauthorized_rejected(self):
        other = User.objects.create_user("p8", password="Xx-secure-88", role="patient")
        comm = self._communicator(other, f"/ws/vitals/{self.em.reference}/")

        async def run():
            connected, _ = await comm.connect()
            self.assertFalse(connected)
        async_to_sync(run)()
