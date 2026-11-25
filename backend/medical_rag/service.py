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

from django.conf import settings
from django.utils import timezone

from . import retriever
from .models import ChatMessage, Conversation


class AssistantError(Exception):
    pass


def history_days():
    return int(getattr(settings, "ASSISTANT_HISTORY_DAYS", 30))


def _vitals_for_patient(user):
    from patient_monitoring.models import VitalSignReading
    from patient_monitoring.services import vitals_text
    r = VitalSignReading.objects.filter(patient=user).order_by("-measured_at").first()
    return vitals_text(r) if r else ""


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

    result = retriever.answer(question, history=history, vitals_text=vitals,
                              hotline=getattr(settings, "EMERGENCY_HOTLINE", "912"))
    result["used_vitals"] = bool(vitals)
    if user.is_authenticated and save_history and result.get("ok"):
        if conv is None:
            conv = Conversation.objects.create(user=user, title=question[:120])
        ChatMessage.objects.create(conversation=conv, role="user", text=question[:2000])
        ChatMessage.objects.create(conversation=conv, role="assistant", text=result["answer"], meta={
            k: result.get(k) for k in ("mode", "emergency", "sources", "follow_up", "safety", "red_flags", "used_vitals")})
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
