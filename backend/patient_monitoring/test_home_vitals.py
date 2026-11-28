"""Home Vital Signs: camera heart rate + SIMULATED temperature / blood pressure for a logged-in patient."""
import uuid
from datetime import timedelta

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from emergency.models import EmergencyRequest
from . import home_vitals as hv, simulation
from .models import PatientMeasurement as PM, SimulationSession, VitalSignReading as VSR


def advance(session, steps=1):
    """Run steps immediately (no waiting for the real-time interval)."""
    for _ in range(steps):
        SimulationSession.objects.filter(pk=session.pk).update(last_tick_at=timezone.now() - timedelta(hours=1))
        simulation.tick(session, max_steps=1)
        session.refresh_from_db()


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_data", verbosity=0)

    def setUp(self):
        self.p1 = User.objects.get(username="patient1")
        self.p2 = User.objects.get(username="patient2")
        self.op = User.objects.get(username="operator")

    def sim_rows(self, p):
        return VSR.objects.filter(patient=p, source="simulated", emergency=None, test_request=None)


class SimulatorTests(Base):
    def test_generates_only_temperature_and_blood_pressure(self):
        s = hv.start(self.p1, self.op, "normal", 5)
        advance(s, 3)
        rows = list(self.sim_rows(self.p1))
        self.assertTrue(rows)
        for r in rows:
            self.assertIsNone(r.heart_rate_bpm)  # heart rate comes only from the camera
            self.assertIsNone(r.spo2_pct)
            self.assertIsNone(r.respiratory_rate)
            self.assertEqual(r.source, "simulated")
            self.assertIsNone(r.emergency_id)
        self.assertTrue(any(r.temperature_c is not None for r in rows))
        bp = [r for r in rows if r.systolic_mmhg is not None]
        self.assertTrue(bp)
        for r in bp:
            self.assertIsNotNone(r.diastolic_mmhg)
            self.assertGreater(r.systolic_mmhg, r.diastolic_mmhg)

    def test_values_change_coherently_towards_scenario(self):
        s = hv.start(self.p1, self.op, "fever", 5)
        advance(s, 25)
        temps = [float(r.temperature_c) for r in self.sim_rows(self.p1).filter(temperature_c__isnull=False)
                 .order_by("measured_at", "id")]
        self.assertGreater(temps[-1], 38.5)
        self.assertLess(temps[0], 37.5)
        steps = [abs(b - a) for a, b in zip(temps, temps[1:])]
        self.assertLess(max(steps), 1.0)  # gradual, not random jumps

    def test_one_generator_per_patient_and_stop(self):
        hv.start(self.p1, self.op, "normal", 10)
        hv.start(self.p1, self.op, "hypertension", 10)
        self.assertEqual(SimulationSession.objects.filter(patient=self.p1, status="running").count(), 1)
        self.assertEqual(hv.stop(self.p1, self.op), 1)
        self.assertFalse(SimulationSession.objects.filter(patient=self.p1, status="running").exists())

    def test_validation_of_controls(self):
        with self.assertRaises(hv.HomeVitalsError):
            hv.start(self.p1, self.p2, "normal", 10)  # a patient cannot start it
        with self.assertRaises(hv.HomeVitalsError):
            hv.start(self.p1, self.op, "normal", 1)  # interval too short
        with self.assertRaises(hv.HomeVitalsError):
            hv.start(self.op, self.op, "normal", 10)  # only patient accounts

    def test_never_creates_emergency_or_alert(self):
        before_em = EmergencyRequest.objects.count()
        s = hv.start(self.p1, self.op, "hypertension", 5)
        advance(s, 20)
        self.assertEqual(EmergencyRequest.objects.count(), before_em)
        from .models import EmergencyAlert
        self.assertFalse(EmergencyAlert.objects.filter(reading__patient=self.p1).exists())

    def test_history_is_capped(self):
        old = hv.KEEP_PER_PATIENT
        hv.KEEP_PER_PATIENT = 5
        try:
            s = hv.start(self.p1, self.op, "normal", 5)
            advance(s, 6)
            self.assertLessEqual(self.sim_rows(self.p1).count(), 5)
        finally:
            hv.KEEP_PER_PATIENT = old

    def test_long_interval_respected(self):
        s = hv.start(self.p1, self.op, "normal", 120)
        self.assertEqual(hv.interval_of(s), 120)


class ApiTests(Base):
    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(self.p1)

    def test_patient_sees_three_vitals_with_sources(self):
        PM.objects.create(client_id=uuid.uuid4(), patient=self.p1, kind="heart_rate", status="estimated", value=74,
                          unit="bpm", source="phone_camera", method="ppg", measured_at=timezone.now(),
                          quality={"quality": 0.81})
        s = hv.start(self.p1, self.op, "normal", 5)
        advance(s, 2)
        d = self.api.get("/api/vitals/mine/").json()
        self.assertEqual(d["heart_rate"]["latest"]["value"], 74.0)
        self.assertEqual(d["heart_rate"]["latest"]["source"], "CAMERA_MEASURED_ESTIMATE")
        self.assertEqual(d["heart_rate"]["latest"]["method"], "SMARTPHONE_CAMERA_PPG")
        self.assertEqual(d["temperature"]["latest"]["source"], "SIMULATED")
        self.assertEqual(d["temperature"]["latest"]["unit"], "°C")
        bp = d["blood_pressure"]["latest"]
        self.assertEqual(bp["source"], "SIMULATED")
        self.assertEqual(bp["value"], f"{bp['systolic']}/{bp['diastolic']}")
        self.assertTrue(d["simulation"]["running"])

    def test_failed_camera_test_is_not_a_heart_rate(self):
        PM.objects.create(client_id=uuid.uuid4(), patient=self.p1, kind="heart_rate", status="inconclusive",
                          unit="bpm", source="phone_camera", method="ppg", measured_at=timezone.now(),
                          failure_reason="signal too noisy")
        d = self.api.get("/api/vitals/mine/").json()
        self.assertIsNone(d["heart_rate"]["latest"])
        self.assertEqual(d["heart_rate"]["last_attempt"]["status"], "INCONCLUSIVE")
        text = hv.ai_context(self.p1)
        self.assertIn("NO heart-rate value", text)
        self.assertIn("no successful camera measurement", text)

    def test_patient_only_sees_own_data(self):
        s = hv.start(self.p2, self.op, "normal", 5)
        advance(s, 2)
        d = self.api.get("/api/vitals/mine/").json()
        self.assertIsNone(d["temperature"]["latest"])
        self.assertFalse(d["simulation"]["running"])

    def test_operator_cannot_use_patient_endpoint(self):
        api = APIClient()
        api.force_authenticate(self.op)
        self.assertEqual(api.get("/api/vitals/mine/").status_code, 403)


class OperatorDashboardTests(Base):
    def test_feed_and_controls(self):
        self.client.force_login(self.op)
        r = self.client.post("/dashboard/home-vitals/control/", {"patient": self.p1.id, "action": "start",
                                                                  "scenario": "fever", "interval": 5})
        self.assertEqual(r.status_code, 200, r.content)
        advance(SimulationSession.objects.get(patient=self.p1, status="running"), 2)
        d = self.client.get(f"/dashboard/home-vitals.json?patient={self.p1.id}").json()
        row = next(p for p in d["patients"] if p["patient_id"] == self.p1.id)
        self.assertEqual(row["temperature"]["source"], "SIMULATED")
        self.assertTrue(row["simulation"]["running"])
        self.assertTrue(d["selected"]["blood_pressure"]["history"])
        r = self.client.post("/dashboard/home-vitals/control/", {"patient": self.p1.id, "action": "stop"})
        self.assertEqual(r.json()["stopped"], 1)

    def test_patient_cannot_open_operator_feed_or_control(self):
        self.client.force_login(self.p1)
        self.assertEqual(self.client.get("/dashboard/home-vitals.json").status_code, 403)
        r = self.client.post("/dashboard/home-vitals/control/", {"patient": self.p1.id, "action": "start"})
        self.assertEqual(r.status_code, 403)


class AiContextTests(Base):
    def test_labels_simulated_values(self):
        PM.objects.create(client_id=uuid.uuid4(), patient=self.p1, kind="heart_rate", status="estimated", value=80,
                          unit="bpm", source="phone_camera", method="ppg", measured_at=timezone.now(),
                          quality={"quality": 0.7})
        s = hv.start(self.p1, self.op, "normal", 5)
        advance(s, 1)
        text = hv.ai_context(self.p1)
        self.assertIn("80 BPM - CAMERA_MEASURED_ESTIMATE", text)
        self.assertIn("signal quality 70%", text)
        self.assertIn("SIMULATED demonstration value, NOT a real measurement", text)
        self.assertIn("never recommend or trigger emergency dispatch", text)

    def test_missing_values_are_not_invented(self):
        text = hv.ai_context(self.p2)
        self.assertIn("Body temperature: not available", text)
        self.assertIn("Blood pressure: not available", text)
