from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from accounts.views import MeView, PatientProfileView, RegisterView, register_fcm_token
from ambulances.views import AmbulanceViewSet
from emergency.views import EmergencyViewSet, ai_preview
from hospitals.views import HospitalViewSet
from notifications.views import NotificationViewSet
from . import patient_api as pa
from . import staff_api as sa
from . import proactive_api as pr
from . import journey_api as ja
from . import measurements_api as ma

router = DefaultRouter()
router.register("hospitals", HospitalViewSet, basename="hospital")
router.register("ambulances", AmbulanceViewSet, basename="ambulance")
router.register("emergencies", EmergencyViewSet, basename="emergency")
router.register("notifications", NotificationViewSet, basename="notification")

urlpatterns = [
    path("auth/register/", RegisterView.as_view()),
    path("auth/login/", TokenObtainPairView.as_view()),
    path("auth/refresh/", TokenRefreshView.as_view()),
    path("auth/me/", MeView.as_view()),
    path("auth/profile/", PatientProfileView.as_view()),
    path("auth/fcm-token/", register_fcm_token),
    path("ai/preview/", ai_preview),
    path("auth/password-reset/", sa.password_reset),
    path("auth/password-reset/confirm/", sa.password_reset_confirm),
    path("auth/password-change/", sa.password_change),
    path("assistant/preferences/", pr.preferences),
    path("home-monitoring/consent/", pr.home_consent),
    path("home-monitoring/readings/", pr.home_reading),
    path("monitoring/location/", pr.location),
    path("monitoring/events/", pr.events),
    path("monitoring/events/<int:pk>/respond/", pr.event_respond),
    path("caregivers/", pr.caregiver_links),
    path("caregivers/invite/", pr.caregiver_invite),
    path("caregivers/accept/", pr.caregiver_accept),
    path("caregivers/<int:pk>/", pr.caregiver_link),
    path("caregivers/patients/<int:pid>/", pr.caregiver_patient),
    path("caregivers/patients/<int:pid>/emergency/", pr.caregiver_emergency),
    path("caregivers/patients/<int:pid>/tests/", pr.caregiver_tests),
    path("caregivers/patients/<int:pid>/report/", pr.caregiver_report),
    path("staff/events/", pr.staff_events),
    path("staff/events/<int:pk>/close/", pr.staff_event_close),
    path("measurements/", ma.measurements),
    path("journeys/", ja.journeys),
    path("journeys/start/", ja.journey_start),
    path("journeys/by-emergency/<str:ref>/", ja.journey_for_emergency),
    path("journeys/<int:pk>/", ja.journey_detail),
    path("journeys/<int:pk>/action/", ja.journey_action),
    path("journeys/<int:pk>/pickup-location/", ja.journey_pickup_location),
    path("staff/alerts/", sa.alerts),
    path("staff/alerts/accept/", sa.alert_accept),
    path("staff/tests/", sa.tests),
    path("staff/tests/<str:ref>/action/", sa.test_action),
    path("staff/stats/", sa.stats),
    path("tests/", pa.tests),
    path("tests/catalog/", pa.test_catalog),
    path("tests/<str:ref>/", pa.test_detail),
    path("tests/<str:ref>/cancel/", pa.test_cancel),
    path("tests/<str:ref>/readings/", pa.test_reading),
    path("vitals/latest/", pa.latest_vitals),
    path("diseases/", pa.diseases),
    path("diseases/<slug:slug>/", pa.disease_detail),
    path("assistant/ask/", pa.assistant_ask),
    path("assistant/history/", pa.assistant_history),
    path("assistant/history/<int:pk>/", pa.assistant_history),
    path("", include(router.urls)),
]
