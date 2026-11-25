from django.contrib import admin

from .models import Disease


@admin.register(Disease)
class DiseaseAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "review_status", "reviewed_by", "reviewed_at", "source_name")
    list_filter = ("category", "review_status")
    search_fields = ("name", "description", "symptoms")
    readonly_fields = ("reviewed_by", "reviewed_at", "updated_at")
    prepopulated_fields = {"slug": ("name",)}
