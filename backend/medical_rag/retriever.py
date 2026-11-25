"""
Retrieval-augmented medical information assistant.

Pipeline for every question:
1. Safety check (safety.assess) - emergency phrases, medication/diagnosis requests, language. Runs first, so an
   emergency warning never depends on search quality.
2. Query expansion - everyday words are mapped to medical terms ("sugar" -> diabetes, "high blood pressure" ->
   hypertension) and follow-up questions borrow the topic of the previous turn.
3. Vector search - each knowledge chunk is a TF-IDF vector (word 1-2-grams + character 3-5-grams for spelling
   mistakes); cosine similarity ranks chunks. Runs locally, no external service. The index is rebuilt when
   documents change.
4. Answer - with ANTHROPIC_API_KEY set, Claude writes a cited answer from the retrieved passages only (llm.py).
   Otherwise (or if the model is unreachable) the answer is EXTRACTIVE: it quotes the trusted text itself,
   so it cannot invent facts.

Documents come from KnowledgeDocument / KnowledgeChunk (see ingest.py). If the table is empty it is filled
automatically from the corpus on first use.
"""
import logging
import re
import threading

from . import llm, safety

log = logging.getLogger(__name__)
MIN_SCORE = 0.08
DISCLAIMER = ("This is general health information, not a diagnosis or treatment plan. It does not "
              "replace a doctor or emergency services.")

# Everyday words -> medical terms, so people do not need to know the vocabulary.
LAY_TERMS = {
    "high blood pressure": "hypertension", "low blood pressure": "hypotension", "sugar": "diabetes glucose",
    "sugar disease": "diabetes", "heart attack": "myocardial infarction coronary heart attack",
    "short of breath": "shortness of breath difficulty breathing", "can't breathe": "difficulty breathing",
    "belly": "abdominal", "tummy": "abdominal", "stomach ache": "abdominal pain", "runny tummy": "diarrhoea",
    "diarrhea": "diarrhoea", "throwing up": "vomiting", "puking": "vomiting", "fits": "seizure convulsion", "low blood": "anaemia", "anemia": "anaemia", "pee": "urine urination", "wee": "urine",
    "yellow eyes": "jaundice", "tired": "fatigue tired", "pulse": "heart rate pulse", "oxygen level": "oxygen saturation SpO2",
    "spo2": "oxygen saturation", "temp": "temperature", "bp": "blood pressure", "sugar level": "blood glucose",
    "fever": "fever temperature", "malaria": "malaria plasmodium", "aids": "hiv aids", "tb": "tuberculosis",
    "flu": "influenza viral infection", "mental": "mental health", "sad": "depression low mood",
    "worried": "anxiety", "panic": "anxiety panic attack", "pregnant": "pregnancy", "baby": "child infant",
    "kid": "child", "paracetamol": "pain reliever analgesic medicine", "ibuprofen": "pain reliever anti-inflammatory",
    "painkiller": "pain reliever analgesic", "pills": "medicine", "tablets": "medicine", "drugs": "medicine",
    "side effect": "side effects", "jab": "vaccine", "shot": "vaccine injection",
    "not breathing": "unresponsive not breathing cpr cardiac arrest", "unconscious": "unresponsive unconscious",
    "collapsed": "unresponsive collapse", "choking": "choking airway",
    # French
    "paludisme": "malaria", "fièvre": "fever", "douleur thoracique": "chest pain", "tension": "blood pressure",
    "diabète": "diabetes", "toux": "cough", "maux de tête": "headache", "diarrhée": "diarrhoea",
}

_lock = threading.Lock()
_index = None  # (fingerprint, vectorizers, matrix, chunks)


def invalidate():
    global _index
    _index = None


def _fingerprint():
    from django.db.models import Count, Max
    from .models import KnowledgeDocument
    agg = KnowledgeDocument.objects.exclude(review_status="retired").aggregate(n=Count("id"), t=Max("updated_at"))
    return (agg["n"], agg["t"])


def _load_chunks():
    from .models import KnowledgeChunk, KnowledgeDocument
    if not KnowledgeDocument.objects.exists():
        from .ingest import ingest
        ingest()
    out = []
    for c in KnowledgeChunk.objects.select_related("document").exclude(document__review_status="retired"):
        d = c.document
        out.append({"id": d.slug, "title": d.title, "heading": c.heading, "text": c.text, "order": c.order,
                    "keywords": d.keywords, "emergency": d.emergency, "source": (d.source_name, d.source_url),
                    "reviewed": d.is_reviewed, "published": d.source_published.isoformat() if d.source_published else None,
                    "version": d.source_version, "summary_version": d.summary_version, "kind": d.kind})
    return out


def _build():
    global _index
    from scipy.sparse import hstack
    from sklearn.feature_extraction.text import TfidfVectorizer
    with _lock:
        fp = _fingerprint()
        if _index and _index[0] == fp:
            return _index
        chunks = _load_chunks()
        fp = _fingerprint()
        corpus = [f"{c['title']} {c['title']} {c['heading']} {c['keywords']} {c['text']}" for c in chunks]
        words = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True)
        chars = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True, min_df=1)
        matrix = hstack([words.fit_transform(corpus), chars.fit_transform(corpus) * 0.5]).tocsr()
        _index = (fp, (words, chars), matrix, chunks)
        return _index


def _index_now():
    idx = _index
    if idx is None or idx[0] != _fingerprint():
        idx = _build()
    return idx


_MEANING = re.compile(r"\bwhat does .{1,30}\bmean\b|\b(meaning of|what is the meaning|stand for|abbreviation)\b|(?:^|\s)-\w+|\b\w+-(?:\s|$)", re.I)


def lay_expand(question):
    """Adds the medical terms for everyday words (used for search AND for the coverage check)."""
    q = question.lower()
    extra = [v for k, v in LAY_TERMS.items() if re.search(r"(?<!\w)" + re.escape(k) + r"(?!\w)", q)]
    return question + (" " + " ".join(extra) if extra else "")


def expand(question):
    """lay_expand + search hints for the kind of question (anatomy, word meaning, abbreviation)."""
    q = question.lower()
    extra = []
    if re.search(r"\bwhat (does|do) (the |your |my |our )?\w+ do\b|\bfunction of\b", q):
        extra.append("anatomy physiology organ function body system what they do")
    if re.search(r"\b(stand for|abbreviation|short for)\b", q):
        extra.append("abbreviation abbreviations")
    elif _MEANING.search(q):
        extra.append("medical word meaning terminology prefix suffix")
    return lay_expand(question) + (" " + " ".join(extra) if extra else "")


def _rank(question):
    from scipy.sparse import hstack
    from sklearn.metrics.pairwise import cosine_similarity
    _, (words, chars), matrix, chunks = _index_now()
    qv = hstack([words.transform([question]), chars.transform([question]) * 0.5])
    return cosine_similarity(qv, matrix)[0], chunks


def retrieve(question, k=3):
    """Top documents: [(score, best_chunk_dict)], one entry per document."""
    scores, chunks = _rank(expand(question))
    best = {}
    for s, c in zip(scores, chunks):
        if s >= MIN_SCORE and (c["id"] not in best or s > best[c["id"]][0]):
            best[c["id"]] = (float(s), c)
    return sorted(best.values(), key=lambda t: t[0], reverse=True)[:k]


def passages_for(question, docs, per_doc=4, limit=10):
    """Context for the answer: the most relevant sections of each retrieved document, in document order."""
    scores, chunks = _rank(expand(question))
    wanted = {c["id"] for _, c in docs}
    by_doc = {}
    for s, c in sorted(zip(scores, chunks), key=lambda t: -t[0]):
        if c["id"] in wanted and len(by_doc.setdefault(c["id"], [])) < per_doc and (s >= MIN_SCORE * 0.5 or not by_doc[c["id"]]):
            by_doc[c["id"]].append(c)
    out = []
    for _, d in docs:  # keep document ranking, sections in reading order
        out += sorted(by_doc.get(d["id"], [d]), key=lambda c: c["order"])
    return out[:limit]


_GENERIC = set("""what which who whom whose why how when where is are was were be been do does did can could should
would will may might must the a an of for to in on at by with about from and or not no my me i you your his her their
our we they it this that these those there here have has had having get gets got feel feels feeling treat treated
treatment treatments cure symptom symptoms sign signs cause causes caused disease diseases condition conditions
illness problem problems medical medicine health normal mean means meaning type types test tests tested tell explain
difference between best way much many often long day days week weeks someone person people child children adult
please help know need want really very good bad thing things que le la les est une des qu'est ce c'est quoi""".split())


def _covers(question, docs):
    """Is at least one specific word of the question present in the retrieved documents?
    Stops the assistant quoting unrelated text for topics it has no document about ("Fabry disease")."""
    words = [w for w in re.findall(r"[a-zà-ÿ0-9]+", question.lower()) if len(w) > 2 and w not in _GENERIC]
    if not words:
        return True
    text = " ".join(f"{d['title']} {d['keywords']} {d['text']} {d['heading']}" for _, d in docs).lower()
    vocab = set(re.findall(r"[a-zà-ÿ0-9]+", text))
    stems = {v[:5] for v in vocab if len(v) >= 5}
    return any(w in vocab or (len(w) >= 5 and w[:5] in stems) for w in words)


def red_flags(question):
    return safety.emergency_phrases(question)


_INTENTS = [
    (r"\b(cause|causes|why do|why does|what makes)\b", r"cause"),
    (r"\b(symptom|symptoms|signs? of)\b", r"symptom|signs"),
    (r"\b(normal|range|healthy level|typical)\b", r"normal|typical|what .* means"),
    (r"\b(treat|treatment|cure|manage|what helps)\b", r"treat|first aid|what usually helps"),
    (r"\b(prevent|prevention|avoid|protect)\b", r"prevent"),
    (r"\b(test|tested|diagnos|investigat)\w*", r"investigat|test"),
    (r"\b(difference|differ|vs|versus|compare)\b", r"difference"),
    (r"\b(when should|seek|go to hospital|get help)\b", r"seek|when to|emergency"),
    (r"\b(low|drop)\b.*\b(oxygen|spo2|saturation)\b", r"low oxygen"),
]


def _extractive(question, docs, passages):
    """Quote the trusted text itself: top document's relevant sections, labelled by heading."""
    top = docs[0][1]
    parts = [p for p in passages if p["id"] == top["id"]] or [top]
    # the section the question asks for first (causes, symptoms, ...), then the rest in reading order
    lead = next((p for pattern, heads in _INTENTS if re.search(pattern, question, re.I)
                 for p in parts if re.search(heads, p["heading"], re.I)), None)
    if lead is None and not re.match(r"\s*(what is|what are|tell me about|explain|qu'est-ce que|c'est quoi)\b", question, re.I):
        lead = top
    if lead is not None:
        parts = [lead] + [p for p in parts if p["order"] != lead["order"]]
    if len(parts) == 1:
        return parts[0]["text"]
    return "\n\n".join(f"{p['heading']}: {p['text']}" for p in parts)


def _follow_up(profile, question, docs):
    if profile["emergency"] or not docs or not profile["medical"]:
        return []
    q = question.lower()
    asks = []
    symptomatic = any(d["kind"] == "symptom" for _, d in docs[:2])
    if symptomatic and not re.search(r"\b(day|days|week|weeks|hour|hours|since|yesterday)\b", q):
        asks.append("How long have you had this?")
    if symptomatic and not re.search(r"\b(\d+ ?(years?|yrs?|months?)|child|baby|adult|old)\b", q):
        asks.append("How old is the person who is unwell?")
    return asks[:2]


def _with_context(question, history):
    """Short follow-ups ("what about prevention?") borrow the last user question's topic for retrieval."""
    if not history or len(question.split()) > 8:
        return question
    last = next((t["text"] for t in reversed(history) if t.get("role") == "user"), "")
    return f"{question} {last}" if last else question


def answer(question, history=None, vitals_text="", hotline="912"):
    """Returns a dict: ok, answer, mode, emergency, red_flags, disclaimer, sources, follow_up, safety, language."""
    question = (question or "").strip()[:500]
    if not question:
        return {"ok": False, "error": "Please type a question."}
    profile = safety.assess(question)
    flags = profile["red_flags"]
    base = {"disclaimer": DISCLAIMER, "red_flags": flags, "language": profile["language"],
            "safety": {k: profile[k] for k in ("emergency", "emergency_topic", "medication", "diagnosis_request")}}
    try:
        search_q = _with_context(question, history or [])
        docs = retrieve(search_q)
        if docs and not _covers(lay_expand(search_q), docs):
            docs = []
        passages = passages_for(search_q, docs) if docs else []
    except Exception:
        log.exception("Retriever unavailable")
        return {**base, "ok": False, "emergency": bool(flags),
                "error": "The information assistant is temporarily unavailable. "
                         "If this is an emergency, use the Emergency button now."}
    emergency = profile["emergency"]
    preface = []
    if emergency:
        preface.append(safety.EMERGENCY_TEXT.format(hotline=hotline))
    if profile["medication"]:
        preface.append(safety.MEDICATION_TEXT[profile["medication"]])
    if profile["diagnosis_request"]:
        preface.append(safety.DIAGNOSIS_TEXT)

    if not docs:
        text = safety.NOT_FOUND_TEXT if profile["medical"] else safety.OUT_OF_SCOPE_TEXT
        mode, cited = "none", []
    else:
        generated = llm.generate(question, passages, history or [], profile, vitals_text)
        if generated:
            text, cited = generated
            mode = "llm_grounded"
            preface = [p for p in preface if p.startswith("This may be")] if emergency else []  # model handles the rest
        else:
            text, mode, cited = _extractive(question, docs, passages), "extractive", []
            if profile["language"] in safety.LANGUAGE_NOTE:
                preface.append(safety.LANGUAGE_NOTE[profile["language"]])
    full = "\n\n".join(preface + [text])

    numbered = passages if mode == "llm_grounded" else []
    seen, sources = set(), []
    for i, p in enumerate(numbered or [d for _, d in docs], 1):
        if p["id"] in seen or (numbered and cited and i not in cited):
            continue
        seen.add(p["id"])
        score = next((s for s, d in docs if d["id"] == p["id"]), 0.0)
        sources.append({"n": i if numbered else None, "title": p["title"], "source": p["source"][0], "url": p["source"][1],
                        "score": round(score, 3), "excerpt": p["text"], "reviewed": p["reviewed"],
                        "published": p["published"], "version": p["version"], "summary_version": p["summary_version"],
                        "doc_id": p["id"]})
    return {**base, "ok": True, "answer": full, "mode": mode, "emergency": emergency, "sources": sources,
            "follow_up": [] if mode == "llm_grounded" else _follow_up(profile, question, docs)}
