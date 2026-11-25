from django.contrib import admin

from .models import EmergencyRequest, StatusLog


class StatusLogInline(admin.TabularInline):
    model = StatusLog
    extra = 0
    readonly_fields = ("status", "note", "by", "at")


@admin.register(EmergencyRequest)
class EmergencyAdmin(admin.ModelAdmin):
    list_display = ("reference", "patient", "emergency_type", "priority", "triage_method", "status",
                    "assigned_hospital", "assigned_ambulance", "is_simulated", "created_at")
    list_filter = ("status", "priority", "emergency_type", "triage_method", "is_simulated")
    search_fields = ("reference", "patient__username", "patient__last_name", "description")
    readonly_fields = ("reference",)
    inlines = [StatusLogInline]
