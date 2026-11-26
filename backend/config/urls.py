from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from django.views.generic import TemplateView

from accounts import web_views as acc
from emergency import portal_views as portal
from patient_monitoring import test_views as tests, urls as monitoring

def service_worker(request):
    """Served from the site root so its scope covers every page (a /static/ path could not)."""
    from django.conf import settings
    from django.http import HttpResponse
    body = (settings.BASE_DIR / "static" / "js" / "sw.js").read_text(encoding="utf-8")
    return HttpResponse(body, content_type="application/javascript", headers={"Cache-Control": "no-cache"})


urlpatterns = [
    path("", portal.landing, name="landing"),
    path("sw.js", service_worker, name="service_worker"),
    path("offline/", TemplateView.as_view(template_name="offline.html"), name="offline"),
    path("home/", acc.home, name="home"),
    path("admin/", admin.site.urls),
    path("i18n/", include("django.conf.urls.i18n")),  # set_language (language switcher)
    path("api/monitoring/", include(monitoring.api_urlpatterns)),
    path("api/", include("api.urls")),
    path("monitoring/", include(monitoring.web_urlpatterns)),
    path("dashboard/", include("emergency.web_urls")),
    path("assistant/", include("medical_rag.urls")),
    path("medical/", include("medical_rag.disease_urls")),
    # accounts
    path("accounts/login/", auth_views.LoginView.as_view(template_name="portal/login.html",
                                                         redirect_authenticated_user=True), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("accounts/register/", acc.register_patient, name="register"),
    path("accounts/register/professional/", acc.register_professional, name="register_professional"),
    path("accounts/profile/", acc.profile, name="profile"),
    path("accounts/password/", auth_views.PasswordChangeView.as_view(
        template_name="portal/password.html", success_url="/accounts/profile/"), name="password_change"),
    # patient portal
    path("patient/", portal.patient_home, name="patient_home"),
    path("patient/emergency/new/", portal.request_emergency, name="request_emergency"),
    path("patient/request-contact/", portal.request_contact, name="request_contact"),
    path("emergency/<str:ref>/track/", portal.track, name="track"),
    path("emergency/<str:ref>/track.json", portal.track_json, name="track_json"),
    path("emergency/<str:ref>/cancel/", portal.cancel_emergency, name="cancel_emergency"),
    path("hospitals/", portal.hospitals_page, name="hospitals"),
    path("hospitals/nearby.json", portal.hospitals_json, name="hospitals_json"),
    path("notifications/", portal.notifications_page, name="notifications"),
    path("notifications/read-all/", portal.notifications_mark_read, name="notifications_read"),
    # medical tests (patient requests -> operator runs IoT test -> results back to patient)
    path("patient/tests/", tests.patient_tests, name="patient_tests"),
    path("patient/tests/<str:ref>/", tests.patient_test_detail, name="patient_test_detail"),
    path("patient/tests/<str:ref>/data.json", tests.test_data, name="test_data"),
    path("patient/tests/<str:ref>/cancel/", tests.patient_test_cancel, name="patient_test_cancel"),
    path("dashboard/test-requests/", tests.test_request_list, name="test_requests"),
    path("dashboard/test-requests/feed.json", tests.operator_feed, name="operator_feed"),
    path("dashboard/alerts/accept/", tests.alert_accept, name="alert_accept"),
    path("dashboard/service-requests/<int:pk>/", tests.service_request_detail, name="service_request_detail"),
    path("dashboard/service-requests/<int:pk>/action/", tests.service_request_action, name="service_request_action"),
    path("dashboard/test-requests/<str:ref>/", tests.test_request_detail, name="test_request_detail"),
    path("dashboard/test-requests/<str:ref>/action/", tests.test_request_action, name="test_request_action"),
    # ambulance crew
    path("crew/", portal.paramedic_home, name="paramedic_home"),
    path("crew/action/", portal.paramedic_action, name="paramedic_action"),
]
