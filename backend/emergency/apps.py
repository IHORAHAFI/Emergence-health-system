from django.apps import AppConfig


class EmergencyConfig(AppConfig):
    name = "emergency"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from . import scheduler
        scheduler.start_background()  # only inside a web-server process; see scheduler.py
