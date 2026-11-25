"""
Retrieval-augmented medical information assistant.

1. Retrieve: TF-IDF (scikit-learn, runs locally - no external API) ranks the curated
   documents in knowledge_base.py against the question.
2. Answer: by default the answer is EXTRACTIVE - it quotes the retrieved trusted text
   with its source, so it cannot invent facts. If ANTHROPIC_API_KEY is set, a language
   model may rephrase ONLY the retrieved text (optional; failures fall back to extractive).

The assistant never diagnoses or prescribes, and red-flag phrases always produce an
"request emergency help now" message first.
"""
import logging
import os

from .knowledge_base import DOCUMENTS, RED_FLAGS

log = logging.getLogger(__name__)
MIN_SCORE = 0.08
DISCLAIMER = ("This is general health information, not a diagnosis or treatment plan. It does not "
              "replace a doctor or emergency services.")

_index = None


def _build():
    global _index
    from sklearn.feature_extraction.text import TfidfVectorizer
    corpus = [f"{d['title']} {d['keywords']} {d['keywords']} {d['text']}" for d in DOCUMENTS]
    vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True)
    _index = (vec, vec.fit_transform(corpus))
    return _index


def retrieve(question, k=3):
    from sklearn.metrics.pairwise import cosine_similarity
    vec, matrix = _index or _build()
    scores = cosine_similarity(vec.transform([question]), matrix)[0]
    ranked = sorted(zip(scores, DOCUMENTS), key=lambda t: t[0], reverse=True)
    return [(float(s), d) for s, d in ranked[:k] if s >= MIN_SCORE]


def red_flags(question):
    q = question.lower()
    return [f for f in RED_FLAGS if f in q]


def _llm_rephrase(question, docs):
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        import requests
        context = "\n\n".join(f"[{d['title']}] {d['text']}" for _, d in docs)
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=20, headers={
            "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": os.getenv("RAG_LLM_MODEL", "claude-haiku-5-5"), "max_tokens": 400,
                  "system": "Answer ONLY using the provided context. Plain language, under 120 words. "
                            "Never diagnose or prescribe. If the context does not answer, say so.",
                  "messages": [{"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"}]})
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json().get("content", [])).strip() or None
    except Exception:
        log.exception("LLM rephrasing failed - using extractive answer")
        return None


def answer(question):
    question = (question or "").strip()[:500]
    if not question:
        return {"ok": False, "error": "Please type a question."}
    flags = red_flags(question)
    try:
        docs = retrieve(question)
    except Exception:
        log.exception("Retriever unavailable")
        return {"ok": False, "emergency": bool(flags), "disclaimer": DISCLAIMER,
                "error": "The information assistant is temporarily unavailable. "
                         "If this is an emergency, use the Emergency button now."}
    emergency = bool(flags) or any(d["emergency"] for _, d in docs[:1])
    if not docs:
        text, mode = ("I could not find trusted information on that in my knowledge base. "
                      "Please ask a healthcare professional."), "none"
    else:
        text = _llm_rephrase(question, docs)
        mode = "llm_grounded" if text else "extractive"
        text = text or docs[0][1]["text"]
    return {
        "ok": True, "answer": text, "mode": mode, "emergency": emergency, "red_flags": flags,
        "disclaimer": DISCLAIMER,
        "sources": [{"title": d["title"], "source": d["source"][0], "url": d["source"][1],
                     "score": round(s, 3), "excerpt": d["text"]} for s, d in docs],
    }
