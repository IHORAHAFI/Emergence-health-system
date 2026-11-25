from django.urls import path

from . import api, sim_views, views

web_urlpatterns = [
    path("", views.board, name="vitals_board"),
    path("alerts/", views.alerts, name="vitals_alerts"),
    # simulator (must come before the <ref> patterns)
    path("simulator/", sim_views.simulator_home, name="simulator"),
    path("simulator/<int:pk>/", sim_views.panel, name="sim_panel"),
    path("simulator/<int:pk>/state.json", sim_views.state, name="sim_state"),
    path("simulator/<int:pk>/tick/", sim_views.tick, name="sim_tick"),
    path("simulator/<int:pk>/control/", sim_views.control, name="sim_control"),
    path("simulator/<int:pk>/device/<int:device_pk>/", sim_views.device, name="sim_device"),
    path("<str:ref>/", views.detail, name="vitals_detail"),
    path("<str:ref>/measure/", views.measure, name="vitals_measure"),
    path("<str:ref>/consent/", views.consent, name="vitals_consent"),
]

api_urlpatterns = [
    path("readings/<int:pk>/review/", api.review, name="api_vitals_review"),
    path("<str:ref>/consent/", api.consent, name="api_vitals_consent"),
    path("<str:ref>/readings/", api.readings, name="api_vitals"),
    path("<str:ref>/readings/simulate/", api.simulate, name="api_vitals_simulate"),
]
