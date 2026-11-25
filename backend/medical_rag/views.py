import json

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from .retriever import DISCLAIMER, answer


def assistant_page(request):
    return render(request, "assistant/assistant.html", {
        "disclaimer": DISCLAIMER,
        "suggestions": ["What are the signs of a stroke?", "How do I treat a burn?",
                        "My child has diarrhoea", "What should I do if someone is choking?"]})


@require_POST
def ask(request):
    """POST {"question": "..."} -> grounded answer with sources (CSRF-protected)."""
    try:
        q = json.loads(request.body or b"{}").get("question", "")
    except json.JSONDecodeError:
        q = request.POST.get("question", "")
    result = answer(q)
    result["library"] = related_entries(q, request.user)
    return JsonResponse(result)


def related_entries(question, user, limit=3):
    """Medical-library entries whose name or symptoms match words in the question."""
    from django.urls import reverse
    from .disease_views import _visible
    words = {w.strip("?.,!").lower() for w in str(question).split() if len(w.strip("?.,!")) > 3}
    if not words:
        return []
    scored = []
    for d in _visible(user).only("name", "slug", "symptoms", "review_status"):
        text = f"{d.name} {d.symptoms}".lower()
        s = sum(w in text for w in words) + 2 * sum(w in d.name.lower() for w in words)
        if s:
            scored.append((s, d))
    scored.sort(key=lambda x: -x[0])
    return [{"name": d.name, "url": reverse("disease_detail", args=[d.slug]), "reviewed": d.is_reviewed}
            for _, d in scored[:limit]]
