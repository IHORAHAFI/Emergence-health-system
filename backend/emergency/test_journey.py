"""End-to-end tests for the SIMULATED ambulance journey (hospital -> patient -> pickup -> hospital).

The routing service is replaced by a fake that returns a realistic road-like polyline (several turns, never the
straight line), with a travel time from a 40 km/h average. Time is moved forward with tick(now=...), so a
journey that would take many minutes runs in milliseconds."""
from datetime import timedelta
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from ai_recommendation.geo import haversine_km
from ambulances.models import Ambulance
from notifications.models import Notification
from . import journey, services
from .models import AmbulanceJourney, EmergencyRequest

J = AmbulanceJourney.Status
PATIENT_A = (-1.9530, 30.0910)
PATIENT_B = (-1.9700, 30.1040)


def road_route(start, end):
    """Road-like route: goes along one axis, then the other, with a detour - like streets, not a straight line."""
    (a, b), (c, d) = start, end
    mid = (a + (c - a) * 0.5, b)
    pts = [list(start), [a + (c - a) * 0.25, b + 0.002], list(mid), [mid[0], b + (d - b) * 0.6], [c, b + (d - b) * 0.6],
           list(end)]
    km = sum(haversine_km(*p, *q) for p, q in zip(pts, pts[1:]))
    return {"coords": pts, "distance_m": km * 1000, "duration_s": km / 40 * 3600, "source": "test router"}


def down(*a):
    raise journey.RoutingError("Routing service unavailable (URLError)")


@override_settings(JOURNEY_SIM_SPEEDUP=10, JOURNEY_PICKUP_SECONDS=10, JOURNEY_ARRIVAL_HOLD_SECONDS=5,
                   JOURNEY_AUTO_START=True)
class JourneyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_data", verbosity=0)

    def setUp(self):
        self.p1 = User.objects.get(username="patient1")
        self.p2 = User.objects.get(username="patient2")
        self.op = User.objects.get(username="operator")
        Ambulance.objects.update(location_is_simulated=True)
        self.route = mock.patch.object(journey, "fetch_route", side_effect=road_route)
        self.route.start()
        self.addCleanup(self.route.stop)

    def request(self, patient, pos=PATIENT_A, **kw):
        data = {"emergency_type": "trauma", "latitude": pos[0], "longitude": pos[1], "location_source": "gps",
                "location_consent": True, **kw}
        with self.captureOnCommitCallbacks(execute=True):
            em = services.create_emergency(patient, data, allow_duplicate=True)
        em.refresh_from_db()
        return em

    def j(self, em):
        return AmbulanceJourney.objects.get(emergency=em)

    def run_leg(self, j, t, steps=10):
        """Tick through the current leg; returns (time at end, positions seen)."""
        j.refresh_from_db()
        dur = journey.sim_duration(journey.leg_route(j), j.sim_speedup)
        seen = []
        for i in range(1, steps + 1):
            journey.tick(t + timedelta(seconds=dur * i / steps))
            j.refresh_from_db()
            seen.append((j.current_latitude, j.current_longitude, j.status))
        return t + timedelta(seconds=dur), seen

    # ------------------------------------------------------------------ complete journey
    def test_full_journey_hospital_patient_pickup_hospital(self):
        em = self.request(self.p1)
        self.assertEqual(em.status, "assigned")
        j = self.j(em)
        amb = em.assigned_ambulance
        # 3. ambulance appears at ITS hospital; 4. patient at the request's GPS position
        self.assertEqual((j.start_latitude, j.start_longitude), (amb.base_hospital.latitude, amb.base_hospital.longitude))
        amb.refresh_from_db()
        self.assertEqual((amb.latitude, amb.longitude), (j.start_latitude, j.start_longitude))
        self.assertEqual((j.pickup_latitude, j.pickup_longitude), PATIENT_A)
        self.assertEqual(j.status, J.ROUTING)

        t = timezone.now()
        journey.tick(t)  # 5. starts automatically
        j.refresh_from_db()
        self.assertEqual(j.status, J.EN_ROUTE_TO_PATIENT)
        em.refresh_from_db()
        self.assertEqual(em.status, "en_route")
        self.assertEqual(Ambulance.objects.get(pk=amb.pk).status, "on_the_way")

        # 6/7. moves along the road route (each position lies on the route polyline), smoothly forward
        route = j.route_to_patient
        t, seen = self.run_leg(j, t)
        moving = [s for s in seen if s[2] == J.EN_ROUTE_TO_PATIENT]
        self.assertGreaterEqual(len(moving), 5)
        for lat, lng, _ in moving:
            self.assertTrue(self.on_route(route, lat, lng), (lat, lng))
        self.assertNotIn((PATIENT_A[0], PATIENT_A[1]), [(s[0], s[1]) for s in moving])  # no jump to the patient

        # 8/9. arrives at the patient, notification sent, recorded
        j.refresh_from_db()
        self.assertEqual(j.status, J.ARRIVED_AT_PATIENT)
        self.assertIsNotNone(j.arrived_patient_at)
        self.assertAlmostEqual(j.current_latitude, PATIENT_A[0], places=6)
        self.assertTrue(Notification.objects.filter(recipient=self.p1, title__startswith="Ambulance arrived").exists())
        em.refresh_from_db()
        self.assertEqual(em.status, "on_scene")

        # 10. pickup phase is not skipped
        journey.tick(t + timedelta(seconds=6))
        j.refresh_from_db()
        self.assertEqual(j.status, J.PICKING_UP_PATIENT)
        journey.tick(t + timedelta(seconds=10))
        j.refresh_from_db()
        self.assertEqual(j.status, J.PICKING_UP_PATIENT)
        t = t + timedelta(seconds=17)
        journey.tick(t)
        j.refresh_from_db()
        # 11. return journey starts on a road route
        self.assertEqual(j.status, J.EN_ROUTE_TO_HOSPITAL)
        self.assertIsNotNone(j.pickup_completed_at)
        em.refresh_from_db()
        self.assertEqual(em.status, "transporting")
        dest = em.assigned_hospital or amb.base_hospital
        self.assertEqual((j.destination_latitude, j.destination_longitude), (dest.latitude, dest.longitude))

        # 12. follows the return road route; 13. reaches the hospital
        back = j.route_to_hospital
        t, seen = self.run_leg(j, t)
        for lat, lng, st in seen:
            if st == J.EN_ROUTE_TO_HOSPITAL:
                self.assertTrue(self.on_route(back, lat, lng))
        j.refresh_from_db()
        self.assertEqual(j.status, J.ARRIVED_AT_HOSPITAL)
        self.assertFalse(j.is_open)
        self.assertAlmostEqual(j.current_latitude, dest.latitude, places=6)
        # 14. completed + recorded; 15. ambulance available again via the existing workflow
        em.refresh_from_db()
        self.assertEqual(em.status, "arrived")
        self.assertEqual(Ambulance.objects.get(pk=amb.pk).status, "available")
        self.assertTrue(Notification.objects.filter(recipient=self.op, title__startswith="Arrived at hospital").exists())
        for f in ("started_at", "arrived_patient_at", "pickup_completed_at", "arrived_hospital_at"):
            self.assertIsNotNone(getattr(j, f), f)
        # updates stop after completion
        v = j.version
        journey.tick(t + timedelta(minutes=10))
        j.refresh_from_db()
        self.assertEqual(j.version, v)

    def on_route(self, route, lat, lng, tol_m=2.0):
        r = journey.prepare(dict(route))
        best = min(self.dist_to_segment(lat, lng, a, b) for a, b in zip(r["coords"], r["coords"][1:]))
        return best <= tol_m

    @staticmethod
    def dist_to_segment(lat, lng, a, b):
        ax, ay, bx, by = a[1], a[0], b[1], b[0]
        dx, dy = bx - ax, by - ay
        t = 0 if dx == dy == 0 else max(0, min(1, ((lng - ax) * dx + (lat - ay) * dy) / (dx * dx + dy * dy)))
        return haversine_km(lat, lng, ay + dy * t, ax + dx * t) * 1000

    # ------------------------------------------------------------------ concurrency / multiple journeys
    def test_two_ambulances_simultaneously(self):
        e1, e2 = self.request(self.p1, PATIENT_A), self.request(self.p2, PATIENT_B)
        j1, j2 = self.j(e1), self.j(e2)
        self.assertNotEqual(j1.ambulance_id, j2.ambulance_id)
        t = timezone.now()
        journey.tick(t)
        journey.tick(t + timedelta(seconds=20))
        j1.refresh_from_db(), j2.refresh_from_db()
        a1, a2 = Ambulance.objects.get(pk=j1.ambulance_id), Ambulance.objects.get(pk=j2.ambulance_id)
        self.assertEqual((a1.latitude, a1.longitude), (j1.current_latitude, j1.current_longitude))
        self.assertEqual((a2.latitude, a2.longitude), (j2.current_latitude, j2.current_longitude))
        self.assertNotEqual((a1.latitude, a1.longitude), (a2.latitude, a2.longitude))

    def test_duplicate_start_rejected(self):
        em = self.request(self.p1)
        with self.assertRaises(journey.JourneyError):
            journey.create_for(em)
        api = APIClient()
        api.force_authenticate(self.op)
        r = api.post("/api/journeys/start/", {"emergency": em.reference}, format="json")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(AmbulanceJourney.objects.filter(emergency=em).count(), 1)

    def test_concurrent_advance_single_transition(self):
        em = self.request(self.p1)
        t = timezone.now()
        journey.tick(t)
        j = self.j(em)
        end = t + timedelta(seconds=journey.sim_duration(j.route_to_patient, j.sim_speedup) + 1)
        stale = self.j(em)
        journey.advance(j, end)
        journey.advance(stale, end)  # a second caller with an old copy must not repeat the arrival
        self.assertEqual(Notification.objects.filter(recipient=self.p1, title__startswith="Ambulance arrived").count(), 1)

    def test_refresh_recovers_same_position(self):
        """A reconnecting browser gets the same server state (position computed from route + time)."""
        em = self.request(self.p1)
        t = timezone.now()
        journey.tick(t)
        j = self.j(em)
        mid = t + timedelta(seconds=journey.sim_duration(j.route_to_patient, j.sim_speedup) / 2)
        s1, s2 = journey.state(self.j(em), mid), journey.state(self.j(em), mid)
        self.assertEqual(s1["position"], s2["position"])
        self.assertAlmostEqual(s1["progress"], 0.5, places=3)
        self.assertLess(s1["leg"]["remaining_m"], s1["leg"]["distance_m"])

    # ------------------------------------------------------------------ error handling
    def test_missing_patient_gps_waits_for_operator(self):
        em = self.request(self.p1, location_source="none")
        j = self.j(em)
        self.assertEqual(j.status, J.WAITING_LOCATION)
        journey.tick()
        j.refresh_from_db()
        self.assertEqual(j.status, J.WAITING_LOCATION)  # never invents a position
        self.assertTrue(Notification.objects.filter(recipient=self.op, title__startswith="LOCATION NEEDED").exists())
        api = APIClient()
        api.force_authenticate(self.op)
        r = api.post(f"/api/journeys/{j.pk}/pickup-location/", {"latitude": PATIENT_A[0], "longitude": PATIENT_A[1]},
                     format="json")
        self.assertEqual(r.status_code, 200, r.content)
        j.refresh_from_db()
        self.assertEqual(j.status, J.EN_ROUTE_TO_PATIENT)
        self.assertEqual((j.pickup_latitude, j.pickup_longitude), PATIENT_A)

    def test_missing_hospital_and_bad_coordinates(self):
        em = self.request(self.p1)
        services.update_status(em, "cancelled")
        amb = Ambulance.objects.get(pk=em.assigned_ambulance_id)
        amb.base_hospital = None
        amb.save()
        Ambulance.objects.exclude(pk=amb.pk).update(status="offline")
        em2 = self.request(self.p2)
        j = self.j(em2)
        self.assertEqual(j.status, J.FAILED)
        self.assertIn("no base hospital", j.error)

    def test_routing_failure_keeps_state_and_retry_works(self):
        em = self.request(self.p1)
        j = self.j(em)
        with mock.patch.object(journey, "fetch_route", side_effect=down):
            journey.tick()
        j.refresh_from_db()
        self.assertEqual(j.status, J.FAILED)
        self.assertIn("unavailable", j.error)
        self.assertEqual(j.route_to_patient, {})  # no fake route
        self.assertEqual((j.current_latitude, j.current_longitude), (j.start_latitude, j.start_longitude))
        em.refresh_from_db()
        self.assertEqual(em.status, "assigned")  # never pretends the ambulance left/arrived
        journey.act(j, "retry", self.op)
        j.refresh_from_db()
        self.assertEqual(j.status, J.EN_ROUTE_TO_PATIENT)

    def test_cancel_emergency_stops_journey(self):
        em = self.request(self.p1)
        journey.tick()
        services.update_status(em, "cancelled")
        j = self.j(em)
        self.assertEqual(j.status, J.CANCELLED)
        self.assertFalse(j.is_open)
        self.assertEqual(Ambulance.objects.get(pk=j.ambulance_id).status, "available")

    def test_pause_resume(self):
        em = self.request(self.p1)
        t = timezone.now()
        journey.tick(t)
        j = self.j(em)
        dur = journey.sim_duration(j.route_to_patient, j.sim_speedup)
        journey.act(j, "pause", self.op, now=t + timedelta(seconds=dur * 0.3))
        journey.tick(t + timedelta(seconds=dur * 5))  # time passes while paused
        j.refresh_from_db()
        self.assertEqual(j.status, J.EN_ROUTE_TO_PATIENT)
        self.assertAlmostEqual(journey.state(j, t + timedelta(seconds=dur * 5))["progress"], 0.3, places=2)
        journey.act(j, "resume", self.op, now=t + timedelta(seconds=dur * 5))
        j.refresh_from_db()
        self.assertAlmostEqual(journey.state(j, t + timedelta(seconds=dur * 5.2))["progress"], 0.5, places=2)

    def test_destination_unavailable_falls_back(self):
        em = self.request(self.p1)
        j = self.j(em)
        if j.destination_hospital == j.start_hospital:
            return
        t = timezone.now()
        journey.tick(t)
        t, _ = self.run_leg(j, t)
        j.destination_hospital.is_active = False
        j.destination_hospital.save()
        journey.tick(t + timedelta(seconds=6))    # -> picking up
        journey.tick(t + timedelta(seconds=30))   # pickup done -> destination chosen
        j.refresh_from_db()
        self.assertEqual(j.destination_hospital, j.start_hospital)
        self.assertEqual(j.status, J.EN_ROUTE_TO_HOSPITAL)

    # ------------------------------------------------------------------ permissions / privacy
    def test_permissions(self):
        e1, e2 = self.request(self.p1, PATIENT_A), self.request(self.p2, PATIENT_B)
        api = APIClient()
        api.force_authenticate(self.p1)
        self.assertEqual(api.get(f"/api/journeys/by-emergency/{e1.reference}/").status_code, 200)
        self.assertEqual(api.get(f"/api/journeys/by-emergency/{e2.reference}/").status_code, 403)
        self.assertEqual(api.get(f"/api/journeys/{self.j(e2).pk}/").status_code, 404)
        self.assertEqual([j["emergency"] for j in api.get("/api/journeys/").json()], [e1.reference])
        self.assertEqual(api.post(f"/api/journeys/{self.j(e1).pk}/action/", {"action": "pause"}).status_code, 403)
        api.force_authenticate(self.op)
        self.assertEqual(len(api.get("/api/journeys/").json()), 2)
        d = api.get(f"/api/journeys/by-emergency/{e1.reference}/").json()
        self.assertTrue(d["simulated"])
        self.assertEqual(d["label"], "SIMULATED")

    def test_real_gps_ambulance_never_simulated(self):
        Ambulance.objects.update(location_is_simulated=False)
        em = self.request(self.p1)
        self.assertFalse(AmbulanceJourney.objects.filter(emergency=em).exists())

    def test_dashboard_pages_render(self):
        em = self.request(self.p1)
        self.client.force_login(self.op)
        from django.urls import reverse
        r = self.client.get(reverse("emergency_detail", args=[em.pk]))
        self.assertContains(r, "js/journey-map.")
        self.assertContains(r, "journey-panel")
        self.client.force_login(self.p1)
        r = self.client.get(reverse("track", args=[em.reference]))
        self.assertContains(r, "journey-panel")
