from django.conf import settings
from django.db import models
from django.utils.text import slugify


class Disease(models.Model):
    """Medical information entry. Entries are summaries of a cited public source and are
    only shown to patients after a healthcare professional has marked them as reviewed."""

    class Category(models.TextChoices):
        INFECTIOUS = "infectious", "Infectious diseases"
        CARDIOVASCULAR = "cardiovascular", "Cardiovascular diseases"
        RESPIRATORY = "respiratory", "Respiratory diseases"
        NEUROLOGICAL = "neurological", "Neurological diseases"
        INJURIES = "injuries", "Injuries and accidents"
        CHILDHOOD = "childhood", "Childhood diseases"
        METABOLIC = "metabolic", "Metabolic and chronic diseases"

    class Review(models.TextChoices):
        PENDING = "pending", "Pending clinical review"
        APPROVED = "approved", "Clinically reviewed"

    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    category = models.CharField(max_length=20, choices=Category.choices)
    description = models.TextField()
    causes = models.TextField(blank=True)
    symptoms = models.TextField(blank=True, help_text="Signs and symptoms, one per line")
    risk_factors = models.TextField(blank=True, help_text="One per line")
    diagnosis = models.TextField(blank=True)
    treatment = models.TextField(blank=True, help_text="General information only - never individual instructions")
    prevention = models.TextField(blank=True, help_text="One per line")
    warning_signs = models.TextField(blank=True, help_text="Emergency warning signs, one per line")
    source_name = models.CharField(max_length=120)
    source_url = models.URLField()
    review_status = models.CharField(max_length=10, choices=Review.choices, default=Review.PENDING)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:140]
        super().save(*args, **kwargs)

    @property
    def is_reviewed(self):
        return self.review_status == self.Review.APPROVED

    @staticmethod
    def lines(text):
        return [l.strip(" -•\t") for l in (text or "").splitlines() if l.strip(" -•\t")]

    def symptom_list(self):
        return self.lines(self.symptoms)

    def risk_list(self):
        return self.lines(self.risk_factors)

    def prevention_list(self):
        return self.lines(self.prevention)

    def warning_list(self):
        return self.lines(self.warning_signs)


# ------------------------------------------------------------------ RAG knowledge base
class KnowledgeDocument(models.Model):
    """One trusted-source document indexed by the assistant. Content is a plain-language summary
    written for this project; provenance (source, publication date, version) is recorded per document."""

    class Origin(models.TextChoices):
        CORPUS = "corpus", "Corpus file (medical_rag/corpus)"
        BUILTIN = "builtin", "Built-in first-aid notes"
        LIBRARY = "library", "Disease library entry"
        ADMIN = "admin", "Added in admin"

    class Review(models.TextChoices):
        PENDING = "pending", "Pending clinical review"
        APPROVED = "approved", "Clinically reviewed"
        RETIRED = "retired", "Retired (not used in answers)"

    slug = models.SlugField(max_length=120, unique=True)
    title = models.CharField(max_length=200)
    category = models.CharField(max_length=40, blank=True)
    kind = models.CharField(max_length=20, blank=True)
    keywords = models.TextField(blank=True, help_text="Extra search words, including everyday terms")
    content = models.TextField(help_text="Markdown; each '## ' section becomes one searchable chunk")
    emergency = models.BooleanField(default=False, help_text="The topic itself is an emergency")
    source_name = models.CharField(max_length=200)
    source_url = models.URLField(max_length=500)
    source_published = models.DateField(null=True, blank=True, help_text="Publication / review date shown on the source")
    source_version = models.CharField(max_length=60, blank=True)
    origin = models.CharField(max_length=10, choices=Origin.choices, default=Origin.ADMIN)
    content_hash = models.CharField(max_length=64, editable=False)
    summary_version = models.PositiveIntegerField(default=1, editable=False,
                                                  help_text="Increments every time the content changes")
    review_status = models.CharField(max_length=10, choices=Review.choices, default=Review.PENDING)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    source_checked_at = models.DateTimeField(null=True, blank=True)
    source_check_status = models.CharField(max_length=40, blank=True)
    ingested_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return self.title

    @property
    def is_reviewed(self):
        return self.review_status == self.Review.APPROVED


class KnowledgeChunk(models.Model):
    document = models.ForeignKey(KnowledgeDocument, on_delete=models.CASCADE, related_name="chunks")
    order = models.PositiveSmallIntegerField()
    heading = models.CharField(max_length=200, blank=True)
    text = models.TextField()

    class Meta:
        ordering = ["document_id", "order"]
        constraints = [models.UniqueConstraint(fields=["document", "order"], name="unique_chunk_order")]

    def __str__(self):
        return f"{self.document.title} / {self.heading}"


# ------------------------------------------------------------------ conversations (opt-in, owner-only)
class Conversation(models.Model):
    """Saved only for logged-in users who keep history on; the owner can delete it at any time.
    Old conversations are purged after ASSISTANT_HISTORY_DAYS (see purge_assistant_history)."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="assistant_conversations")
    title = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.user} - {self.title}"


class ChatMessage(models.Model):
    class Role(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=10, choices=Role.choices)
    text = models.TextField()
    meta = models.JSONField(default=dict, blank=True, help_text="Sources, safety flags, mode (assistant messages)")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]


class ModelVersion(models.Model):
    """A trained AI artefact (intent classifier, recommendation policy). Nothing trained is used until an authorised
    person approves it: candidate -> (evaluation passes) -> approved/active. Only one ACTIVE version per kind;
    the previous active one becomes RETIRED and can be restored (rollback)."""

    class Kind(models.TextChoices):
        INTENT = "intent", "Intent classifier"
        RECOMMENDATION = "recommendation", "Recommendation policy"

    class Status(models.TextChoices):
        CANDIDATE = "candidate", "Candidate (not used)"
        REJECTED = "rejected", "Rejected"
        ACTIVE = "active", "Active"
        RETIRED = "retired", "Retired"

    kind = models.CharField(max_length=20, choices=Kind.choices)
    version = models.CharField(max_length=40)
    artifact = models.CharField(max_length=255, help_text="File under ai_training/models/")
    sha256 = models.CharField(max_length=64)
    metrics = models.JSONField(default=dict, blank=True)
    gate = models.JSONField(default=dict, blank=True, help_text="Automatic evaluation gate results")
    data_info = models.JSONField(default=dict, blank=True, help_text="Datasets, licences, sizes, checksums")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.CANDIDATE)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = [("kind", "version")]

    def __str__(self):
        return f"{self.kind} {self.version} [{self.status}]"


class AssistantFeedback(models.Model):
    """Explicit feedback on one assistant answer or suggestion. Stores labels, not the conversation: the question
    text is kept only when the answer was saved in the user's own history (linked message, deleted with it).
    Feedback NEVER changes safety behaviour; it only feeds the offline, human-approved recommendation policy and
    the review queue."""

    class Rating(models.TextChoices):
        HELPFUL = "helpful", "Helpful"
        UNHELPFUL = "unhelpful", "Not helpful"
        UNCLEAR = "unclear", "Needs clarification"
        SUGGESTION = "suggestion", "Suggestion used"

    class Review(models.TextChoices):
        PENDING = "pending", "Not reviewed"
        CORRECT = "correct", "Answer correct"
        INCORRECT = "incorrect", "Answer incorrect"
        UNSAFE = "unsafe", "Unsafe - fix knowledge/rules"

    response_id = models.CharField(max_length=36, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                             related_name="+")
    role = models.CharField(max_length=20, blank=True)
    message = models.ForeignKey(ChatMessage, null=True, blank=True, on_delete=models.CASCADE, related_name="feedback")
    rating = models.CharField(max_length=10, choices=Rating.choices)
    intent = models.CharField(max_length=30, blank=True)
    mode = models.CharField(max_length=20, blank=True)
    suggestion = models.CharField(max_length=40, blank=True, help_text="Suggestion id when rating=suggestion")
    suggestions_shown = models.JSONField(default=list, blank=True)
    emergency = models.BooleanField(default=False)
    comment = models.CharField(max_length=300, blank=True)
    review = models.CharField(max_length=10, choices=Review.choices, default=Review.PENDING)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["response_id", "user", "rating", "suggestion"],
                                               name="one_feedback_per_choice")]
