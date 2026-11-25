from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("title", "channel", "recipient", "hospital", "destination", "delivery_status",
                    "provider", "is_read", "created_at")
    list_filter = ("channel", "delivery_status", "provider")
    search_fields = ("title", "message", "destination", "emergency__reference")
