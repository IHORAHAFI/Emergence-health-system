"""Tests for the virtual patient / virtual IoT device simulation engine."""
import io
from datetime import timedelta

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from emergency.models import EmergencyRequest
from notifications.models import Notification
from . import services, simulation
from .models import SimulationSession, VirtualDevice, VitalSignReading

PATIENT = {"full_name": "Demo Patient", "age": 45, "gender": "female", "weight_kg": 70, "height_cm": 165,
           "medical_history": "Asthma"}


class SimBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_data", verbosity=0)

    def setUp(self):
        self.op = User.objects.get(username="operator")

    def session(self, scenario="normal", **kw):
        vp = simulation.create_virtual_patient(dict(PATIENT), self.op)
        return simulation.create_session(vp, scenario, self.op, **kw)

    def advance(self, s, steps=1):
        """Run `steps` simulated minutes, pretending the tick interval has passed each time."""
        for _ in range(steps):
            SimulationSession.objects.filter(pk=s.pk).update(last_tick_at=timezone.now() - timedelta(seconds=60))
            simulation.tick(s, max_steps=1)
        s.refresh_from_db()
        return s


class TrajectoryTests(SimBase):
    def test_fever_rises_gradually(self):
        s = self.session("fever")
        temps = [simulation.true_values(s, step)["temperature_c"] for step in (0, 2, 7, 12, 22, 40)]
        self.assertEqual(temps[0], simulation.BASELINE["temperature_c"])
        self.assertTrue(all(a <= b for a, b in zip(temps, temps[1:])), temps)  # never goes down
        self.assertAlmostEqual(temps[-1], 39.3)
        self.assertTrue(37.0 < temps[3] < 39.3)  # half-way is in between: gradual, not a jump

    def test_accident_has_sudden_change(self):
        s = self.session("accident")
        before, after = simulation.true_values(s, 2), simulation.true_values(s, 3)
        self.assertEqual(before["heart_rate_bpm"], simulation.BASELINE["heart_rate_bpm"])
        self.assertEqual(after["heart_rate_bpm"], 118)  # jumps at the moment of injury

    def test_values_vary_but_are_reproducible_and_plausible(self):
        s = self.session("normal")
        hrs = {simulation.measured_values(s, i)["heart_rate_bpm"] for i in range(30)}
        self.assertGreater(len(hrs), 3)  # changing numbers, not a fixed value
        self.assertEqual(simulation.measured_values(s, 5), simulation.measured_values(s, 5))
        for i in range(60):
            v = simulation.measured_values(s, i)
            self.assertLess(v["diastolic_mmhg"], v["systolic_mmhg"])
            self.assertLessEqual(v["spo2_pct"], 100)

    def test_custom_scenario(self):
        s = self.session("custom", custom={"heart_rate_bpm": "150", "sudden": True, "start_at": 2})
        self.assertEqual(simulation.true_values(s, 2)["heart_rate_bpm"], 150)


class SessionTests(SimBase):
    def test_creates_simulated_emergency_and_five_devices(self):
        s = self.session("fever")
        self.assertTrue(s.emergency.is_simulated)
        self.assertEqual(s.emergency.location_source, "simulated")
        self.assertFalse(s.virtual_patient.user.is_active)  # placeholder account cannot log in
        self.assertEqual(set(s.devices.values_list("kind", flat=True)),
                         {"thermometer", "pulse_oximeter", "blood_pressure", "heart_rate", "respiratory"})
        self.assertTrue(s.virtual_patient.patient_code.startswith("VP-"))

    def test_refuses_real_emergency(self):
        real = EmergencyRequest.objects.create(patient=User.objects.get(username="patient1"), latitude=-1.95,
                                               longitude=30.06, is_simulated=False)
        vp = simulation.create_virtual_patient(dict(PATIENT), self.op)
        with self.assertRaises(ValueError):
            simulation.create_session(vp, "normal", self.op, emergency=real)
        with self.assertRaises(services.ReadingRejected):
            services.record_reading(real, self.op, {"heart_rate_bpm": 80}, "simulated", simulator=True)

    def test_controls_state_machine(self):
        s = self.session()
        with self.assertRaises(ValueError):
            simulation.control(s, "pause")
        for action, status in [("start", "running"), ("pause", "paused"), ("resume", "running"),
                               ("stop", "stopped")]:
            simulation.control(s, action)
            self.assertEqual(s.status, status)
        with self.assertRaises(ValueError):
            simulation.control(s, "start")  # stopped -> must reset first
        simulation.control(s, "reset")
        self.assertEqual((s.status, s.step), ("ready", 0))

    def test_paused_session_does_not_advance(self):
        s = self.session()
        simulation.control(s, "start")
        self.advance(s, 2)
        simulation.control(s, "pause")
        self.advance(s, 3)
        self.assertEqual(s.step, 2)

    def test_speed(self):
        s = self.session()
        simulation.set_speed(s, 5)
        self.assertAlmostEqual(s.tick_seconds, 0.4)
        with self.assertRaises(ValueError):
            simulation.set_speed(s, 100)


class TransmissionTests(SimBase):
    def test_devices_store_simulated_readings_on_their_schedule(self):
        s = self.session("normal")
        simulation.control(s, "start")
        self.advance(s, 6)
        rs = VitalSignReading.objects.filter(emergency=s.emergency)
        self.assertTrue(rs.exists())
        self.assertFalse(rs.exclude(source="simulated").exists())  # never stored as real
        by_kind = {d.kind: d.transmissions for d in s.devices.all()}
        self.assertEqual(by_kind["pulse_oximeter"], 6)   # every minute
        self.assertEqual(by_kind["respiratory"], 3)      # every 2 minutes
        self.assertEqual(by_kind["thermometer"], 2)      # every 3 minutes
        self.assertEqual(by_kind["blood_pressure"], 1)   # every 5 minutes
        r = rs.filter(device__device_identifier=s.devices.get(kind="thermometer").device_id).first()
        self.assertIsNotNone(r.temperature_c)
        self.assertIsNone(r.heart_rate_bpm)  # each record identifies the device that measured it
        self.assertTrue(r.device.is_simulated)

    def test_disconnected_device_sends_nothing(self):
        s = self.session()
        ox = s.devices.get(kind="pulse_oximeter")
        simulation.device_action(ox, "disconnect")
        simulation.control(s, "start")
        self.advance(s, 3)
        ox.refresh_from_db()
        self.assertEqual(ox.transmissions, 0)
        simulation.device_action(ox, "connect")
        self.advance(s, 1)
        ox.refresh_from_db()
        self.assertEqual(ox.transmissions, 1)

    def test_battery_runs_out(self):
        s = self.session()
        hr = s.devices.get(kind="heart_rate")
        hr.battery_pct = 0.25
        hr.save()
        simulation.control(s, "start")
        self.advance(s, 2)
        hr.refresh_from_db()
        self.assertEqual(hr.state, "battery_empty")
        self.assertEqual(hr.transmissions, 1)
        self.assertTrue(s.events.filter(message__icontains="battery empty").exists())

    def test_step_cannot_be_processed_twice(self):
        s = self.session()
        simulation.control(s, "start")
        SimulationSession.objects.filter(pk=s.pk).update(last_tick_at=timezone.now() - timedelta(seconds=60))
        stale = SimulationSession.objects.get(pk=s.pk)
        simulation.tick(s, max_steps=1)
        # a second caller holding the old step number must not repeat it
        claimed = SimulationSession.objects.filter(pk=stale.pk, step=stale.step).update(step=stale.step + 1)
        self.assertEqual(claimed, 0)

    def test_abnormal_scenario_raises_alert_without_flooding(self):
        s = self.session("critical")
        simulation.control(s, "start")
        self.advance(s, 10)
        self.assertTrue(s.events.filter(level="alert").exists())
        alerts = Notification.objects.filter(emergency=s.emergency, title__startswith="VITALS NEED REVIEW")
        self.assertTrue(alerts.exists())
        titles = list(alerts.values_list("title", "recipient_id", "hospital_id", "channel", "destination"))
        self.assertEqual(len(titles), len(set(titles)))  # each alert sent once per window, not every reading

    def test_management_command_runs_sessions(self):
        s = self.session()
        simulation.control(s, "start")
        SimulationSession.objects.filter(pk=s.pk).update(last_tick_at=timezone.now() - timedelta(seconds=60))
        out = io.StringIO()
        call_command("run_iot_simulator", "--once", stdout=out)
        self.assertIn("SIMULATED", out.getvalue())
        s.refresh_from_db()
        self.assertEqual(s.step, 1)


class PageTests(SimBase):
    def test_permissions(self):
        for user, pw in (("patient1", "patient123"), ("chuk_staff", "hospital123")):
            self.client.login(username=user, password=pw)
            self.assertEqual(self.client.get(reverse("simulator")).status_code, 403)
            self.client.logout()

    def test_full_flow_through_pages(self):
        self.client.login(username="operator", password="operator123")
        self.assertEqual(self.client.get(reverse("simulator")).status_code, 200)
        r = self.client.post(reverse("simulator"), {**PATIENT, "scenario": "low_spo2", "speed": "2"})
        s = SimulationSession.objects.get()
        self.assertRedirects(r, reverse("sim_panel", args=[s.id]))
        self.assertContains(self.client.get(reverse("sim_panel", args=[s.id])), "SIMULATED")
        d = self.client.post(reverse("sim_control", args=[s.id]), {"action": "start"},
                             content_type="application/json").json()
        self.assertEqual(d["status"], "running")
        SimulationSession.objects.filter(pk=s.pk).update(last_tick_at=timezone.now() - timedelta(seconds=60))
        d = self.client.post(reverse("sim_tick", args=[s.id])).json()
        self.assertEqual(d["step"], 1)
        self.assertTrue(d["readings"])
        self.assertTrue(d["simulated"])
        dev = VirtualDevice.objects.filter(session=s).first()
        d = self.client.post(reverse("sim_device", args=[s.id, dev.id]), {"action": "disconnect"},
                             content_type="application/json").json()
        self.assertEqual(next(x for x in d["devices"] if x["id"] == dev.id)["state"], "disconnected")
        bad = self.client.post(reverse("sim_control", args=[s.id]), {"action": "resume"}, content_type="application/json")
        self.assertEqual(bad.status_code, 400)

    def test_custom_scenario_needs_a_target(self):
        self.client.login(username="operator", password="operator123")
        self.client.post(reverse("simulator"), {**PATIENT, "scenario": "custom"})
        self.assertFalse(SimulationSession.objects.exists())


class ThresholdAlertTests(SimBase):
    def reading(self, s, **values):
        return services.record_reading(s.emergency, self.op, values, "simulated",
                                       {"device_identifier": "VTH-TEST", "name": "Thermometer", "kind": "thermometer"},
                                       simulator=True)

    def test_value_above_threshold_creates_alert_with_device_and_time(self):
        from .models import EmergencyAlert
        s = self.session()
        self.reading(s, temperature_c=38.6)
        a = EmergencyAlert.objects.get(emergency=s.emergency)
        self.assertEqual((a.parameter, a.value, a.limit), ("temperature_c", 38.6, "> 38"))
        self.assertEqual(a.device.device_identifier, "VTH-TEST")
        self.assertTrue(a.is_simulated and "[SIMULATED]" in a.message)
        self.assertIsNotNone(a.created_at)
        # the existing workflow is notified
        self.assertTrue(Notification.objects.filter(emergency=s.emergency, title__startswith="VITALS NEED REVIEW").exists())

    def test_normal_value_no_alert_and_no_flood(self):
        from .models import EmergencyAlert
        s = self.session()
        self.reading(s, temperature_c=37.0)
        self.assertFalse(EmergencyAlert.objects.exists())
        for _ in range(5):
            self.reading(s, temperature_c=39.0)
        self.assertEqual(EmergencyAlert.objects.filter(emergency=s.emergency).count(), 1)

    def test_child_limits_used_for_children(self):
        from .models import EmergencyAlert
        vp = simulation.create_virtual_patient({**PATIENT, "age": 6}, self.op)
        s = simulation.create_session(vp, "normal", self.op)
        self.reading(s, heart_rate_bpm=130)  # above adult 120, below child 140
        self.assertFalse(EmergencyAlert.objects.filter(emergency=s.emergency).exists())
        self.reading(s, heart_rate_bpm=150)
        self.assertTrue(EmergencyAlert.objects.filter(emergency=s.emergency).exists())

    def test_alerts_page_acknowledge_and_thresholds(self):
        from .models import AlertThreshold, EmergencyAlert
        s = self.session()
        self.reading(s, spo2_pct=85)
        a = EmergencyAlert.objects.get()
        self.client.login(username="operator", password="operator123")
        self.assertContains(self.client.get(reverse("vitals_alerts")), "SpO")
        self.assertContains(self.client.get(reverse("dashboard")), "vital-sign alert")
        self.client.post(reverse("vitals_alerts"), {"action": "ack", "alert": a.id})
        a.refresh_from_db()
        self.assertEqual((a.status, a.acknowledged_by.username), ("acknowledged", "operator"))
        t = AlertThreshold.objects.get(parameter="temperature_c", age_group="adult")
        self.client.post(reverse("vitals_alerts"), {"action": "thresholds", f"high_{t.id}": "37.5"})
        t.refresh_from_db()
        self.assertEqual(t.high, 38.0)  # operators cannot change thresholds
        self.client.logout()
        self.client.login(username="admin", password="admin12345")
        data = {"action": "thresholds"}
        for x in AlertThreshold.objects.all():
            data[f"low_{x.id}"] = "" if x.low is None else str(x.low)
            data[f"high_{x.id}"] = "" if x.high is None else str(x.high)
            data[f"on_{x.id}"] = "on"
        data[f"high_{t.id}"] = "37.5"
        self.client.post(reverse("vitals_alerts"), data)
        t.refresh_from_db()
        self.assertEqual(t.high, 37.5)

    def test_patient_cannot_see_alerts_page(self):
        self.client.login(username="patient1", password="patient123")
        self.assertEqual(self.client.get(reverse("vitals_alerts")).status_code, 403)
