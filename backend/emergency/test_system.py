"""End-to-end tests for the web portal, dispatch safety, notifications, permissions and AI fallbacks."""
from unittest import mock

from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from ambulances.models import Ambulance, AmbulanceAssignment
from hospitals.models import Hospital
from notifications.models import Notification
from . import services
from .models import EmergencyRequest

KIGALI = (-1.9536, 30.0606)


def form_data(**kw):
    d = {"emergency_type": "cardiac", "description": "chest pain", "contact_phone": "+250781111111",
         "latitude": KIGALI[0], "longitude": KIGALI[1], "location_source": "gps", "location_consent": "on",
         "conscious": "on", "chest_pain": "on", "pain_level": 8, "age": 60}
    d.update(kw)
    return d


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_data", verbosity=0)

    def login(self, username, pw):
        self.assertTrue(self.client.login(username=username, password=pw))

    def patient_request(self, **kw):
        self.login("patient1", "patient123")
        return self.client.post(reverse("request_emergency"), form_data(**kw))


class AuthTests(Base):
    def test_patient_registration_and_login(self):
        r = self.client.post(reverse("register"), {
            "username": "newpatient", "first_name": "A", "last_name": "B", "email": "a@example.com",
            "phone": "+250780000001", "password1": "S3cure-pass-xyz", "password2": "S3cure-pass-xyz",
            "emergency_contact_name": "Mum", "emergency_contact_phone": "+250780000002"})
        self.assertRedirects(r, reverse("profile"))
        u = User.objects.get(username="newpatient")
        self.assertEqual(u.role, "patient")
        self.assertTrue(u.check_password("S3cure-pass-xyz"))  # stored hashed
        self.assertNotEqual(u.password, "S3cure-pass-xyz")
        self.assertEqual(u.patient_profile.emergency_contact_phone, "+250780000002")
        self.client.logout()
        r = self.client.post(reverse("login"), {"username": "newpatient", "password": "S3cure-pass-xyz"})
        self.assertRedirects(r, reverse("home"), target_status_code=302)

    def test_professional_needs_approval(self):
        self.client.post(reverse("register_professional"), {
            "username": "drx", "first_name": "D", "last_name": "X", "email": "d@example.com", "phone": "+250780000003",
            "hospital": Hospital.objects.first().id, "professional_id": "MD-1",
            "password1": "S3cure-pass-xyz", "password2": "S3cure-pass-xyz"})
        u = User.objects.get(username="drx")
        self.assertEqual(u.role, "hospital")
        self.assertFalse(u.is_approved)
        self.login("drx", "S3cure-pass-xyz")
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 403)
        # admin approves
        self.client.logout()
        self.login("admin", "admin12345")
        self.client.post(reverse("approvals"), {"user": u.id, "decision": "approve"})
        u.refresh_from_db()
        self.assertTrue(u.is_approved)

    def test_role_redirects(self):
        for name, pw, target in [("patient1", "patient123", "patient_home"), ("paramedic1", "paramedic123", "paramedic_home"),
                                 ("operator", "operator123", "dashboard")]:
            self.login(name, pw)
            self.assertRedirects(self.client.get(reverse("home")), reverse(target), fetch_redirect_response=False)
            self.client.logout()


class EmergencyRequestTests(Base):
    def test_submit_creates_reference_assigns_ambulance_and_notifies(self):
        r = self.patient_request()
        em = EmergencyRequest.objects.get()
        self.assertRedirects(r, reverse("track", args=[em.reference]))
        self.assertRegex(em.reference, r"^EMG-\d{8}-[0-9A-F]{6}$")
        self.assertEqual(em.location_source, "gps")
        self.assertTrue(em.location_consent)
        self.assertEqual(em.status, "assigned")
        self.assertEqual(em.assigned_ambulance.status, Ambulance.Status.DISPATCHED)
        self.assertTrue(em.assigned_hospital.specialties.filter(code="cardiology").exists())
        self.assertTrue(em.recommendations and em.recommendations[0]["reasons"])
        # notifications: patient, crew, operator, hospital and its staff
        self.assertTrue(Notification.objects.filter(emergency=em, recipient=em.patient).exists())
        self.assertTrue(Notification.objects.filter(emergency=em, recipient=em.assigned_ambulance.driver).exists())
        self.assertTrue(Notification.objects.filter(emergency=em, recipient__username="operator").exists())
        self.assertTrue(Notification.objects.filter(emergency=em, hospital=em.assigned_hospital).exists())
        alert = Notification.objects.filter(emergency=em, recipient__username="operator").first()
        for part in (em.reference, "Location", "Hospital", "Ambulance", "Status"):
            self.assertIn(part, alert.message)

    def test_request_page_renders(self):
        self.login("patient1", "patient123")
        r = self.client.get(reverse("request_emergency"))
        self.assertContains(r, "Use my location")
        self.assertContains(r, "pick it on the map")

    def test_manual_location(self):
        self.patient_request(location_source="manual", location_consent="", latitude=-1.97, longitude=30.10,
                             location_description="near the market")
        em = EmergencyRequest.objects.get()
        self.assertEqual((em.location_source, em.latitude, em.location_description), ("manual", -1.97, "near the market"))

    def test_gps_without_consent_rejected(self):
        r = self.patient_request(location_consent="")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(EmergencyRequest.objects.count(), 0)

    def test_missing_location_rejected(self):
        r = self.patient_request(latitude="", longitude="")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(EmergencyRequest.objects.count(), 0)

    def test_duplicate_submission_prevented(self):
        self.patient_request()
        r = self.client.post(reverse("request_emergency"), form_data())
        self.assertEqual(EmergencyRequest.objects.count(), 1)
        self.assertRedirects(r, reverse("track", args=[EmergencyRequest.objects.get().reference]))

    def test_duplicate_via_api_returns_409(self):
        c = APIClient()
        c.force_authenticate(User.objects.get(username="patient1"))
        body = {"emergency_type": "trauma", "latitude": -1.95, "longitude": 30.06}
        self.assertEqual(c.post("/api/emergencies/", body, format="json").status_code, 201)
        self.assertEqual(c.post("/api/emergencies/", body, format="json").status_code, 409)

    def test_patient_can_cancel_and_ambulance_is_freed(self):
        self.patient_request()
        em = EmergencyRequest.objects.get()
        amb = em.assigned_ambulance
        self.client.post(reverse("cancel_emergency", args=[em.reference]))
        em.refresh_from_db(); amb.refresh_from_db()
        self.assertEqual(em.status, "cancelled")
        self.assertEqual(amb.status, "available")


class HospitalTests(Base):
    def test_nearby_json_sorted_with_distance_and_recommendation(self):
        self.login("patient1", "patient123")
        d = self.client.get(reverse("hospitals_json"), {"lat": KIGALI[0], "lng": KIGALI[1], "type": "burns"}).json()
        dists = [h["distance_km"] for h in d["nearby"]]
        self.assertEqual(dists, sorted(dists))
        self.assertTrue(d["ai_available"])
        top = Hospital.objects.get(pk=d["recommended"][0]["hospital_id"])
        self.assertTrue(top.specialties.filter(code="burns").exists())
        self.assertIn("simulated / unverified", " ".join(d["recommended"][0]["reasons"]))

    def test_bad_coordinates(self):
        self.login("patient1", "patient123")
        self.assertEqual(self.client.get(reverse("hospitals_json"), {"lat": "x", "lng": 1}).status_code, 400)
        self.assertEqual(self.client.get(reverse("hospitals_json"), {"lat": 99, "lng": 1}).status_code, 400)

    def test_recommendation_is_not_just_nearest(self):
        """Patient next to a maternity-only district hospital with a cardiac emergency:
        the recommender should prefer a hospital with cardiology."""
        muhima = Hospital.objects.get(name__startswith="Muhima")
        from ai_recommendation.recommender import recommend_hospitals
        recs = recommend_hospitals(muhima.latitude, muhima.longitude, "cardiac", "critical")
        self.assertNotEqual(recs[0]["hospital_id"], muhima.id)


class AmbulanceTests(Base):
    def _em(self, user="patient1", **kw):
        u = User.objects.get(username=user)
        return services.create_emergency(u, {"emergency_type": "trauma", "latitude": -1.95, "longitude": 30.06, **kw},
                                         allow_duplicate=True)

    def test_same_ambulance_never_assigned_twice(self):
        Ambulance.objects.exclude(pk=Ambulance.objects.first().pk).update(status="offline")
        a, b = self._em(), self._em()
        self.assertEqual(a.status, "assigned")
        self.assertEqual(b.status, "pending")
        self.assertIsNone(b.assigned_ambulance)

    def test_race_lost_ambulance_is_skipped(self):
        """If another request grabs the best ambulance between ranking and reservation, the next one is used."""
        from ai_recommendation import recommender
        real = recommender.rank_ambulances

        def stale_ranking(*a, **kw):
            ranked = real(*a, **kw)
            Ambulance.objects.filter(pk=ranked[0][0].pk).update(status="assigned")  # stolen meanwhile
            return ranked
        with mock.patch.object(services, "rank_ambulances", stale_ranking):
            em = self._em()
        self.assertEqual(em.status, "assigned")
        self.assertEqual(AmbulanceAssignment.objects.filter(released_at__isnull=True).count(), 1)

    def test_database_constraint_blocks_double_assignment(self):
        em1, em2 = self._em(), self._em("operator")
        amb = em1.assigned_ambulance
        with self.assertRaises(IntegrityError), transaction.atomic():
            AmbulanceAssignment.objects.create(ambulance=amb, emergency=em2, start_latitude=0, start_longitude=0)

    def test_status_flow_updates_ambulance(self):
        em = self._em(severe_bleeding=True)
        amb = em.assigned_ambulance
        for s, expected in [("en_route", "on_the_way"), ("on_scene", "arrived"), ("transporting", "busy"),
                            ("arrived", "available"), ("completed", "available")]:
            services.update_status(em, s)
            amb.refresh_from_db()
            self.assertEqual(amb.status, expected, s)
        self.assertEqual(em.assignments.get().outcome, "completed")

    def test_invalid_transition(self):
        em = self._em()
        with self.assertRaises(ValueError):
            services.update_status(em, "completed")

    def test_redispatch_frees_previous_ambulance(self):
        em = self._em()
        first = em.assigned_ambulance
        services.dispatch(em)
        first.refresh_from_db()
        self.assertEqual(first.status, "available") if em.assigned_ambulance_id != first.id else None
        self.assertEqual(em.assignments.filter(released_at__isnull=True).count(), 1)
        self.assertEqual(em.assignments.filter(outcome="released").count(), 1)

    def test_simulation_moves_only_simulated_ambulances(self):
        em = self._em()
        amb = em.assigned_ambulance
        before = (amb.latitude, amb.longitude)
        self.assertIsNotNone(services.simulate_step(em))
        amb.refresh_from_db()
        self.assertNotEqual(before, (amb.latitude, amb.longitude))
        amb.location_is_simulated = False
        amb.save()
        em.refresh_from_db()
        self.assertIsNone(services.simulate_step(em))

    def test_operator_cannot_take_busy_ambulance_out_of_service(self):
        em = self._em()
        self.login("operator", "operator123")
        self.client.post(reverse("ambulance_action", args=[em.assigned_ambulance_id]), {"status": "offline"})
        em.assigned_ambulance.refresh_from_db()
        self.assertNotEqual(em.assigned_ambulance.status, "offline")


class NotificationTests(Base):
    def test_sms_simulated_is_not_marked_delivered(self):
        services.create_emergency(User.objects.get(username="patient1"),
                                  {"emergency_type": "other", "latitude": -1.95, "longitude": 30.06})
        smss = Notification.objects.filter(channel="sms")
        self.assertTrue(smss.exists())
        self.assertFalse(smss.filter(delivered=True).exists())
        self.assertEqual(set(smss.values_list("delivery_status", flat=True)), {"simulated"})

    @override_settings(SMS_PROVIDER="twilio", TWILIO_ACCOUNT_SID="", TWILIO_AUTH_TOKEN="", TWILIO_FROM_NUMBER="")
    def test_missing_credentials_reported(self):
        from notifications.services import send_sms
        self.assertEqual(send_sms("+250780000000", "hi")[0], "not_configured")

    @override_settings(SMS_PROVIDER="africastalking", AT_USERNAME="sandbox", AT_API_KEY="k")
    def test_provider_network_error_is_failed_not_crash(self):
        import requests
        from notifications.services import send_sms
        with mock.patch("notifications.services.requests.post", side_effect=requests.ConnectionError("down")):
            status, provider, err = send_sms("+250780000000", "hi")
        self.assertEqual((status, provider), ("failed", "africastalking"))

    def test_notification_page_and_mark_read(self):
        services.create_emergency(User.objects.get(username="patient1"),
                                  {"emergency_type": "other", "latitude": -1.95, "longitude": 30.06})
        self.login("patient1", "patient123")
        self.assertContains(self.client.get(reverse("notifications")), "Ambulance assigned")
        self.client.post(reverse("notifications_read"))
        self.assertFalse(Notification.objects.filter(recipient__username="patient1", is_read=False).exists())


class PermissionTests(Base):
    def setUp(self):
        self.em = services.create_emergency(User.objects.get(username="patient1"),
                                            {"emergency_type": "cardiac", "latitude": KIGALI[0], "longitude": KIGALI[1],
                                             "chest_pain": True, "conscious": False})

    def test_patient_cannot_open_staff_pages(self):
        self.login("patient1", "patient123")
        for name in ("dashboard", "emergencies", "ambulances", "approvals"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)

    def test_other_patient_cannot_track(self):
        User.objects.create_user("p2", password="x-Secure-123", role="patient")
        self.login("p2", "x-Secure-123")
        self.assertEqual(self.client.get(reverse("track", args=[self.em.reference])).status_code, 403)
        self.assertEqual(self.client.get(reverse("track_json", args=[self.em.reference])).status_code, 403)

    def test_anonymous_redirected_to_login(self):
        r = self.client.get(reverse("request_emergency"))
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("login"), r.url)

    def test_hospital_staff_sees_only_own_hospital(self):
        self.login("chuk_staff", "hospital123")
        own = self.em.assigned_hospital.name.startswith("CHUK")
        code = self.client.get(reverse("emergency_detail", args=[self.em.id])).status_code
        self.assertEqual(code, 200 if own else 404)

    def test_hospital_staff_cannot_dispatch(self):
        self.em.assigned_hospital = Hospital.objects.get(name__startswith="CHUK")
        self.em.save()
        self.login("chuk_staff", "hospital123")
        self.client.post(reverse("emergency_action", args=[self.em.id]), {"action": "priority", "priority": "low"})
        self.em.refresh_from_db()
        self.assertNotEqual(self.em.priority, "low")

    def test_unapproved_professional_blocked_from_api(self):
        c = APIClient()
        c.force_authenticate(User.objects.get(username="nurse_pending"))
        self.assertEqual(len(c.get("/api/emergencies/").data), 0)

    def test_paramedic_only_sees_own_job(self):
        driver = self.em.assigned_ambulance.driver
        other = User.objects.filter(role="paramedic").exclude(pk=driver.pk).first()
        c = APIClient()
        c.force_authenticate(other)
        self.assertEqual(c.get(f"/api/emergencies/{self.em.id}/").status_code, 404)

    def test_pages_render_for_each_role(self):
        # patient1 already has an active emergency -> the request page redirects to tracking
        self.login("patient1", "patient123")
        self.assertRedirects(self.client.get(reverse("request_emergency")), reverse("track", args=[self.em.reference]))
        self.client.logout()
        pages = {"patient1": ("patient123", ["patient_home", "hospitals", "notifications", "profile", "assistant"]),
                 "operator": ("operator123", ["dashboard", "emergencies", "ambulances", "approvals", "reports", "notifications"]),
                 "chuk_staff": ("hospital123", ["dashboard", "emergencies", "hospital_capacity", "reports"]),
                 "paramedic1": ("paramedic123", ["paramedic_home", "notifications"])}
        for user, (pw, names) in pages.items():
            self.login(user, pw)
            for n in names:
                self.assertEqual(self.client.get(reverse(n)).status_code, 200, f"{user} {n}")
            self.client.logout()
        self.assertEqual(self.client.get(reverse("landing")).status_code, 200)
        self.login("operator", "operator123")
        self.assertEqual(self.client.get(reverse("emergency_detail", args=[self.em.id])).status_code, 200)
        self.assertEqual(self.client.get(reverse("emergencies"), {"q": self.em.reference, "status": "assigned"}).status_code, 200)


class AIFailureTests(Base):
    def test_emergency_still_created_when_model_crashes(self):
        with mock.patch("ai_recommendation.priority.predict_priority", side_effect=RuntimeError("model gone")):
            em = services.create_emergency(User.objects.get(username="patient1"),
                                           {"emergency_type": "cardiac", "latitude": KIGALI[0], "longitude": KIGALI[1],
                                            "conscious": False, "chest_pain": True, "breathing_difficulty": True})
        self.assertEqual(em.triage_method, "rule_fallback")
        self.assertIn(em.priority, ("high", "critical"))
        self.assertEqual(em.status, "assigned")

    def test_emergency_still_created_when_recommender_crashes(self):
        with mock.patch.object(services, "recommend_hospitals", side_effect=RuntimeError("db")):
            em = services.create_emergency(User.objects.get(username="patient1"),
                                           {"emergency_type": "trauma", "latitude": -1.95, "longitude": 30.06})
        self.assertEqual(em.recommendations, [])
        self.assertIsNotNone(em.pk)

    def test_dispatch_crash_alerts_operators(self):
        with mock.patch.object(services, "dispatch", side_effect=RuntimeError("boom")):
            em = services.create_emergency(User.objects.get(username="patient1"),
                                           {"emergency_type": "trauma", "latitude": -1.95, "longitude": 30.06})
        self.assertTrue(Notification.objects.filter(emergency=em, title__startswith="MANUAL DISPATCH").exists())
