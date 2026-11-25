from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from accounts.views import MeView, PatientProfileView, RegisterView, register_fcm_token
from ambulances.views import AmbulanceViewSet
from emergency.views import EmergencyViewSet, ai_preview
from hospitals.views import HospitalViewSet
from notifications.views import NotificationViewSet

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
    path("", include(router.urls)),
]
