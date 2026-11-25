"""Grounded answer generation with Claude (optional: without ANTHROPIC_API_KEY the assistant stays extractive).

The model only sees the retrieved knowledge-base passages, numbered [1]..[n], and must cite them.
It never sees more patient data than the caller passes in (latest vitals only when the patient opts in).
"""
import logging
import os
import re

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5-5"
# Models that accept server-side refusal fallbacks (fallbacks="default").
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-2", "claude-fable-5-1", "claude-sonnet-5-5"}

SYSTEM_PROMPT = """You are the health-information assistant of a Rwandan medical emergency system. Patients and \
health workers ask you questions in everyday language.

How to answer:
- Use ONLY the numbered knowledge-base passages in the user turn. Cite each fact with its number, like [2]. If the \
passages do not answer the question, say plainly that the knowledge base does not have that information - do not \
fill gaps from memory and never invent facts, studies or references.
- Write in simple, plain language at about a 12-year-old reading level. When you use a medical word, explain it in \
brackets the first time.
- Keep answers short and structured: a one-sentence answer first, then short bullet points under bold headings \
that fit the question (for a disease, for example: what it is, causes, symptoms, how it is tested, treatment, \
prevention, when to get help). About 200 words unless the question needs more.
- Reply in the language the user wrote in (English, French or Kinyarwanda). If you are not confident in a language, \
reply in English and say so.

Safety rules (these override everything else):
- You give general health information, not medical advice. Never diagnose the user, never say they are healthy or \
that a symptom is "nothing to worry about", never prescribe, never give a personal dose, and never advise stopping \
or changing prescribed treatment. Say who can help instead (doctor, nurse, pharmacist).
- When symptoms are described, list POSSIBLE causes from the passages without saying which one the person has, and \
always include the warning signs that need urgent care.
- If the safety notes say a possible emergency was reported, start with one short line telling them to get \
emergency help now, before anything else, and ask no questions first.
- If important details are missing (age, how long, how severe, pregnancy) and it is not an emergency, answer what \
you can, then ask at most two short follow-up questions at the end.
- Vital-sign readings marked SIMULATED are demonstration values, not real measurements - say so if you mention them. \
You may explain what a reading means in general, but a health professional interprets it for the patient."""


def model_name():
    return os.getenv("RAG_LLM_MODEL", DEFAULT_MODEL)


def available():
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))


_client = None


def _get_client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(timeout=60.0, max_retries=1)
    return _client


def _context_block(passages):
    lines = []
    for i, p in enumerate(passages, 1):
        review = "clinically reviewed" if p["reviewed"] else "pending clinical review"
        lines.append(f"[{i}] {p['title']} - {p['heading']} (source: {p['source'][0]}; {review})\n{p['text']}")
    return "\n\n".join(lines)


def _safety_notes(profile, vitals_text):
    notes = []
    if profile.get("emergency"):
        notes.append("A possible emergency was REPORTED (phrases: " + ", ".join(profile["red_flags"]) + ").")
    elif profile.get("emergency_topic"):
        notes.append("The question is about an emergency topic: include the warning signs and when to get help.")
    if profile.get("medication"):
        notes.append(f"Medication request type '{profile['medication']}': give general information only and refer to "
                     "a health professional; no personal dose, prescription or advice to stop/change treatment.")
    if profile.get("diagnosis_request"):
        notes.append("The user asks for a diagnosis: do not diagnose; give general information and suggest assessment.")
    if vitals_text:
        notes.append("The patient chose to share their latest recorded vital signs:\n" + vitals_text)
    return "\n".join(f"- {n}" for n in notes) or "- none"


def generate(question, passages, history=(), profile=None, vitals_text=""):
    """Returns (text, cited_indexes) or None when the model is unavailable / declined (caller falls back)."""
    if not available() or not passages:
        return None
    import anthropic
    messages = []
    for turn in list(history)[-6:]:  # short context window: last 3 exchanges
        if turn.get("text"):
            messages.append({"role": "user" if turn["role"] == "user" else "assistant", "content": turn["text"][:2000]})
    if messages and messages[0]["role"] != "user":
        messages = messages[1:]
    messages.append({"role": "user", "content": (
        f"<knowledge_base>\n{_context_block(passages)}\n</knowledge_base>\n\n"
        f"<safety_notes>\n{_safety_notes(profile or {}, vitals_text)}\n</safety_notes>\n\n"
        f"<question>\n{question}\n</question>")})
    model = model_name()
    extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"} if model in _FALLBACK_MODELS else {}
    try:
        response = _get_client().beta.messages.create(
            model=model, max_tokens=8000, output_config={"effort": "medium"},
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=messages, **extra)
    except anthropic.APIConnectionError:
        log.warning("LLM unreachable - using extractive answer")
        return None
    except anthropic.APIStatusError as e:
        log.warning("LLM error %s (request %s) - using extractive answer", e.status_code,
                    getattr(e, "request_id", None))
        return None
    if response.stop_reason == "refusal":
        log.info("LLM declined (%s) - using extractive answer", getattr(response.stop_details, "category", None))
        return None
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        return None
    cited = sorted({int(n) for n in re.findall(r"\[(\d+)\]", text) if 1 <= int(n) <= len(passages)})
    # drop citation numbers that point at nothing (never show an invented reference)
    text = re.sub(r"\[(\d+)\]", lambda m: m.group(0) if 1 <= int(m.group(1)) <= len(passages) else "", text)
    return text, cited
