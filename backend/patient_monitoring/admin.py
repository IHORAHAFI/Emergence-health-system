from django.contrib import admin

from .models import (AccessAuditLog, AlertThreshold, ClinicalReview, EmergencyAlert, MedicalDevice, MonitoringConsent, SimulationSession,
                     VirtualDevice, VirtualPatient, VitalSignReading)


@admin.register(VitalSignReading)
class VitalSignReadingAdmin(admin.ModelAdmin):
    list_display = ("emergency", "measured_at", "source", "temperature_c", "heart_rate_bpm", "spo2_pct",
                    "systolic_mmhg", "diastolic_mmhg", "news2_score", "risk")
    list_filter = ("source", "risk")
    search_fields = ("emergency__reference", "patient__username")


@admin.register(AccessAuditLog)
class AccessAuditLogAdmin(admin.ModelAdmin):
    list_display = ("at", "user", "emergency", "action", "detail", "ip_address")
    list_filter = ("action",)
    search_fields = ("user__username", "emergency__reference")

    def has_change_permission(self, request, obj=None):  # audit trail is append-only
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(VirtualPatient)
class VirtualPatientAdmin(admin.ModelAdmin):
    list_display = ("patient_code", "full_name", "age", "gender", "created_at")
    readonly_fields = ("patient_code", "user")


class VirtualDeviceInline(admin.TabularInline):
    model = VirtualDevice
    extra = 0


@admin.register(SimulationSession)
class SimulationSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "virtual_patient", "scenario", "emergency", "status", "step", "speed", "created_at")
    list_filter = ("status", "scenario")
    inlines = [VirtualDeviceInline]


@admin.register(AlertThreshold)
class AlertThresholdAdmin(admin.ModelAdmin):
    list_display = ("parameter", "age_group", "low", "high", "enabled")
    list_editable = ("low", "high", "enabled")


@admin.register(EmergencyAlert)
class EmergencyAlertAdmin(admin.ModelAdmin):
    list_display = ("created_at", "emergency", "message", "is_simulated", "status", "acknowledged_by")
    list_filter = ("status", "is_simulated", "parameter")


admin.site.register(MedicalDevice)
admin.site.register(MonitoringConsent)
admin.site.register(ClinicalReview)
