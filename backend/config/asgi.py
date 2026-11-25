"""ASGI entry point: HTTP (Django) + WebSockets (Channels) for live vital signs.

`python manage.py runserver` uses this automatically because `daphne` is in INSTALLED_APPS.
Production: `daphne -b 0.0.0.0 -p 8000 config.asgi:application` behind HTTPS (wss://).
"""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django_asgi_app = get_asgi_application()

from channels.auth import AuthMiddlewareStack  # noqa: E402
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402

from patient_monitoring.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AllowedHostsOriginValidator(AuthMiddlewareStack(URLRouter(websocket_urlpatterns))),
})
