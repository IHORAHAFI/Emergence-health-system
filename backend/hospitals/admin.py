from django.contrib import admin

from .models import Hospital, Specialty


@admin.register(Hospital)
class HospitalAdmin(admin.ModelAdmin):
    list_display = ("name", "level", "phone", "available_beds", "icu_available", "has_emergency_unit",
                    "capacity_verified", "is_active")
    list_filter = ("level", "is_active", "has_emergency_unit", "capacity_verified", "specialties")
    search_fields = ("name", "address")
    filter_horizontal = ("specialties",)


admin.site.register(Specialty)
