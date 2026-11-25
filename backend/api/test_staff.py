"""Operator / medical-staff / admin API used by the mobile app, and password reset."""
import re

from django.core import mail
from django.core.management import call_command
from rest_framework.test import APITestCase

from accounts.models import User
from emergency import services
from notifications.models import Notification
from patient_monitoring import test_requests as tr


class StaffApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_data", verbosity=0)

    def setUp(self):
        self.p1 = User.objects.get(username="patient1")
        self.p2 = User.objects.get(username="patient2")
        self.op = User.objects.get(username="operator")
        self.staff = User.objects.get(username="chuk_staff")

    def em(self, p):
        return services.create_emergency(p, {"emergency_type": "other", "latitude": -1.95, "longitude": 30.06})

    def test_operator_alerts_and_accept(self):
        e1, e2 = self.em(self.p1), self.em(self.p2)
        self.client.force_authenticate(self.op)
        d = self.client.get("/api/staff/alerts/").data
        self.assertEqual(d["alert_count"], 2)
        self.assertIn("siren_url", d)
        self.assertEqual(d["alerts"][0]["alert_count"], 1)
        d = self.client.post("/api/staff/alerts/accept/", {"key": f"emergency-{e1.pk}"}, format="json").data
        self.assertEqual([a["key"] for a in d["alerts"]], [f"emergency-{e2.pk}"])
        r = self.client.post(f"/api/emergencies/{e2.pk}/accept/")
        self.assertEqual(r.data["operator_label"], "ACCEPTED" if r.data["status"] in ("pending", "assigned") else "IN PROGRESS")
        self.assertEqual(self.client.get("/api/staff/alerts/").data["alert_count"], 0)

    def test_patients_and_staff_cannot_use_operator_endpoints(self):
        e = self.em(self.p1)
        for u in (self.p1, self.staff):
            self.client.force_authenticate(u)
            self.assertEqual(self.client.get("/api/staff/alerts/").status_code, 403)
            self.assertEqual(self.client.get("/api/staff/stats/").status_code, 403)
            self.assertIn(self.client.post(f"/api/emergencies/{e.pk}/accept/").status_code, (403, 404))
        self.client.force_authenticate(self.p2)  # another patient cannot even see it
        self.assertEqual(self.client.get(f"/api/emergencies/{e.pk}/").status_code, 404)
        self.assertEqual(self.client.post(f"/api/emergencies/{e.pk}/message/", {"text": "hi"}).status_code, 404)

    def test_messages_between_patient_and_team(self):
        e = self.em(self.p1)
        self.client.force_authenticate(self.op)
        self.client.post(f"/api/emergencies/{e.pk}/message/", {"text": "An ambulance is coming."}, format="json")
        self.assertTrue(Notification.objects.filter(recipient=self.p1, message__contains="An ambulance is coming").exists())
        self.client.force_authenticate(self.p1)
        self.client.post(f"/api/emergencies/{e.pk}/message/", {"text": "Thank you"}, format="json")
        self.assertTrue(Notification.objects.filter(recipient=self.op, message__contains="Thank you").exists())

    def test_operator_test_workflow(self):
        req = tr.create_request(self.p1, ["heart_rate"])
        self.client.force_authenticate(self.op)
        self.assertEqual(len(self.client.get("/api/staff/tests/?open=1").data), 1)
        for act, st in (("accept", "accepted"), ("start", "testing")):
            r = self.client.post(f"/api/staff/tests/{req.reference}/action/", {"action": act}, format="json")
            self.assertEqual(r.data["status"], st, r.data)
        r = self.client.post(f"/api/staff/tests/{req.reference}/action/", {"action": "complete"}, format="json")
        self.assertEqual(r.status_code, 400)  # no reading yet - cannot claim a result
        self.client.force_authenticate(self.p1)
        self.assertEqual(self.client.post(f"/api/staff/tests/{req.reference}/action/", {"action": "accept"}).status_code, 403)

    def test_stats(self):
        self.client.force_authenticate(self.op)
        d = self.client.get("/api/staff/stats/").data
        self.assertIn("patient", d["users_by_role"])
        self.assertIn("real", d["devices"])

    def test_password_reset_flow(self):
        self.p1.email = "p1@example.org"
        self.p1.save()
        self.client.post("/api/auth/password-reset/", {"email": "nobody@example.org"})
        self.assertEqual(len(mail.outbox), 0)
        r = self.client.post("/api/auth/password-reset/", {"email": "P1@example.org"})
        self.assertEqual(r.status_code, 200)
        body = mail.outbox[0].body
        uid = re.search(r"User ID code: (\S+)", body).group(1)
        token = re.search(r"Reset code: (\S+)", body).group(1)
        self.assertEqual(self.client.post("/api/auth/password-reset/confirm/",
                                          {"uid": uid, "token": "bad", "new_password": "N3w-secret-pass"}).status_code, 400)
        r = self.client.post("/api/auth/password-reset/confirm/", {"uid": uid, "token": token, "new_password": "N3w-secret-pass"})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(self.client.post("/api/auth/login/", {"username": "patient1", "password": "N3w-secret-pass"}).data["access"])
        # the same code cannot be used twice
        self.assertEqual(self.client.post("/api/auth/password-reset/confirm/",
                                          {"uid": uid, "token": token, "new_password": "Other-pass-123"}).status_code, 400)

    def test_password_change(self):
        self.client.force_authenticate(self.p1)
        self.assertEqual(self.client.post("/api/auth/password-change/", {"old_password": "wrong", "new_password": "N3w-secret-pass"}).status_code, 400)
        self.assertEqual(self.client.post("/api/auth/password-change/", {"old_password": "patient123", "new_password": "123"}).status_code, 400)
        self.assertEqual(self.client.post("/api/auth/password-change/", {"old_password": "patient123", "new_password": "N3w-secret-pass"}).status_code, 200)
