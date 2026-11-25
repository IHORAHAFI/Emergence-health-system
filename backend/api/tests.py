"""Mobile patient API: own-records-only access, test workflow, Bluetooth readings, library, assistant."""
from django.core.management import call_command
from rest_framework.test import APITestCase

from accounts.models import User
from patient_monitoring import test_requests as svc
from patient_monitoring.models import MedicalTestRequest, VitalSignReading


class PatientApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_data", verbosity=0)

    def setUp(self):
        self.a = User.objects.get(username="patient1")
        self.b = User.objects.get(username="patient2")
        self.op = User.objects.get(username="operator")
        self.client.force_authenticate(self.a)

    def _create(self, tests=("temperature",)):
        r = self.client.post("/api/tests/", {"tests": list(tests)}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        return r.data["reference"]

    def test_jwt_login_and_me(self):
        self.client.force_authenticate(None)
        self.a.set_password("Secret-pass-123")
        self.a.save()
        r = self.client.post("/api/auth/login/", {"username": "patient1", "password": "Secret-pass-123"})
        self.assertEqual(r.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + r.data["access"])
        self.assertEqual(self.client.get("/api/auth/me/").data["username"], "patient1")

    def test_create_list_and_duplicate(self):
        ref = self._create(["temperature", "spo2"])
        self.assertEqual([t["reference"] for t in self.client.get("/api/tests/").data], [ref])
        r = self.client.post("/api/tests/", {"tests": ["spo2"]}, format="json")
        self.assertEqual(r.status_code, 400)  # overlaps the open request
        self.assertEqual(self.client.post("/api/tests/", {"tests": ["bogus"]}, format="json").status_code, 400)

    def test_other_patient_cannot_see_or_cancel(self):
        ref = self._create()
        self.client.force_authenticate(self.b)
        self.assertEqual(self.client.get("/api/tests/").data, [])
        self.assertEqual(self.client.get(f"/api/tests/{ref}/").status_code, 404)
        self.assertEqual(self.client.post(f"/api/tests/{ref}/cancel/").status_code, 404)
        self.assertEqual(self.client.get("/api/vitals/latest/").data, [])

    def test_staff_cannot_use_patient_endpoints(self):
        self.client.force_authenticate(self.op)
        self.assertEqual(self.client.get("/api/tests/").status_code, 403)

    def test_cancel(self):
        ref = self._create()
        r = self.client.post(f"/api/tests/{ref}/cancel/")
        self.assertEqual(r.data["status"], "cancelled")

    def test_bluetooth_reading_only_while_testing(self):
        ref = self._create(["temperature"])
        body = {"temperature_c": 37.2, "device": {"device_identifier": "AA:BB", "name": "Thermo", "kind": "thermometer"}}
        self.assertEqual(self.client.post(f"/api/tests/{ref}/readings/", body, format="json").status_code, 400)
        req = MedicalTestRequest.objects.get(reference=ref)
        svc.accept(req, self.op)
        req.status = "testing"
        req.save()
        r = self.client.post(f"/api/tests/{ref}/readings/", body, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertFalse(r.data["simulated"])
        reading = VitalSignReading.objects.get(test_request=req)
        self.assertEqual((reading.source, reading.patient_id), ("bluetooth", self.a.id))
        latest = self.client.get("/api/vitals/latest/").data
        self.assertEqual(latest[0]["test_request"], ref)
        # missing device identification is refused
        self.assertEqual(self.client.post(f"/api/tests/{ref}/readings/", {"temperature_c": 37}, format="json")
                         .status_code, 400)

    def test_simulated_results_are_flagged(self):
        ref = self._create(["heart_rate"])
        req = MedicalTestRequest.objects.get(reference=ref)
        svc.accept(req, self.op)
        svc.start(req, self.op, "normal")
        svc.record_test_reading(req, {"heart_rate_bpm": 80}, "simulated",
                                {"device_identifier": "SIM-hr", "name": "Virtual HR", "kind": "simulator"})
        d = self.client.get(f"/api/tests/{ref}/").data
        self.assertTrue(d["summary"]["heart_rate_bpm"]["simulated"])
        self.assertTrue(all(r["simulated"] for r in d["readings"]))

    def test_notifications_read_all(self):
        self._create()
        svc.accept(MedicalTestRequest.objects.get(patient=self.a), self.op)
        notes = self.client.get("/api/notifications/?unread=1").data
        self.assertTrue(notes and notes[0]["test_request_ref"])
        self.client.post("/api/notifications/read-all/")
        self.assertEqual(self.client.get("/api/notifications/?unread=1").data, [])

    def test_library_and_assistant(self):
        lst = self.client.get("/api/diseases/").data
        self.assertTrue(all(d["reviewed"] for d in lst))
        if lst:
            d = self.client.get(f"/api/diseases/{lst[0]['slug']}/").data
            self.assertIn("symptoms", d)
        r = self.client.post("/api/assistant/ask/", {"question": "What are the signs of a stroke?"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertIn("disclaimer", r.data)
