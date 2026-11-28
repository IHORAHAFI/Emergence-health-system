"""One entry point for the web chat and the mobile API: privacy, history and patient-data rules live here.

Privacy rules
* Anonymous users: nothing is stored.
* Logged-in users: the conversation is stored only if save_history is True (default on, switchable in the UI);
  only the owner can read or delete it; it is purged after ASSISTANT_HISTORY_DAYS.
* Vital signs are added to the model context only when
  - a patient ticks "use my latest vital signs" (their own readings), or
  - a staff member passes an emergency reference they are already authorised to view
    (patient_monitoring.services.can_view); every such access is written to the audit log.
"""
from datetime import timedelta

import uuid

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from . import retriever
from .models import ChatMessage, Conversation


class AssistantError(Exception):
    pass


def history_days():
    return int(getattr(settings, "ASSISTANT_HISTORY_DAYS", 30))


def _vitals_for_patient(user):
    """Only called when the patient ticked "use my latest vital signs". Camera heart rate, simulated temperature and
    simulated blood pressure are given with their origin (home_vitals.ai_context); a real device / clinician reading
    from an emergency or test is added separately."""
    from patient_monitoring import home_vitals
    from patient_monitoring.models import VitalSignReading
    from patient_monitoring.services import vitals_text
    parts = [home_vitals.ai_context(user)]
    r = (VitalSignReading.objects.filter(patient=user)
         .exclude(device__manufacturer=home_vitals.MANUFACTURER)  # already described by ai_context above
         .order_by("-measured_at").first())
    if r:
        parts.append("Latest recorded device reading: " + vitals_text(r))
    return "\n".join(parts)


def _vitals_for_emergency(user, ref, request=None):
    from emergency.models import EmergencyRequest
    from patient_monitoring import services
    em = EmergencyRequest.objects.select_related("assigned_ambulance").filter(reference=ref).first()
    if em is None or not services.can_view(user, em):
        raise AssistantError("You are not authorised to use this patient's records.")
    services.audit(user, em, "assistant_vitals_context", "vital signs shared with AI assistant", request)
    r = em.vitals.first()
    return services.vitals_text(r) if r else ""


def conversation_for(user, conversation_id):
    if not user.is_authenticated or not conversation_id:
        return None
    c = Conversation.objects.filter(pk=conversation_id, user=user).first()
    if c is None:
        raise AssistantError("Conversation not found.")
    return c


def ask(user, question, conversation_id=None, save_history=True, include_vitals=False, emergency_ref=None,
        history=None, request=None):
    """history: client-held previous turns (used when nothing is stored, e.g. anonymous users)."""
    conv = conversation_for(user, conversation_id)
    if conv is not None:
        history = [{"role": m.role, "text": m.text} for m in conv.messages.order_by("-created_at", "-id")[:6]][::-1]
    else:
        history = [{"role": h.get("role"), "text": str(h.get("text", ""))[:2000]}
                   for h in (history or [])[-6:] if h.get("role") in ("user", "assistant")]

    vitals = ""
    if emergency_ref:
        if not user.is_authenticated:
            raise AssistantError("Log in to use patient records.")
        vitals = _vitals_for_emergency(user, emergency_ref, request)
    elif include_vitals and user.is_authenticated and user.role == "patient":
        vitals = _vitals_for_patient(user)

    from . import recommendations
    recent = []
    if conv is not None:
        last = conv.messages.filter(role="assistant").order_by("-created_at", "-id").first()
        recent = [x.get("id") for x in ((last.meta or {}).get("suggestions") or [])] if last else []
    result = retriever.answer(question, history=history, vitals_text=vitals,
                              hotline=getattr(settings, "EMERGENCY_HOTLINE", "912"),
                              role=recommendations.role_of(user), recently_suggested=recent)
    result["used_vitals"] = bool(vitals)
    result["response_id"] = str(uuid.uuid4())  # lets the user rate this answer (see feedback())
    if user.is_authenticated and save_history and result.get("ok"):
        if conv is None:
            conv = Conversation.objects.create(user=user, title=question[:120])
        ChatMessage.objects.create(conversation=conv, role="user", text=question[:2000])
        msg = ChatMessage.objects.create(conversation=conv, role="assistant", text=result["answer"], meta={
            k: result.get(k) for k in ("mode", "emergency", "sources", "follow_up", "safety", "red_flags", "used_vitals",
                                       "intent", "suggestions", "response_id")})
        result["message_id"] = msg.pk
        conv.save(update_fields=["updated_at"])
    result["conversation_id"] = conv.pk if conv else None
    return result


def conversations(user):
    return [{"id": c.pk, "title": c.title, "updated_at": c.updated_at.isoformat()}
            for c in Conversation.objects.filter(user=user)[:50]]


def conversation_detail(user, conversation_id):
    c = conversation_for(user, conversation_id)
    return {"id": c.pk, "title": c.title, "messages": [
        {"role": m.role, "text": m.text, "meta": m.meta, "at": m.created_at.isoformat()} for m in c.messages.all()]}


def delete_conversations(user, conversation_id=None):
    qs = Conversation.objects.filter(user=user)
    if conversation_id:
        qs = qs.filter(pk=conversation_id)
    return qs.delete()[0]


def purge_old(days=None):
    cutoff = timezone.now() - timedelta(days=days or history_days())
    return Conversation.objects.filter(updated_at__lt=cutoff).delete()[0]


# ------------------------------------------------------------------ feedback (no automatic learning)
FEEDBACK_RATINGS = ("helpful", "unhelpful", "unclear", "suggestion")


def feedback(user, data):
    """Store one explicit rating. The answer it refers to is identified by response_id (and message_id when the
    answer was saved in the user's own history). Nothing here changes the assistant: ratings feed the human review
    queue and the offline, admin-approved recommendation policy only."""
    from .models import AssistantFeedback
    from .recommendations import CATALOG, role_of
    rid = str(data.get("response_id") or "")
    try:
        uuid.UUID(rid)
    except ValueError:
        raise AssistantError("Unknown answer.")
    rating = data.get("rating")
    if rating not in FEEDBACK_RATINGS:
        raise AssistantError("Rating must be one of: " + ", ".join(FEEDBACK_RATINGS))
    suggestion = str(data.get("suggestion") or "")
    if rating == "suggestion" and suggestion not in CATALOG:
        raise AssistantError("Unknown suggestion.")
    msg = None
    if data.get("message_id") and user.is_authenticated:
        msg = ChatMessage.objects.filter(pk=data["message_id"], conversation__user=user, role="assistant").first()
        if msg is None or (msg.meta or {}).get("response_id") != rid:
            raise AssistantError("That answer is not in your history.")
    meta = (msg.meta if msg else {}) or {}
    shown = [s.get("id") for s in (meta.get("suggestions") or [])] or [
        x for x in (data.get("suggestions_shown") or []) if x in CATALOG][:6]
    intent = (meta.get("intent") or {}).get("intent") or str(data.get("intent") or "")[:30]
    try:
        with transaction.atomic():  # savepoint: a duplicate must not break the caller's transaction
            fb = AssistantFeedback.objects.create(
                response_id=rid, user=user if user.is_authenticated else None, role=role_of(user), message=msg,
                rating=rating, intent=intent, mode=str(meta.get("mode") or data.get("mode") or "")[:20],
                suggestion=suggestion if rating == "suggestion" else "", suggestions_shown=shown,
                emergency=bool(meta.get("emergency") or data.get("emergency")),
                comment=str(data.get("comment") or "")[:300])
    except IntegrityError:
        return None  # the same choice was already recorded: idempotent
    return fb
