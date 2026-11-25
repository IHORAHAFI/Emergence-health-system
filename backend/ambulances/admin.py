from django.contrib import admin

from .models import Ambulance, AmbulanceAssignment


@admin.register(Ambulance)
class AmbulanceAdmin(admin.ModelAdmin):
    list_display = ("plate_number", "ambulance_type", "status", "base_hospital", "driver", "contact_phone",
                    "location_is_simulated", "last_location_update")
    list_filter = ("status", "ambulance_type", "location_is_simulated")
    search_fields = ("plate_number", "driver__username", "contact_phone")


@admin.register(AmbulanceAssignment)
class AmbulanceAssignmentAdmin(admin.ModelAdmin):
    list_display = ("ambulance", "emergency", "assigned_at", "distance_km", "eta_minutes", "outcome", "released_at")
    list_filter = ("outcome",)
    search_fields = ("ambulance__plate_number", "emergency__reference")
