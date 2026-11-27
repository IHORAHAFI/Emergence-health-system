"""Quick measurements: automatic submission, validation, idempotent retries, dashboard push, feed + permissions."""
import uuid
from datetime import timedelta
from unittest import mock

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from emergency import services as em_services
from . import quick_measurements as qm
from .models import PatientMeasurement as PM

URL = "/api/measurements/"


def hr(**kw):
    return {"client_id": str(uuid.uuid4()), "kind": "heart_rate", "status": "estimated", "value": 72,
            "source": "phone_camera", "method": "ppg", "measured_at": timezone.now().isoformat(), "duration_s": 34.2,
            "device": {"name": "Phone camera", "platform": "android"}, "quality": {"score": 0.82}, **kw}


def temp(**kw):
    return {"client_id": str(uuid.uuid4()), "kind": "temperature", "status": "measured", "value": 37.24,
            "source": "ble_thermometer", "method": "ble", "measured_at": timezone.now().isoformat(),
            "device": {"device_identifier": "AA:BB:CC:DD", "name": "ThermoX"}, **kw}


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_data", verbosity=0)

    def setUp(self):
        self.p1 = User.objects.get(username="patient1")
        self.p2 = User.objects.get(username="patient2")
        self.op = User.objects.get(username="operator")
        self.staff = User.objects.get(username="chuk_staff")
        self.api = APIClient()
        self.api.force_authenticate(self.p1)

    def post(self, body, user=None):
        if user:
            self.api.force_authenticate(user)
        return self.api.post(URL, body, format="json")


class SubmitTests(Base):
    def test_heart_rate_saved_and_confirmed(self):
        r = self.post(hr())
        self.assertEqual(r.status_code, 201, r.data)
        self.assertTrue(r.data["saved"])
        self.assertFalse(r.data["duplicate"])
        self.assertTrue(r.data["dashboard_notified"])
        m = PM.objects.get()
        self.assertEqual((m.kind, m.status, float(m.value), m.unit), ("heart_rate", "estimated", 72.0, "bpm"))
        self.assertIsNotNone(m.dashboard_notified_at)
        self.assertEqual(r.data["measurement"]["status_display"], "ESTIMATED")

    def test_temperature_saved_with_one_decimal(self):
        r = self.post(temp())
        self.assertEqual(r.status_code, 201, r.data)
        m = PM.objects.get()
        self.assertEqual((str(m.value), m.unit, m.method), ("37.2", "°C", "ble"))

    def test_failed_and_inconclusive_are_stored_without_value(self):
        for status in ("failed", "inconclusive"):
            r = self.post(hr(status=status, value=None, failure_reason="timeout: no valid signal"))
            self.assertEqual(r.status_code, 201, r.data)
            self.assertIsNone(r.data["measurement"]["value"])
            self.assertFalse(r.data["measurement"]["valid"])
        self.assertEqual(PM.objects.filter(value__isnull=True).count(), 2)

    def test_attaches_the_patients_active_emergency(self):
        em = em_services.create_emergency(self.p1, {"emergency_type": "other", "latitude": -1.95, "longitude": 30.06,
                                                    "location_source": "gps", "location_consent": True})
        r = self.post(temp())
        self.assertEqual(r.data["measurement"]["emergency"], em.reference)


class ValidationTests(Base):
    def test_rejects_invalid_results(self):
        bad = [
            hr(status="failed"),                                    # failed but carries a value
            hr(status="failed", value=None),                        # failed without a reason
            hr(status="measured"),                                  # camera PPG can never be "measured"
            hr(value=None),                                         # completed without a value
            hr(value=300),                                          # implausible
            hr(source="ble_thermometer"),                           # wrong source for heart rate
            temp(method="ppg"),                                     # wrong method for temperature
            temp(status="estimated"),
            temp(value=47.0),
            temp(device={}),                                        # thermometer must be identified
            temp(measured_at=(timezone.now() + timedelta(hours=1)).isoformat()),
            temp(measured_at=(timezone.now() - timedelta(days=8)).isoformat()),
            {**hr(), "client_id": "not-a-uuid"},
        ]
        for body in bad:
            with self.subTest(body=body):
                self.assertEqual(self.post(body).status_code, 400)
        self.assertFalse(PM.objects.exists())

    def test_only_patients_submit(self):
        self.assertEqual(self.post(hr(), user=self.op).status_code, 403)
        self.assertEqual(APIClient().post(URL, hr(), format="json").status_code, 401)

    def test_cannot_attach_someone_elses_emergency(self):
        em = em_services.create_emergency(self.p2, {"emergency_type": "other", "latitude": -1.95, "longitude": 30.06,
                                                    "location_source": "gps", "location_consent": True})
        self.assertEqual(self.post(hr(emergency=em.reference)).status_code, 400)


class IdempotencyTests(Base):
    def test_retry_returns_the_same_row(self):
        body = hr()
        first, again = self.post(body), self.post(body)
        self.assertEqual((first.status_code, again.status_code), (201, 200))
        self.assertTrue(again.data["duplicate"])
        self.assertEqual(first.data["measurement"]["id"], again.data["measurement"]["id"])
        self.assertEqual(PM.objects.count(), 1)

    def test_same_id_from_another_patient_is_a_conflict(self):
        body = hr()
        self.post(body)
        self.assertEqual(self.post(body, user=self.p2).status_code, 409)
        self.assertEqual(PM.objects.count(), 1)

    def test_retry_republishes_when_the_first_push_failed(self):
        body = hr()
        with mock.patch("channels.layers.get_channel_layer", side_effect=RuntimeError("layer down")):
            r = self.post(body)
        self.assertEqual(r.status_code, 201)               # still saved
        self.assertFalse(r.data["dashboard_notified"])
        self.assertIsNone(PM.objects.get().dashboard_notified_at)
        r = self.post(body)
        self.assertTrue(r.data["dashboard_notified"])
        self.assertIsNotNone(PM.objects.get().dashboard_notified_at)


class PushTests(Base):
    def test_operators_group_gets_a_nudge_after_saving(self):
        layer = get_channel_layer()
        async_to_sync(layer.group_add)("operators", "test-channel")
        r = self.post(temp())
        msg = async_to_sync(layer.receive)("test-channel")
        self.assertEqual((msg["type"], msg["kind"], msg["id"]), ("notify.event", "measurement",
                                                                r.data["measurement"]["id"]))
        self.assertTrue(PM.objects.filter(pk=msg["id"]).exists())   # pushed only once it is in the database
        self.assertNotIn("value", msg)                               # the socket carries no medical data


class FeedTests(Base):
    def feed(self, user, **q):
        self.client.force_login(user)
        return self.client.get(reverse("measurements_feed"), q)

    def test_operator_sees_newest_first_with_latest_valid_values(self):
        self.post(temp(measured_at=(timezone.now() - timedelta(minutes=5)).isoformat()))
        self.post(hr())
        self.post(temp(status="failed", value=None, failure_reason="timeout"))
        d = self.feed(self.op).json()
        self.assertEqual([m["status"] for m in d["measurements"]], ["failed", "estimated", "measured"])
        latest = d["latest"][0]
        self.assertEqual(latest["temperature"]["status"], "measured")      # failure does not hide the real value
        self.assertEqual(latest["last_attempt"]["status"], "failed")

    def test_access(self):
        self.post(hr())
        self.assertEqual(self.feed(self.p1).status_code, 403)
        self.assertEqual(self.feed(self.staff).json()["measurements"], [])  # not at this hospital's emergency
        self.client.logout()
        self.assertEqual(self.client.get(reverse("measurements_feed")).status_code, 302)

    def test_hospital_staff_see_their_hospitals_patients(self):
        em = em_services.create_emergency(self.p1, {"emergency_type": "other", "latitude": -1.95, "longitude": 30.06,
                                                    "location_source": "gps", "location_consent": True})
        em.assigned_hospital = self.staff.hospital
        em.save(update_fields=["assigned_hospital"])
        self.post(hr())
        rows = self.feed(self.staff, emergency=em.id).json()["measurements"]
        self.assertEqual(len(rows), 1)

    def test_dashboard_pages_render_the_panel(self):
        self.client.force_login(self.op)
        self.assertContains(self.client.get(reverse("dashboard")), "data-measurements")
