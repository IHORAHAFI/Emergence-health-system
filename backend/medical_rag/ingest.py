"""Medical document ingestion: corpus files + built-in notes + reviewed library entries -> KnowledgeDocument/Chunk.

* Idempotent: unchanged documents are skipped (content hash).
* A changed document gets a new summary_version and goes back to "Pending clinical review".
* Each "## " section becomes one chunk (the unit the retriever searches).
"""
import datetime
import hashlib
import logging
import re
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from .knowledge_base import DOCUMENTS
from .models import Disease, KnowledgeChunk, KnowledgeDocument

log = logging.getLogger(__name__)
CORPUS_DIR = Path(__file__).resolve().parent / "corpus"
_FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S | re.M)


# ------------------------------------------------------------------ parsing
def parse_corpus_text(text, origin_name="corpus"):
    """Splits a file into documents: each starts with a '---' front-matter block."""
    docs = []
    matches = list(_FRONT.finditer(text))
    for i, m in enumerate(matches):
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        meta = {}
        for line in m.group(1).splitlines():
            if ":" in line and not line.lstrip().startswith("#"):
                k, v = line.split(":", 1)
                meta[k.strip()] = v.split(" #")[0].strip()
        body = text[m.end():body_end].strip()
        if not meta.get("id") or not body:
            continue
        missing = [f for f in ("title", "source_name", "source_url") if not meta.get(f)]
        if missing:
            raise ValueError(f"{origin_name}: document '{meta['id']}' is missing {', '.join(missing)}")
        docs.append({**meta, "content": body})
    return docs


def load_corpus(directory=CORPUS_DIR):
    docs = []
    for f in sorted(Path(directory).glob("*.md")):
        if f.name.lower() == "readme.md":
            continue
        docs += parse_corpus_text(f.read_text(encoding="utf-8"), f.name)
    return docs


def chunk(content):
    """[(heading, text)] - one chunk per '## ' section (text before the first heading becomes 'Overview')."""
    out, heading, buf = [], "Overview", []
    for line in content.splitlines():
        if line.startswith("## "):
            if "".join(buf).strip():
                out.append((heading, " ".join(l.strip() for l in buf if l.strip())))
            heading, buf = line[3:].strip(), []
        else:
            buf.append(line)
    if "".join(buf).strip():
        out.append((heading, " ".join(l.strip() for l in buf if l.strip())))
    return out


def _date(v):
    try:
        return datetime.date.fromisoformat(v) if v else None
    except ValueError:
        raise ValueError(f"source_published must be YYYY-MM-DD, got {v!r}")


def _builtin_docs():
    return [{"id": d["id"], "title": d["title"], "keywords": d["keywords"], "emergency": d["emergency"],
             "source_name": d["source"][0], "source_url": d["source"][1], "category": "first_aid",
             "kind": "first_aid", "content": f"## {d['title']}\n{d['text']}", "_origin": "builtin"}
            for d in DOCUMENTS]


def _library_docs():
    """Disease-library entries that a clinician has approved (they are already reviewed)."""
    out = []
    for d in Disease.objects.filter(review_status=Disease.Review.APPROVED):
        parts = [("Definition", d.description), ("Causes", d.causes), ("Risk factors", d.risk_factors),
                 ("Symptoms", d.symptoms), ("Warning signs", d.warning_signs), ("How it is investigated", d.diagnosis),
                 ("Treatment approaches", d.treatment), ("Prevention", d.prevention)]
        content = "\n\n".join(f"## {h}\n" + "; ".join(Disease.lines(t)) for h, t in parts if (t or "").strip())
        out.append({"id": f"library-{d.slug}"[:120], "title": d.name, "category": d.category, "kind": "condition",
                    "keywords": d.name, "content": content, "source_name": d.source_name, "source_url": d.source_url,
                    "_origin": "library", "_approved": True,
                    "emergency": False})
    return out


# ------------------------------------------------------------------ storing
def _store(doc, origin):
    content = doc["content"].strip()
    h = hashlib.sha256(f"{doc['title']}\n{doc.get('keywords', '')}\n{content}".encode()).hexdigest()
    fields = dict(
        title=doc["title"][:200], category=doc.get("category", "")[:40], kind=doc.get("kind", "")[:20],
        keywords=doc.get("keywords", ""), content=content,
        emergency=str(doc.get("emergency", "false")).lower() in ("true", "1", "yes"),
        source_name=doc["source_name"][:200], source_url=doc["source_url"],
        source_published=_date(doc.get("source_published")) if isinstance(doc.get("source_published"), str)
        else doc.get("source_published"),
        source_version=(doc.get("source_version") or "")[:60], origin=origin)
    obj = KnowledgeDocument.objects.filter(slug=doc["id"]).first()
    if obj and obj.content_hash == h:
        return "unchanged"
    with transaction.atomic():
        if obj is None:
            obj = KnowledgeDocument(slug=doc["id"], content_hash=h, **fields)
            status = "created"
        else:
            for k, v in fields.items():
                setattr(obj, k, v)
            obj.content_hash = h
            obj.summary_version += 1
            status = "updated"
            if obj.review_status == KnowledgeDocument.Review.APPROVED:
                # changed text must be reviewed again
                obj.review_status, obj.reviewed_by, obj.reviewed_at = KnowledgeDocument.Review.PENDING, None, None
        if doc.get("_approved"):
            obj.review_status = KnowledgeDocument.Review.APPROVED
            obj.reviewed_at = obj.reviewed_at or timezone.now()
        obj.save()
        obj.chunks.all().delete()
        KnowledgeChunk.objects.bulk_create(
            [KnowledgeChunk(document=obj, order=i, heading=hd[:200], text=t) for i, (hd, t) in enumerate(chunk(content))])
    return status


def ingest(sources=("corpus", "builtin", "library"), directory=CORPUS_DIR):
    """Returns {'created': n, 'updated': n, 'unchanged': n}."""
    counts = {"created": 0, "updated": 0, "unchanged": 0}
    batches = []
    if "builtin" in sources:
        batches.append(("builtin", _builtin_docs()))
    if "corpus" in sources:
        batches.append(("corpus", load_corpus(directory)))
    if "library" in sources:
        batches.append(("library", _library_docs()))
    for origin, docs in batches:
        for d in docs:
            counts[_store(d, d.get("_origin", origin))] += 1
    from . import retriever
    retriever.invalidate()
    return counts


def check_urls(timeout=10):
    """HEAD/GET every source URL and record the result (needs internet)."""
    import requests
    results = {}
    for d in KnowledgeDocument.objects.exclude(review_status=KnowledgeDocument.Review.RETIRED):
        try:
            r = requests.head(d.source_url, timeout=timeout, allow_redirects=True,
                              headers={"User-Agent": "medical-rag-source-check/1.0"})
            if r.status_code >= 400:
                r = requests.get(d.source_url, timeout=timeout, stream=True,
                                 headers={"User-Agent": "medical-rag-source-check/1.0"})
            status = f"HTTP {r.status_code}"
        except Exception as e:
            status = f"error: {type(e).__name__}"
        d.source_check_status, d.source_checked_at = status[:40], timezone.now()
        d.save(update_fields=["source_check_status", "source_checked_at"])
        results[d.slug] = status
    return results
