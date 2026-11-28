from django.contrib.auth import views as auth_views
from django.urls import path

from accounts import admin_views
from hospitals import web_views as hospital_views
from . import web_views

urlpatterns = [
    # hospitals
    path("hospitals/", hospital_views.hospital_list, name="hospital_list"),
    path("hospitals/new/", hospital_views.hospital_edit, name="hospital_new"),
    path("hospitals/<int:pk>/", hospital_views.hospital_detail, name="hospital_detail"),
    path("hospitals/<int:pk>/edit/", hospital_views.hospital_edit, name="hospital_edit"),
    # administration
    path("users/", admin_views.user_list, name="user_list"),
    path("users/<int:pk>/action/", admin_views.user_action, name="user_action"),
    path("patients/", admin_views.patient_list, name="patient_list"),
    path("patients/<int:pk>/", admin_views.patient_detail, name="patient_detail"),
    path("activity/", admin_views.activity_log, name="activity_log"),
    # kept for old bookmarks; the main login is /accounts/login/
    path("login/", auth_views.LoginView.as_view(template_name="portal/login.html")),
    path("", web_views.dashboard, name="dashboard"),
    path("emergencies/", web_views.emergencies_list, name="emergencies"),
    path("emergency/<int:pk>/", web_views.emergency_detail, name="emergency_detail"),
    path("emergency/<int:pk>/action/", web_views.emergency_action, name="emergency_action"),
    path("live-locations.json", web_views.live_locations, name="live_locations"),
    path("measurements.json", web_views.measurements_feed, name="measurements_feed"),
    path("iot.json", web_views.iot_feed, name="iot_feed"),
    path("home-vitals.json", web_views.home_vitals_feed, name="home_vitals_feed"),
    path("home-vitals/control/", web_views.home_vitals_control, name="home_vitals_control"),
    path("ambulances/", web_views.ambulances_page, name="ambulances"),
    path("ambulances/<int:pk>/action/", web_views.ambulance_action, name="ambulance_action"),
    path("ambulances/new/", web_views.ambulance_edit, name="ambulance_new"),
    path("ambulances/<int:pk>/edit/", web_views.ambulance_edit, name="ambulance_edit"),
    path("approvals/", web_views.approvals, name="approvals"),
    path("hospital/capacity/", web_views.hospital_capacity, name="hospital_capacity"),
    path("simulate/", web_views.simulate_emergency, name="simulate"),
    path("reports/", web_views.reports, name="reports"),
]
