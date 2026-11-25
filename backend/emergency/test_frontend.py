"""Tests for the redesigned frontend: navigation by role, new management pages,
medical information review workflow and the extended emergency form."""
from django.urls import reverse

from accounts.models import User
from ambulances.models import Ambulance
from hospitals.models import Hospital
from medical_rag.models import Disease
from .models import EmergencyRequest
from .test_system import Base, form_data

ACCOUNTS = {"admin": "admin12345", "operator": "operator123", "chuk_staff": "hospital123",
            "patient1": "patient123", "paramedic1": "paramedic123"}


class NavigationTests(Base):
    def test_public_pages_render_for_visitors(self):
        for name in ("landing", "login", "register", "assistant", "disease_list"):
            r = self.client.get(reverse(name))
            self.assertEqual(r.status_code, 200, name)
            self.assertContains(r, "public-nav")

    def test_patient_sidebar_has_no_admin_links(self):
        self.login("patient1", "patient123")
        r = self.client.get(reverse("patient_home"))
        self.assertContains(r, 'class="sidebar"')
        for admin_link in ("user_list", "patient_list", "ambulances", "activity_log", "reports"):
            self.assertNotContains(r, f'href="{reverse(admin_link)}"')
        self.assertContains(r, reverse("request_emergency"))

    def test_admin_sidebar_has_management_links(self):
        self.login("admin", "admin12345")
        r = self.client.get(reverse("dashboard"))
        for link in ("user_list", "patient_list", "ambulances", "hospital_list", "activity_log", "reports",
                     "disease_list", "assistant"):
            self.assertContains(r, f'href="{reverse(link)}"')

    def test_every_page_renders_for_each_allowed_role(self):
        pages = {
            "admin": ["dashboard", "emergencies", "ambulances", "ambulance_new", "hospital_list", "hospital_new",
                      "user_list", "patient_list", "activity_log", "reports", "disease_list", "disease_new",
                      "assistant", "notifications", "profile"],
            "operator": ["dashboard", "ambulances", "hospital_list", "patient_list", "activity_log"],
            "chuk_staff": ["dashboard", "hospital_list", "hospital_capacity", "disease_list", "disease_new"],
            "patient1": ["patient_home", "request_emergency", "hospitals", "disease_list", "assistant"],
            "paramedic1": ["paramedic_home", "disease_list"],
        }
        for user, names in pages.items():
            self.login(user, ACCOUNTS[user])
            for name in names:
                self.assertEqual(self.client.get(reverse(name)).status_code, 200, f"{user} {name}")
            self.client.logout()
        self.login("admin", "admin12345")
        for tab in ("emergency", "notifications", "access"):
            self.assertEqual(self.client.get(reverse("activity_log"), {"tab": tab}).status_code, 200)

    def test_rbac_on_new_pages(self):
        forbidden = {
            "patient1": ["user_list", "patient_list", "activity_log", "hospital_list", "ambulance_new", "disease_new"],
            "paramedic1": ["user_list", "patient_list", "hospital_list"],
            "chuk_staff": ["user_list", "patient_list", "activity_log", "hospital_new", "ambulance_new"],
            "operator": ["user_list"],
        }
        for user, names in forbidden.items():
            self.login(user, ACCOUNTS[user])
            for name in names:
                self.assertEqual(self.client.get(reverse(name)).status_code, 403, f"{user} {name}")
            self.client.logout()


class DashboardTests(Base):
    def test_dashboard_charts_use_real_counts(self):
        self.patient_request()
        self.client.logout()
        self.login("operator", "operator123")
        r = self.client.get(reverse("dashboard"))
        charts = r.context["charts"]
        self.assertEqual(sum(charts["status"]["data"]), EmergencyRequest.objects.count())
        self.assertEqual(sum(charts["trend"]["data"]), 1)
        self.assertEqual(len(charts["trend"]["labels"]), 14)
        self.assertEqual(charts["priority"]["labels"], ["Critical", "High", "Medium", "Low"])
        self.assertIn(EmergencyRequest.objects.get(), list(r.context["attention"]) + list(r.context["active"]))


class EmergencyFormTests(Base):
    def test_new_fields_are_stored(self):
        self.patient_request(patient_name="Jane Doe", gender="female", symptoms="vomiting, dizzy",
                             patient_condition="unresponsive", seizure="on", request_medical_advice="on")
        em = EmergencyRequest.objects.get()
        self.assertEqual((em.patient_name, em.gender, em.symptoms), ("Jane Doe", "female", "vomiting, dizzy"))
        self.assertTrue(em.seizure and em.request_medical_advice)
        self.assertFalse(em.conscious)  # "not responding" is passed to the AI as not conscious
        self.assertFalse(em.request_ambulance)  # only advice ticked - recorded exactly as chosen

    def test_no_service_ticked_still_sends_ambulance(self):
        self.patient_request()
        self.assertTrue(EmergencyRequest.objects.get().request_ambulance)

    def test_form_prefilled_from_profile(self):
        self.login("patient1", "patient123")
        r = self.client.get(reverse("request_emergency"))
        u = User.objects.get(username="patient1")
        self.assertEqual(r.context["form"].initial["patient_name"], u.get_full_name())


class AmbulanceManagementTests(Base):
    def test_register_ambulance(self):
        self.login("operator", "operator123")
        h = Hospital.objects.first()
        r = self.client.post(reverse("ambulance_new"), {"plate_number": "RAD 999 Z", "ambulance_type": "als",
                                                        "base_hospital": h.id, "latitude": h.latitude,
                                                        "longitude": h.longitude, "contact_phone": "+250788000000"})
        self.assertRedirects(r, reverse("ambulances"))
        a = Ambulance.objects.get(plate_number="RAD 999 Z")
        self.assertEqual(a.status, "available")
        self.assertTrue(a.location_is_simulated)  # typed in, not live GPS

    def test_invalid_coordinates_rejected(self):
        self.login("operator", "operator123")
        r = self.client.post(reverse("ambulance_new"), {"plate_number": "BAD 1", "ambulance_type": "bls",
                                                        "latitude": 200, "longitude": 30})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Ambulance.objects.filter(plate_number="BAD 1").exists())

    def test_simple_status_labels(self):
        a = Ambulance(status="on_the_way")
        self.assertEqual(a.simple_status, "En route")
        self.assertEqual(Ambulance(status="offline").simple_status, "Unavailable")


class HospitalManagementTests(Base):
    def test_edit_beds_marks_capacity_unverified(self):
        h = Hospital.objects.first()
        h.capacity_verified = True
        h.save()
        self.login("admin", "admin12345")
        data = {"name": h.name, "level": h.level, "latitude": h.latitude, "longitude": h.longitude,
                "total_beds": 100, "available_beds": 40, "icu_available": 2, "is_active": "on",
                "has_emergency_unit": "on", "email": "info@example.org"}
        r = self.client.post(reverse("hospital_edit", args=[h.id]), data)
        self.assertRedirects(r, reverse("hospital_detail", args=[h.id]))
        h.refresh_from_db()
        self.assertEqual(h.available_beds, 40)
        self.assertFalse(h.capacity_verified)

    def test_available_cannot_exceed_total(self):
        h = Hospital.objects.first()
        self.login("admin", "admin12345")
        r = self.client.post(reverse("hospital_edit", args=[h.id]), {
            "name": h.name, "level": h.level, "latitude": h.latitude, "longitude": h.longitude,
            "total_beds": 10, "available_beds": 50, "icu_available": 0})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "cannot exceed")

    def test_incoming_patients_hidden_from_other_hospitals_staff(self):
        self.patient_request()
        em = EmergencyRequest.objects.get()
        self.client.logout()
        staff = User.objects.get(username="chuk_staff")
        other = Hospital.objects.exclude(id=staff.hospital_id).first()
        em.assigned_hospital = other
        em.save()
        self.login("chuk_staff", "hospital123")
        r = self.client.get(reverse("hospital_detail", args=[other.id]))
        self.assertFalse(r.context["can_see_patients"])
        self.assertNotContains(r, em.reference)


class MedicalInformationTests(Base):
    def test_library_loaded_as_pending(self):
        self.assertGreaterEqual(Disease.objects.count(), 10)
        self.assertFalse(Disease.objects.filter(review_status="approved").exists())
        self.assertTrue(all(d.source_url.startswith("https://") for d in Disease.objects.all()))

    def test_patients_only_see_reviewed_entries(self):
        d = Disease.objects.get(name="Malaria")
        self.login("patient1", "patient123")
        self.assertEqual(self.client.get(reverse("disease_detail", args=[d.slug])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("disease_list")), "Malaria")
        self.client.logout()
        # an approved healthcare professional reviews it
        self.login("chuk_staff", "hospital123")
        self.client.post(reverse("disease_review", args=[d.slug]), {"decision": "approve"})
        d.refresh_from_db()
        self.assertTrue(d.is_reviewed)
        self.assertEqual(d.reviewed_by.username, "chuk_staff")
        self.client.logout()
        self.login("patient1", "patient123")
        r = self.client.get(reverse("disease_detail", args=[d.slug]))
        self.assertContains(r, "Emergency warning signs")
        self.assertContains(r, d.source_url)

    def test_patient_cannot_review_or_edit(self):
        d = Disease.objects.first()
        self.login("patient1", "patient123")
        self.assertEqual(self.client.post(reverse("disease_review", args=[d.slug]), {"decision": "approve"}).status_code, 403)
        self.assertEqual(self.client.get(reverse("disease_edit", args=[d.slug])).status_code, 403)

    def test_editing_resets_review(self):
        d = Disease.objects.get(name="Asthma")
        d.review_status = "approved"
        d.save()
        self.login("admin", "admin12345")
        self.client.post(reverse("disease_edit", args=[d.slug]), {
            "name": d.name, "category": d.category, "description": "Changed text", "source_name": d.source_name,
            "source_url": d.source_url})
        d.refresh_from_db()
        self.assertEqual(d.description, "Changed text")
        self.assertEqual(d.review_status, "pending")

    def test_search_and_category_filter(self):
        Disease.objects.update(review_status="approved")
        r = self.client.get(reverse("disease_list"), {"q": "mosquito"})
        self.assertContains(r, "Malaria")
        r = self.client.get(reverse("disease_list"), {"category": "childhood"})
        self.assertContains(r, "Measles")
        self.assertNotContains(r, "Hypertension")

    def test_assistant_links_library_entries(self):
        Disease.objects.update(review_status="approved")
        r = self.client.post(reverse("assistant_ask"), {"question": "what are the signs of stroke"},
                             content_type="application/json")
        names = [x["name"] for x in r.json()["library"]]
        self.assertIn("Stroke", names)


class AdminPanelTests(Base):
    def test_deactivate_and_activate_user(self):
        self.login("admin", "admin12345")
        u = User.objects.get(username="patient1")
        self.client.post(reverse("user_action", args=[u.id]), {"action": "deactivate"})
        u.refresh_from_db()
        self.assertFalse(u.is_active)
        self.client.post(reverse("user_action", args=[u.id]), {"action": "activate"})
        u.refresh_from_db()
        self.assertTrue(u.is_active)

    def test_admin_cannot_deactivate_self(self):
        self.login("admin", "admin12345")
        me = User.objects.get(username="admin")
        self.client.post(reverse("user_action", args=[me.id]), {"action": "deactivate"})
        me.refresh_from_db()
        self.assertTrue(me.is_active)

    def test_user_search_and_filter(self):
        self.login("admin", "admin12345")
        r = self.client.get(reverse("user_list"), {"role": "paramedic"})
        self.assertTrue(all(u.role == "paramedic" for u in r.context["page"]))
        r = self.client.get(reverse("user_list"), {"state": "pending"})
        self.assertIn("nurse_pending", [u.username for u in r.context["page"]])

    def test_patient_detail_for_operator(self):
        self.login("operator", "operator123")
        p = User.objects.get(username="patient1")
        r = self.client.get(reverse("patient_detail", args=[p.id]))
        self.assertContains(r, "Confidential")


class DesignV2Tests(Base):
    def test_pwa_files(self):
        r = self.client.get("/sw.js")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/javascript")
        self.assertIn("never stored", r.content.decode())  # privacy rule documented in the worker
        self.assertContains(self.client.get(reverse("offline")), "tel:")
        self.assertContains(self.client.get(reverse("landing")), "manifest.webmanifest")

    def test_staff_get_dark_theme_patients_light(self):
        self.login("operator", "operator123")
        self.assertContains(self.client.get(reverse("dashboard")), 'data-bs-theme="dark"')
        self.client.logout()
        self.login("patient1", "patient123")
        self.assertNotContains(self.client.get(reverse("patient_home")), 'data-bs-theme="dark"')

    def test_patient_service_grid_links_work(self):
        import re
        self.login("patient1", "patient123")
        html = self.client.get(reverse("patient_home")).content.decode()
        nav = html[html.index('class="pnav"'):html.index("</nav>", html.index('class="pnav"'))]
        links = re.findall(r'href="([^"]+)"', nav)
        self.assertGreaterEqual(len(links), 6)
        for url in links:
            self.assertEqual(self.client.get(url.split("#")[0]).status_code, 200, url)

    def test_patient_vitals_come_from_own_readings(self):
        from patient_monitoring import test_requests as svc
        a, b = User.objects.get(username="patient1"), User.objects.get(username="patient2")
        op = User.objects.get(username="operator")
        req = svc.create_request(b, ["temperature"])
        svc.accept(req, op)
        svc.start(req, op)
        svc.record_test_reading(req, {"temperature_c": 39.4}, "simulated", None)
        self.login("patient1", "patient123")
        cards = {c["key"]: c for c in self.client.get(reverse("patient_home")).context["vitals"]}
        self.assertIsNone(cards["temperature_c"]["value"])  # patient B's reading never shows for A
        self.client.logout()
        self.login("patient2", "patient123")
        cards = {c["key"]: c for c in self.client.get(reverse("patient_home")).context["vitals"]}
        self.assertEqual(cards["temperature_c"]["value"], 39.4)
        self.assertTrue(cards["temperature_c"]["abnormal"] and cards["temperature_c"]["simulated"])

    def test_dashboard_sparklines_use_real_counts(self):
        self.login("operator", "operator123")
        spark = self.client.get(reverse("dashboard")).context["charts"]["spark"]
        self.assertEqual(len(spark["emergencies"]), 14)
        self.assertEqual(sum(spark["emergencies"]), EmergencyRequest.objects.filter(
            created_at__date__gte=__import__("django.utils.timezone", fromlist=["x"]).localdate()
            - __import__("datetime").timedelta(days=13)).count())
