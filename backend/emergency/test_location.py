"""Live patient location: validation, permissions, throttling, live / last known / manual / unavailable."""
import json
from datetime import timedelta

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from . import location, services
from .models import EmergencyLocation, EmergencyRequest

KIGALI = (-1.9500, 30.0588)


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_data", verbosity=0)

    def setUp(self):
        self.p1 = User.objects.get(username="patient1")
        self.p2 = User.objects.get(username="patient2")
        self.op = User.objects.get(username="operator")

    def em(self, patient=None, **extra):
        data = {"emergency_type": "other", "latitude": KIGALI[0], "longitude": KIGALI[1], "location_accuracy_m": 30,
                "location_source": "gps", "location_consent": True, **extra}
        return services.create_emergency(patient or self.p1, data)

    def post_web(self, user, em, **body):
        self.client.force_login(user)
        return self.client.post(reverse("update_location", args=[em.reference]), json.dumps(body),
                                content_type="application/json")


class ValidationTests(Base):
    def test_rejects_invalid_coordinates(self):
        em = self.em()
        for bad in ({"latitude": 91, "longitude": 30}, {"latitude": -1.9, "longitude": 181},
                    {"latitude": "abc", "longitude": 30}, {"latitude": 0, "longitude": 0},
                    {"latitude": -1.9, "longitude": 30, "accuracy": -5},
                    {"latitude": -1.9, "longitude": 30, "accuracy": 999999},
                    {"latitude": -1.9, "longitude": 30, "timestamp": (timezone.now() + timedelta(hours=1)).isoformat()},
                    {"latitude": -1.9, "longitude": 30, "timestamp": (timezone.now() - timedelta(hours=1)).isoformat()},
                    {"latitude": "NaN", "longitude": 30}):
            with self.subTest(bad=bad):
                self.assertEqual(self.post_web(self.p1, em, **bad).status_code, 400)
        em.refresh_from_db()
        self.assertEqual((em.latitude, em.longitude), KIGALI)       # nothing was changed


class PermissionTests(Base):
    def test_only_the_patient_updates_their_own(self):
        em = self.em()
        self.assertEqual(self.post_web(self.p2, em, latitude=-1.95, longitude=30.07).status_code, 404)
        self.assertEqual(self.post_web(self.op, em, latitude=-1.95, longitude=30.07).status_code, 404)
        api = APIClient()
        api.force_authenticate(self.p2)
        self.assertEqual(api.post(f"/api/emergencies/{em.pk}/location/", {"latitude": -1.95, "longitude": 30.07}).status_code, 404)
        self.client.logout()
        r = self.client.post(reverse("update_location", args=[em.reference]), "{}", content_type="application/json")
        self.assertIn(r.status_code, (302, 403))                       # anonymous -> login

    def test_location_feeds_are_protected(self):
        self.em()
        self.client.force_login(self.p1)
        self.assertIn(self.client.get(reverse("live_locations")).status_code, (302, 403))
        self.client.force_login(self.p2)
        em = EmergencyRequest.objects.get(patient=self.p1)
        self.assertEqual(self.client.get(reverse("track_json", args=[em.reference])).status_code, 403)

    def test_closed_emergency_stops_sharing(self):
        em = self.em()
        services.update_status(em, "cancelled", self.p1)
        r = self.post_web(self.p1, em, latitude=-1.951, longitude=30.06)
        self.assertEqual(r.status_code, 400)
        self.assertIn("closed", r.json()["error"])


class LiveUpdateTests(Base):
    def test_moving_patient_updates_marker_and_history(self):
        em = self.em()
        self.assertEqual(em.location_history.count(), 1)                # position at submission
        r = self.post_web(self.p1, em, latitude=-1.9530, longitude=30.0620, accuracy=12)  # ~480 m away
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["stored"])
        self.assertEqual(r.json()["location"]["state"], "live")
        em.refresh_from_db()
        self.assertEqual((round(em.latitude, 4), round(em.longitude, 4), em.location_accuracy_m), (-1.953, 30.062, 12))
        # operator map sees the new position for the same emergency (one marker per emergency)
        self.client.force_login(self.op)
        feed = self.client.get(reverse("live_locations")).json()["emergencies"]
        mine = [e for e in feed if e["id"] == em.pk]
        self.assertEqual(len(mine), 1)
        self.assertEqual((mine[0]["location"]["latitude"], mine[0]["location"]["state"]), (-1.953, "live"))

    def test_tiny_moves_do_not_flood_history(self):
        em = self.em()
        r = self.post_web(self.p1, em, latitude=KIGALI[0] + 0.00001, longitude=KIGALI[1])  # ~1 m, seconds later
        self.assertFalse(r.json()["stored"])
        self.assertEqual(em.location_history.count(), 1)

    def test_mobile_api_same_workflow(self):
        em = self.em()
        api = APIClient()
        api.force_authenticate(self.p1)
        r = api.post(f"/api/emergencies/{em.pk}/location/", {"latitude": -1.96, "longitude": 30.07, "accuracy": 8},
                     format="json")
        self.assertEqual(r.status_code, 200, r.data)
        d = api.get(f"/api/emergencies/{em.pk}/").data
        self.assertEqual((d["latitude"], d["location_status"]["state"]), (-1.96, "live"))

    def test_multiple_patients_keep_their_own_markers(self):
        a = self.em(self.p1)
        b = self.em(self.p2, latitude=-1.99, longitude=30.18)
        self.post_web(self.p1, a, latitude=-1.951, longitude=30.059)
        self.client.force_login(self.op)
        feed = {e["id"]: e for e in self.client.get(reverse("live_locations")).json()["emergencies"]}
        self.assertEqual(feed[a.pk]["location"]["latitude"], -1.951)
        self.assertEqual(feed[b.pk]["location"]["latitude"], -1.99)
        self.assertEqual(feed[b.pk]["patient_code"], f"P-{self.p2.pk:05d}")


class StatusTests(Base):
    @override_settings(LOCATION_LIVE_SECONDS=120)
    def test_old_gps_is_last_known_with_warning(self):
        em = self.em()
        EmergencyRequest.objects.filter(pk=em.pk).update(location_updated_at=timezone.now() - timedelta(minutes=10))
        em.refresh_from_db()
        s = location.status(em)
        self.assertEqual((s["state"], s["label"], s["stale"]), ("last_known", "LAST KNOWN LOCATION", True))
        self.assertTrue(s["note"])

    def test_manual_is_labelled_manual(self):
        em = self.em(location_source="manual")
        self.assertEqual(location.status(em)["label"], "MANUAL LOCATION")

    def test_unavailable_never_shows_coordinates(self):
        em = self.em(location_source="none", location_consent=False,
                     location_description="LOCATION NOT SHARED - call the patient / caregiver to confirm")
        s = location.status(em)
        self.assertEqual((s["state"], s["latitude"], s["longitude"]), ("unavailable", None, None))
        self.client.force_login(self.op)
        d = self.client.get(reverse("track_json", args=[em.reference])).json()
        self.assertIsNone(d["patient"])
        self.assertEqual(d["patient_location"]["label"], "LOCATION UNAVAILABLE")
        api = APIClient()
        api.force_authenticate(self.op)
        self.assertIsNone(api.get(f"/api/emergencies/{em.pk}/").data["latitude"])
        self.assertEqual(em.location_history.count(), 0)                # no invented history either

    def test_automatic_alert_without_location_is_unavailable(self):
        from patient_monitoring import proactive
        ev = proactive.report(self.p1, "patient_report", ["cannot_breathe"], "", by=self.p1)  # severe -> escalates
        ev.refresh_from_db()
        self.assertEqual(ev.emergency.location_source, "none")
        self.assertEqual(location.status(ev.emergency)["state"], "unavailable")

    def test_dashboard_and_detail_render(self):
        em = self.em()
        self.client.force_login(self.op)
        self.assertContains(self.client.get(reverse("dashboard")), "live_locations".replace("_", "-"), status_code=200)
        self.assertContains(self.client.get(reverse("emergency_detail", args=[em.pk])), "op-loc-badge")
        self.client.force_login(self.p1)
        self.assertContains(self.client.get(reverse("track", args=[em.reference])), "Share my live location")
