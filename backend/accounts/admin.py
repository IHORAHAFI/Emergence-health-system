from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import PatientProfile, User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("Emergency system", {"fields": (
        "role", "phone", "hospital", "professional_id", "is_approved", "fcm_token")}),)
    add_fieldsets = UserAdmin.add_fieldsets + (("Emergency system", {"fields": ("role", "phone", "hospital")}),)
    list_display = ("username", "get_full_name", "role", "hospital", "phone", "is_approved", "is_active")
    list_filter = ("role", "is_approved", "is_active", "hospital")
    search_fields = ("username", "first_name", "last_name", "email", "phone", "professional_id")
    actions = ["approve"]

    @admin.action(description="Approve selected healthcare professionals")
    def approve(self, request, qs):
        self.message_user(request, f"{qs.update(is_approved=True)} account(s) approved.")


@admin.register(PatientProfile)
class PatientProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "blood_group", "emergency_contact_name", "emergency_contact_phone")
    search_fields = ("user__username", "user__first_name", "user__last_name")
