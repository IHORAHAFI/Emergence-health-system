from django.contrib import admin

from .models import IoTDevice, PatientMeasurement, SensorReading
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


@admin.register(PatientMeasurement)
class PatientMeasurementAdmin(admin.ModelAdmin):
    """Read-only: measurements come only from the patient's app."""
    list_display = ("measured_at", "patient", "kind", "status", "value", "unit", "source", "emergency")
    list_filter = ("kind", "status", "source")
    search_fields = ("patient__username", "emergency__reference", "client_id")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(IoTDevice)
class IoTDeviceAdmin(admin.ModelAdmin):
    """Approve Arduino nodes here. A device that appears on the serial port is listed as not approved first."""
    list_display = ("device_id", "name", "mode", "approved", "patient", "connected", "port", "last_seen", "firmware")
    list_filter = ("approved", "mode", "connected")
    list_editable = ("approved",)
    search_fields = ("device_id", "name")
    readonly_fields = ("firmware", "boot_id", "interval_ms", "connected", "port", "last_seen", "sensor_state",
                       "created_at")
    autocomplete_fields = ("patient",)


@admin.register(SensorReading)
class SensorReadingAdmin(admin.ModelAdmin):
    list_display = ("measured_at", "device", "sensor_type", "value", "unit", "source", "status", "patient")
    list_filter = ("source", "status", "sensor_type", "device")
    readonly_fields = [f.name for f in SensorReading._meta.fields]

    def has_add_permission(self, request):  # readings only ever come from a device through the bridge
        return False
