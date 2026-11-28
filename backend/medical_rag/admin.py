from django.contrib import admin
from django.utils import timezone

from .models import ChatMessage, Conversation, Disease, KnowledgeChunk, KnowledgeDocument


@admin.register(Disease)
class DiseaseAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "review_status", "reviewed_by", "reviewed_at", "source_name")
    list_filter = ("category", "review_status")
    search_fields = ("name", "description", "symptoms")
    readonly_fields = ("reviewed_by", "reviewed_at", "updated_at")
    prepopulated_fields = {"slug": ("name",)}


class ChunkInline(admin.TabularInline):
    model = KnowledgeChunk
    extra = 0
    readonly_fields = ("order", "heading", "text")
    can_delete = False


@admin.register(KnowledgeDocument)
class KnowledgeDocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "kind", "review_status", "summary_version", "source_name",
                    "source_published", "source_check_status", "origin")
    list_filter = ("review_status", "category", "kind", "origin", "emergency")
    search_fields = ("title", "keywords", "content", "source_name")
    readonly_fields = ("content_hash", "summary_version", "reviewed_by", "reviewed_at", "ingested_at", "updated_at",
                       "source_checked_at", "source_check_status")
    inlines = [ChunkInline]
    actions = ["approve", "retire"]

    @admin.action(description="Mark as clinically reviewed")
    def approve(self, request, qs):
        qs.update(review_status="approved", reviewed_by=request.user, reviewed_at=timezone.now())
        from . import retriever
        retriever.invalidate()

    @admin.action(description="Retire (stop using in answers)")
    def retire(self, request, qs):
        qs.update(review_status="retired")
        from . import retriever
        retriever.invalidate()


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    """Metadata only - message text is private to the user and not shown here."""
    list_display = ("id", "user", "created_at", "updated_at")
    readonly_fields = ("user", "created_at", "updated_at")
    exclude = ("title",)

    def has_add_permission(self, request):
        return False


from .models import AssistantFeedback, ModelVersion  # noqa: E402


@admin.register(AssistantFeedback)
class AssistantFeedbackAdmin(admin.ModelAdmin):
    """Human review queue: operators/clinicians mark answers correct / incorrect / unsafe."""
    list_display = ("created_at", "rating", "intent", "mode", "suggestion", "emergency", "review", "reviewed_by")
    list_filter = ("rating", "review", "intent", "mode", "emergency")
    readonly_fields = ("response_id", "user", "role", "message", "rating", "intent", "mode", "suggestion",
                       "suggestions_shown", "emergency", "comment", "created_at", "reviewed_by", "reviewed_at")
    fields = readonly_fields[:11] + ("review",) + readonly_fields[11:]

    def save_model(self, request, obj, form, change):
        if "review" in form.changed_data:
            obj.reviewed_by, obj.reviewed_at = request.user, timezone.now()
        super().save_model(request, obj, form, change)


@admin.register(ModelVersion)
class ModelVersionAdmin(admin.ModelAdmin):
    """Read-only: models are approved / rolled back with management commands so the evaluation gate always runs."""
    list_display = ("kind", "version", "status", "approved_by", "approved_at", "created_at")
    list_filter = ("kind", "status")
    readonly_fields = [f.name for f in ModelVersion._meta.fields]

    def has_add_permission(self, request):
        return False
