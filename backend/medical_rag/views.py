import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods, require_POST

from . import service
from .retriever import DISCLAIMER

SUGGESTIONS = [
    "What is malaria?",
    "What causes high blood pressure?",
    "What is a normal heart rate?",
    "What does low oxygen saturation mean?",
    "What are the symptoms of diabetes?",
    "When should I seek emergency medical assistance?",
]


def assistant_page(request):
    return render(request, "assistant/assistant.html", {
        "disclaimer": DISCLAIMER, "suggestions": SUGGESTIONS, "history_days": service.history_days(),
        "is_staff_user": request.user.is_authenticated and (request.user.is_superuser or
                                                             request.user.role in ("operator", "admin", "hospital", "paramedic"))})


def _body(request):
    try:
        return json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return request.POST.dict()


@require_POST
def ask(request):
    """POST {"question", "conversation_id"?, "save_history"?, "include_vitals"?, "emergency_ref"?, "history"?}
    -> grounded answer with sources (CSRF-protected)."""
    d = _body(request)
    try:
        result = service.ask(request.user, d.get("question", ""), conversation_id=d.get("conversation_id"),
                             save_history=d.get("save_history", True) not in (False, "false", "0"),
                             include_vitals=d.get("include_vitals") in (True, "true", "1"),
                             emergency_ref=(d.get("emergency_ref") or "").strip() or None,
                             history=d.get("history") if isinstance(d.get("history"), list) else None, request=request)
    except service.AssistantError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=403)
    result["library"] = related_entries(d.get("question", ""), request.user)
    return JsonResponse(result)


@login_required
@require_http_methods(["GET", "DELETE"])
def history(request, pk=None):
    """GET list / one conversation; DELETE one (pk) or all of the user's conversations."""
    try:
        if request.method == "DELETE":
            return JsonResponse({"deleted": service.delete_conversations(request.user, pk)})
        if pk:
            return JsonResponse(service.conversation_detail(request.user, pk))
        return JsonResponse({"conversations": service.conversations(request.user)})
    except service.AssistantError as e:
        return JsonResponse({"error": str(e)}, status=404)


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


@require_POST
def feedback(request):
    """POST {response_id, rating, suggestion?, message_id?, comment?} (CSRF-protected). Stores labels only."""
    try:
        fb = service.feedback(request.user, _body(request))
    except service.AssistantError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    return JsonResponse({"ok": True, "duplicate": fb is None}, status=201 if fb else 200)
