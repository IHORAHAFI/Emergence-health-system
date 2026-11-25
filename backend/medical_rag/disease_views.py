"""Medical information library (diseases and conditions).

Patients and the public only see entries a healthcare professional or administrator has
reviewed. Clinicians/admins also see pending entries (clearly marked) so they can review them.
"""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Disease


def can_review(user):
    """Administrators, or approved healthcare professionals."""
    if not user.is_authenticated:
        return False
    return user.is_superuser or user.role == "admin" or (user.role == "hospital" and user.is_approved)


def _visible(user):
    qs = Disease.objects.all()
    return qs if can_review(user) else qs.filter(review_status=Disease.Review.APPROVED)


def disease_list(request):
    qs = _visible(request.user)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(description__icontains=q) | Q(symptoms__icontains=q)
                       | Q(causes__icontains=q))
    cat = request.GET.get("category", "")
    if cat:
        qs = qs.filter(category=cat)
    if request.GET.get("review") in ("pending", "approved") and can_review(request.user):
        qs = qs.filter(review_status=request.GET["review"])
    params = request.GET.copy()
    params.pop("page", None)
    base = _visible(request.user)
    categories = [(c, label, base.filter(category=c).count()) for c, label in Disease.Category.choices]
    return render(request, "medical/disease_list.html", {
        "page": Paginator(qs, 12).get_page(request.GET.get("page")), "q": q, "category": cat,
        "categories": categories, "params": params.urlencode(), "reviewer": can_review(request.user),
        "pending_count": Disease.objects.filter(review_status="pending").count(),
        "hidden_count": 0 if can_review(request.user) else Disease.objects.filter(review_status="pending").count()})


def disease_detail(request, slug):
    d = get_object_or_404(_visible(request.user), slug=slug)
    return render(request, "medical/disease_detail.html", {
        "d": d, "reviewer": can_review(request.user),
        "related": _visible(request.user).filter(category=d.category).exclude(pk=d.pk)[:5]})


@login_required
@require_POST
def disease_review(request, slug):
    if not can_review(request.user):
        return HttpResponseForbidden("Only healthcare professionals and administrators can review entries.")
    d = get_object_or_404(Disease, slug=slug)
    if request.POST.get("decision") == "approve":
        d.review_status, d.reviewed_by, d.reviewed_at = Disease.Review.APPROVED, request.user, timezone.now()
        messages.success(request, f"'{d.name}' marked as clinically reviewed - now visible to patients.")
    else:
        d.review_status, d.reviewed_by, d.reviewed_at = Disease.Review.PENDING, None, None
        messages.info(request, f"'{d.name}' returned to pending review - hidden from patients.")
    d.save()
    return redirect("disease_detail", slug=d.slug)


class DiseaseForm(forms.ModelForm):
    class Meta:
        model = Disease
        fields = ("name", "category", "description", "causes", "symptoms", "risk_factors", "diagnosis",
                  "treatment", "prevention", "warning_signs", "source_name", "source_url")
        widgets = {f: forms.Textarea(attrs={"rows": 3}) for f in (
            "description", "causes", "symptoms", "risk_factors", "diagnosis", "treatment", "prevention",
            "warning_signs")}
        labels = {"source_name": "Source (organisation and title)", "source_url": "Source link"}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        for f in self.fields.values():
            f.widget.attrs.setdefault("class", "form-select" if isinstance(f.widget, forms.Select) else "form-control")


@login_required
def disease_edit(request, slug=None):
    if not can_review(request.user):
        return HttpResponseForbidden("Only healthcare professionals and administrators can edit entries.")
    d = get_object_or_404(Disease, slug=slug) if slug else None
    form = DiseaseForm(request.POST or None, instance=d)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        # Any content change needs a fresh clinical review.
        obj.review_status, obj.reviewed_by, obj.reviewed_at = Disease.Review.PENDING, None, None
        obj.save()
        messages.success(request, f"'{obj.name}' saved. It is pending review until approved.")
        return redirect("disease_detail", slug=obj.slug)
    return render(request, "medical/disease_form.html", {"form": form, "d": d})
