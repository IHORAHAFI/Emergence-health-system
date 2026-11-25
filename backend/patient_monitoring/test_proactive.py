"""Proactive assistant: home monitoring, AI-initiated check-ins, no-response escalation, caregivers.
Uses SIMULATED test data, labelled as such (scenario numbers refer to the project brief, section 20)."""
from datetime import timedelta

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import CaregiverLink, User
from emergency.models import EmergencyRequest
from notifications.models import Notification
from . import proactive
from .models import MonitoringEvent as E

DEVICE = {"device_identifier": "AA:BB:CC", "name": "Oximeter", "kind": "pulse_oximeter"}


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_data", verbosity=0)

    def setUp(self):
        self.p = User.objects.get(username="patient1")
        self.p2 = User.objects.get(username="patient2")
        self.op = User.objects.get(username="operator")
        self.api = APIClient()
        self.api.force_authenticate(self.p)
        proactive.give_home_consent(self.p)

    def reading(self, **values):
        r = self.api.post("/api/home-monitoring/readings/", {**values, "device": DEVICE}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        return r.data

    def notes(self, user, title):
        return Notification.objects.filter(recipient=user, channel="in_app", title__startswith=title).count()

    def later(self, ev, seconds):
        return ev.last_prompt_at + timedelta(seconds=seconds)


class MonitoringTests(Base):
    def test_10_normal_reading_no_event(self):
        d = self.reading(heart_rate_bpm=72, spo2_pct=98, respiratory_rate=14)
        self.assertIsNone(d["event"])
        self.assertFalse(E.objects.exists())

    def test_one_borderline_reading_is_not_enough(self):
        self.assertIsNone(self.reading(temperature_c=38.6)["event"])   # single concern: wait for confirmation
        d = self.reading(temperature_c=38.7)                           # second within 15 min -> event
        self.assertEqual(d["event"]["severity"], "concern")
        ev = E.objects.get()
        self.assertEqual((ev.status, ev.readings.count(), ev.is_simulated), ("awaiting_response", 2, False))
        self.assertEqual(self.notes(self.p, "Your medical assistant is checking on you"), 1)  # 11: AI starts it

    def test_9_severe_real_reading_escalates_immediately(self):
        d = self.reading(spo2_pct=84, heart_rate_bpm=128)
        ev = E.objects.get(pk=d["event"]["id"])
        self.assertEqual((ev.severity, ev.status), ("severe", "escalated"))
        em = ev.emergency
        self.assertIsNotNone(em)                                       # 19: reaches the operator dashboard
        self.assertIsNone(em.accepted_at)                              # 25: open until a human accepts
        self.assertEqual(em.patient_condition, "unknown")
        self.assertTrue(em.breathing_difficulty)
        self.assertIn("REAL", ev.summary)                              # 22: AI summary
        self.assertEqual(self.notes(self.op, "AUTOMATIC ALERT"), 1)    # 20: staff notified
        self.client.force_login(self.op)
        keys = [a["key"] for a in self.client.get("/dashboard/test-requests/feed.json").json()["alerts"]]
        self.assertIn(f"emergency-{em.pk}", keys)                      # siren rings for it

    def test_no_location_means_no_automatic_dispatch(self):
        ev = E.objects.get(pk=self.reading(spo2_pct=84)["event"]["id"])
        em = ev.emergency
        self.assertFalse(em.location_consent)
        self.assertIn("LOCATION NOT SHARED", em.location_description)
        self.assertIsNone(em.assigned_ambulance)

    def test_consented_location_is_used(self):
        self.api.patch("/api/assistant/preferences/", {"share_location_on_escalation": True}, format="json")
        self.assertTrue(self.api.post("/api/monitoring/location/", {"latitude": -1.95, "longitude": 30.1}).data["stored"])
        em = E.objects.get(pk=self.reading(spo2_pct=84)["event"]["id"]).emergency
        self.assertTrue(em.location_consent)
        self.assertAlmostEqual(em.latitude, -1.95)

    def test_location_not_stored_without_permission(self):
        self.assertFalse(self.api.post("/api/monitoring/location/", {"latitude": -1.9, "longitude": 30}).data["stored"])

    def test_18_duplicates_merge(self):
        self.reading(spo2_pct=84)
        self.reading(spo2_pct=83)
        self.assertEqual(E.objects.count(), 1)
        self.assertEqual(EmergencyRequest.objects.filter(patient=self.p).count(), 1)
        self.assertEqual(E.objects.get().readings.count(), 2)

    def test_existing_emergency_is_reused(self):
        from emergency import services as em_services
        em = em_services.create_emergency(self.p, {"emergency_type": "other", "latitude": -1.95, "longitude": 30.06})
        ev = E.objects.get(pk=self.reading(spo2_pct=84)["event"]["id"])
        self.assertEqual(ev.emergency, em)
        self.assertEqual(EmergencyRequest.objects.filter(patient=self.p).count(), 1)

    def test_consent_required_and_revocable(self):
        self.api.delete("/api/home-monitoring/consent/")
        r = self.api.post("/api/home-monitoring/readings/", {"spo2_pct": 84, "device": DEVICE}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("not switched on", r.data["detail"])

    def test_old_reading_does_not_start_a_conversation(self):
        old = (timezone.now() - timedelta(hours=2)).isoformat()
        self.assertIsNone(self.reading(spo2_pct=84, measured_at=old)["event"])

    def test_simulated_command_is_labelled(self):
        call_command("simulate_home_reading", "patient1", "--scenario", "hypoxia", verbosity=0)
        ev = E.objects.get()
        self.assertTrue(ev.is_simulated)
        self.assertIn("SIMULATED", ev.reason)


class ConversationTests(Base):
    def concern(self):
        self.reading(temperature_c=38.6)
        return E.objects.get(pk=self.reading(temperature_c=38.8)["event"]["id"])

    def test_12_voice_answer_ok(self):
        ev = self.concern()
        r = self.api.post(f"/api/monitoring/events/{ev.pk}/respond/",
                          {"answer": "ok", "text": "I am fine thank you", "channel": "voice"}, format="json")
        self.assertEqual(r.data["event"]["status"], "responded_ok")
        self.assertFalse(r.data["escalated"])
        self.assertEqual(r.data["event"]["log"][-1]["by"], "patient")

    def test_13_buttons_not_ok_then_help(self):
        ev = self.concern()
        r = self.api.post(f"/api/monitoring/events/{ev.pk}/respond/", {"answer": "not_ok"}, format="json")
        self.assertIn("notify medical assistance", r.data["next_prompt"])
        r = self.api.post(f"/api/monitoring/events/{ev.pk}/respond/", {"answer": "help", "symptoms": ["dizzy"], "pain": 4},
                          format="json")
        self.assertTrue(r.data["escalated"])
        self.assertIsNotNone(E.objects.get(pk=ev.pk).emergency)

    def test_emergency_words_escalate_even_if_answer_ok(self):
        ev = self.concern()
        r = self.api.post(f"/api/monitoring/events/{ev.pk}/respond/",
                          {"answer": "ok", "text": "I can't breathe", "channel": "voice"}, format="json")
        self.assertTrue(r.data["escalated"])

    def test_14_no_response_repeats_then_escalates(self):
        ev = self.concern()
        self.assertEqual(ev.prompt_count, 1)
        proactive.tick(self.later(ev, 30))
        ev.refresh_from_db()
        self.assertEqual(ev.prompt_count, 1)                       # not yet
        for n in (2, 3):
            proactive.tick(self.later(ev, 61))
            ev.refresh_from_db()
            self.assertEqual(ev.prompt_count, n)                   # repeated a limited number of times
        proactive.tick(self.later(ev, 61))
        ev.refresh_from_db()
        self.assertEqual(ev.status, "escalated")
        self.assertIn("NOT confirmed unconscious", ev.summary)
        self.assertEqual(ev.emergency.patient_condition, "unknown")
        proactive.tick(self.later(ev, 600))                         # nothing more happens twice
        self.assertEqual(EmergencyRequest.objects.filter(patient=self.p).count(), 1)

    def test_concurrent_ticks(self):
        ev = self.concern()
        t = self.later(ev, 61)
        proactive.tick(t)
        proactive.tick(t)
        ev.refresh_from_db()
        self.assertEqual(ev.prompt_count, 2)

    def test_15_possible_fall(self):
        r = self.api.post("/api/monitoring/events/", {"kind": "possible_fall"}, format="json")
        self.assertEqual(r.data["kind"], "possible_fall")
        self.api.post("/api/monitoring/events/", {"kind": "possible_fall"}, format="json")  # sensor burst
        self.assertEqual(E.objects.filter(kind="possible_fall").count(), 1)
        self.assertIn("possible fall", r.data["prompt"])

    def test_patient_report_with_severe_symptom(self):
        r = self.api.post("/api/monitoring/events/", {"kind": "patient_report", "symptoms": ["cannot_breathe"]},
                          format="json")
        self.assertEqual((r.data["severity"], r.data["status"]), ("severe", "escalated"))

    def test_contact_request_notifies_staff(self):
        self.api.post("/api/monitoring/events/", {"kind": "contact_request"}, format="json")
        self.assertEqual(self.notes(self.op, "Patient asks for contact"), 1)

    def test_other_patient_cannot_answer(self):
        ev = self.concern()
        other = APIClient()
        other.force_authenticate(self.p2)
        self.assertEqual(other.post(f"/api/monitoring/events/{ev.pk}/respond/", {"answer": "ok"}).status_code, 403)
        self.assertEqual(other.get("/api/monitoring/events/").data, [])


class CheckInTests(Base):
    def test_checkin_once_per_slot_and_missed_checkin(self):
        now = timezone.localtime()
        slot = (now - timedelta(minutes=5)).strftime("%H:%M")
        self.api.patch("/api/assistant/preferences/", {"checkins_enabled": True, "checkin_times": [slot]}, format="json")
        proactive.tick(now)
        proactive.tick(now)
        ev = E.objects.get(kind="check_in")
        for _ in range(3):
            proactive.tick(self.later(ev, 61))
            ev.refresh_from_db()
        self.assertEqual(ev.status, "closed")              # missed routine check-in: no emergency...
        self.assertFalse(EmergencyRequest.objects.filter(patient=self.p).exists())

    def test_preferences_validation(self):
        r = self.api.patch("/api/assistant/preferences/", {"checkin_times": ["25:00"]}, format="json")
        self.assertEqual(r.status_code, 400)
        r = self.api.patch("/api/assistant/preferences/", {"voice_rate": 5, "high_contrast": True}, format="json")
        self.assertEqual((r.data["voice_rate"], r.data["high_contrast"]), (1.0, True))


class CaregiverTests(Base):
    def setUp(self):
        super().setUp()
        self.cg = User.objects.create_user("carer1", password="Carer-pass-123", role="caregiver", first_name="Ama")
        self.cgapi = APIClient()
        self.cgapi.force_authenticate(self.cg)
        code = self.api.post("/api/caregivers/invite/", {"relationship": "daughter"}, format="json").data["invite_code"]
        self.link = CaregiverLink.objects.get(pk=self.cgapi.post("/api/caregivers/accept/", {"code": code}).data["id"])

    def test_invite_is_one_time(self):
        code = self.api.post("/api/caregivers/invite/", {}, format="json").data["invite_code"]
        self.cgapi.post("/api/caregivers/accept/", {"code": code})
        self.assertEqual(self.cgapi.post("/api/caregivers/accept/", {"code": code}).status_code, 400)

    def test_23_vitals_only_with_permission(self):
        self.reading(heart_rate_bpm=70)
        d = self.cgapi.get(f"/api/caregivers/patients/{self.p.pk}/").data
        self.assertNotIn("vitals", d)
        self.api.patch(f"/api/caregivers/{self.link.pk}/", {"can_view_vitals": True}, format="json")
        self.assertEqual(len(self.cgapi.get(f"/api/caregivers/patients/{self.p.pk}/").data["vitals"]), 1)
        # not linked to patient2 at all
        self.assertEqual(self.cgapi.get(f"/api/caregivers/patients/{self.p2.pk}/").status_code, 403)

    def test_21_caregiver_notified_and_can_answer(self):
        ev = E.objects.get(pk=self.reading(spo2_pct=84)["event"]["id"])
        self.assertEqual(self.notes(self.cg, "Alert for"), 1)
        self.assertEqual(len(self.cgapi.get("/api/monitoring/events/?open=1").data), 1)
        r = self.cgapi.post(f"/api/monitoring/events/{ev.pk}/respond/", {"answer": "ok", "text": "I am with her"},
                            format="json")
        self.assertEqual(r.data["event"]["log"][-1]["by"], "caregiver")   # distinguished from the patient

    def test_caregiver_requests_emergency_and_test(self):
        r = self.cgapi.post(f"/api/caregivers/patients/{self.p.pk}/emergency/",
                            {"latitude": -1.95, "longitude": 30.06, "description": "She fell"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        em = EmergencyRequest.objects.get(pk=r.data["id"])
        self.assertEqual(em.patient, self.p)
        self.assertIn("Reported by caregiver", em.description)
        r = self.cgapi.post(f"/api/caregivers/patients/{self.p.pk}/tests/", {"tests": ["heart_rate"]}, format="json")
        self.assertEqual(r.status_code, 201, r.data)

    def test_revoked_caregiver_loses_access(self):
        self.api.delete(f"/api/caregivers/{self.link.pk}/")
        self.assertEqual(self.cgapi.get(f"/api/caregivers/patients/{self.p.pk}/").status_code, 403)
        self.assertEqual(self.cgapi.post(f"/api/caregivers/patients/{self.p.pk}/emergency/",
                                         {"latitude": 0, "longitude": 0}).status_code, 403)

    def test_caregiver_report(self):
        r = self.cgapi.post(f"/api/caregivers/patients/{self.p.pk}/report/", {"symptoms": ["chest_pain"]}, format="json")
        self.assertEqual(r.data["status"], "escalated")
        self.assertEqual(E.objects.get(pk=r.data["id"]).reported_by, self.cg)


class StaffEventTests(Base):
    def test_staff_view_and_close(self):
        self.reading(spo2_pct=84)
        staff = APIClient()
        staff.force_authenticate(self.op)
        evs = staff.get("/api/staff/events/?open=1").data
        self.assertEqual(len(evs), 1)
        self.assertIn("summary", evs[0])
        r = staff.post(f"/api/staff/events/{evs[0]['id']}/close/", {"note": "called the patient"}, format="json")
        self.assertEqual(r.data["status"], "closed")
        self.assertEqual(self.api.get("/api/staff/events/").status_code, 403)  # patients cannot
