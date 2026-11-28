"""Training data for the intent classifier.

CLINC150 ("An Evaluation Dataset for Intent Classification and Out-of-Scope Prediction", Larson et al., EMNLP 2019)
  source  https://github.com/clinc/oos-eval  (data/data_full.json, ~2.5 MB)
  licence Creative Commons Attribution 3.0 Unported (LICENSE file in that repository, checked 2026-11-28)
  use     general conversation only: greeting / goodbye / thank_you / what_can_i_ask_you etc. are mapped to our
          labels; every other CLINC intent and the out-of-scope queries become "out_of_scope". It contains NO
          medical data and is never used to learn anything medical.

The raw file is downloaded to ai_training/datasets/raw/ (git-ignored) and its SHA-256 is recorded in the model
metadata, so every trained model states exactly which data produced it.
"""
import hashlib
import json
import random
import re
import unicodedata
from pathlib import Path
from urllib.request import urlopen

RAW_DIR = Path(__file__).resolve().parent / "datasets" / "raw"
CLINC_URL = "https://raw.githubusercontent.com/clinc/oos-eval/master/data/data_full.json"
CLINC_FILE = RAW_DIR / "clinc150_data_full.json"
CLINC_INFO = {"name": "CLINC150 (oos-eval data_full)", "url": "https://github.com/clinc/oos-eval",
              "licence": "CC BY 3.0", "citation": "Larson et al., EMNLP 2019"}

CLINC_MAP = {
    "greeting": "greeting", "goodbye": "goodbye", "thank_you": "thanks",
    "what_can_i_ask_you": "capabilities", "are_you_a_bot": "capabilities", "what_is_your_name": "capabilities",
    "who_made_you": "capabilities", "meaning_of_life": "out_of_scope",
}
OTHER_PER_INTENT = 3     # keep the 140+ unrelated CLINC intents from drowning our 11 labels
OOS_SAMPLE = 150
MAPPED_PER_INTENT = 60   # cap each mapped CLINC intent (4 map to "capabilities" -> it was 613 rows, 30x the others)


def download_clinc(force=False):
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    if force or not CLINC_FILE.exists():
        with urlopen(CLINC_URL, timeout=60) as r:  # public GitHub raw file
            data = r.read()
        json.loads(data)  # refuse anything that is not the expected JSON
        CLINC_FILE.write_bytes(data)
    return CLINC_FILE


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def clean(text):
    """Normalise: unicode NFC, lower case, collapse spaces, strip. Empty / non-text rows are dropped by the caller."""
    if not isinstance(text, str):
        return ""
    t = unicodedata.normalize("NFC", text).lower().strip()
    return re.sub(r"\s+", " ", t)


def clinc_rows(seed=13):
    raw = json.loads(CLINC_FILE.read_text(encoding="utf-8"))
    rng = random.Random(seed)
    by_intent = {}
    for split in ("train", "val", "test"):
        for text, intent in raw.get(split, []):
            by_intent.setdefault(intent, []).append(text)
    oos = [t for split in ("oos_train", "oos_val", "oos_test") for t, _ in raw.get(split, [])]
    rows = []
    for intent, texts in sorted(by_intent.items()):
        if intent in CLINC_MAP:
            cap = MAPPED_PER_INTENT // 2 if CLINC_MAP[intent] == "capabilities" else MAPPED_PER_INTENT
            rows += [(t, CLINC_MAP[intent], f"clinc:{intent}") for t in rng.sample(texts, min(cap, len(texts)))]
        else:
            rows += [(t, "out_of_scope", f"clinc:{intent}") for t in rng.sample(texts, min(OTHER_PER_INTENT, len(texts)))]
    rows += [(t, "out_of_scope", "clinc:oos") for t in rng.sample(oos, min(OOS_SAMPLE, len(oos)))]
    return rows


def build(use_clinc=True, seed=13):
    """Returns (rows, report). rows = [(text, label, origin)] cleaned and de-duplicated."""
    from . import project_intents
    raw = project_intents.examples() + (clinc_rows(seed) if use_clinc else [])
    seen, rows, dropped_empty, dropped_dup, conflicts = {}, [], 0, 0, 0
    for text, label, origin in raw:
        t = clean(text)
        if not t:
            dropped_empty += 1
            continue
        if t in seen:
            if seen[t] != label:
                conflicts += 1  # same text, two labels: keep the first (project data is listed first)
            dropped_dup += 1
            continue
        seen[t] = label
        rows.append((t, label, origin))
    counts = {}
    for _, label, _ in rows:
        counts[label] = counts.get(label, 0) + 1
    origins = {}
    for _, _, origin in rows:
        fam = origin.split(":")[0]
        origins[fam] = origins.get(fam, 0) + 1
    report = {"rows": len(rows), "rows_by_origin": origins, "dropped_empty": dropped_empty,
              "dropped_duplicates": dropped_dup,
              "label_conflicts": conflicts, "label_counts": dict(sorted(counts.items())),
              "sources": ["project-authored examples (see ai_training/project_intents.py)"]}
    if use_clinc:
        report["sources"].append({**CLINC_INFO, "sha256": sha256(CLINC_FILE)})
    return rows, report
