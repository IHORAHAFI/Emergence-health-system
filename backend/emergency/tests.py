"""Objective 4 tests: location accuracy, hospital identification, ambulance assignment, notification."""
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from ai_recommendation.geo import haversine_km
from ai_recommendation.priority import predict_priority
from ambulances.models import Ambulance
from notifications.models import Notification
from .models import EmergencyRequest

KIGALI_CENTER = (-1.9536, 30.0606)


class GeoTests(TestCase):
    def test_haversine_known_distance(self):
        # Kigali -> Huye is ~ 82 km straight line
        d = haversine_km(-1.9441, 30.0619, -2.5967, 29.7394)
        self.assertAlmostEqual(d, 80.6, delta=3)

    def test_zero_distance(self):
        self.assertEqual(haversine_km(1, 1, 1, 1), 0)


class PriorityTests(TestCase):
    def test_unconscious_cardiac_is_critical_or_high(self):
        p, _ = predict_priority({"emergency_type": "cardiac", "age": 70, "conscious": False,
                                 "breathing_difficulty": True, "chest_pain": True, "pain_level": 9})
        self.assertIn(p, ("critical", "high"))

    def test_minor_is_low_or_medium(self):
        p, _ = predict_priority({"emergency_type": "other", "age": 30, "conscious": True, "pain_level": 2})
        self.assertIn(p, ("low", "medium"))


class WorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_data", verbosity=0)

    def setUp(self):
        self.client = APIClient()
        r = self.client.post("/api/auth/login/", {"username": "patient1", "password": "patient123"})
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}")

    def test_nearby_hospitals_sorted(self):
        r = self.client.get("/api/hospitals/nearby/", {"lat": KIGALI_CENTER[0], "lng": KIGALI_CENTER[1]})
        self.assertEqual(r.status_code, 200)
        dists = [h["distance_km"] for h in r.data]
        self.assertTrue(len(dists) > 0)
        self.assertEqual(dists, sorted(dists))

    def test_full_emergency_flow(self):
        r = self.client.post("/api/emergencies/", {
            "emergency_type": "cardiac", "latitude": KIGALI_CENTER[0], "longitude": KIGALI_CENTER[1],
            "conscious": False, "chest_pain": True, "pain_level": 8, "age": 60}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        em = EmergencyRequest.objects.get(pk=r.data["id"])
        self.assertEqual(em.status, "assigned")
        self.assertIsNotNone(em.assigned_ambulance)
        self.assertIsNotNone(em.assigned_hospital)
        self.assertEqual(em.assigned_ambulance.status, Ambulance.Status.DISPATCHED)
        # cardiac case should go to a hospital with cardiology
        self.assertTrue(em.assigned_hospital.specialties.filter(code="cardiology").exists())
        self.assertTrue(Notification.objects.filter(hospital=em.assigned_hospital, emergency=em).exists())
        self.assertTrue(Notification.objects.filter(recipient=em.patient, emergency=em).exists())

    def test_no_ambulance_stays_pending(self):
        Ambulance.objects.update(status="offline")
        r = self.client.post("/api/emergencies/", {"emergency_type": "trauma", "latitude": -1.95,
                                                   "longitude": 30.06}, format="json")
        self.assertEqual(r.data["status"], "pending")

    def test_paramedic_status_flow_frees_ambulance(self):
        r = self.client.post("/api/emergencies/", {"emergency_type": "trauma", "latitude": -1.95,
                                                   "longitude": 30.06, "severe_bleeding": True}, format="json")
        em = EmergencyRequest.objects.get(pk=r.data["id"])
        para = APIClient()
        para.force_authenticate(em.assigned_ambulance.driver)
        for s in ["en_route", "on_scene", "transporting", "arrived", "completed"]:
            resp = para.post(f"/api/emergencies/{em.id}/status/", {"status": s}, format="json")
            self.assertEqual(resp.status_code, 200, resp.data)
        em.assigned_ambulance.refresh_from_db()
        self.assertEqual(em.assigned_ambulance.status, "available")

    def test_patient_cannot_see_others(self):
        other = User.objects.create_user("other", password="x12345678", role="patient")
        c = APIClient()
        c.force_authenticate(other)
        self.client.post("/api/emergencies/", {"emergency_type": "other", "latitude": -1.95,
                                               "longitude": 30.06}, format="json")
        self.assertEqual(len(c.get("/api/emergencies/").data), 0)
