from django.urls import path

from .consumers import NotifyConsumer, VitalsConsumer

websocket_urlpatterns = [
    path("ws/notify/", NotifyConsumer.as_asgi()),
    path("ws/vitals/", VitalsConsumer.as_asgi()),
    path("ws/vitals/<str:ref>/", VitalsConsumer.as_asgi()),
]
