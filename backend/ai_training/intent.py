"""Supervised intent classifier: TF-IDF (word 1-2-grams + character 2-5-grams) -> logistic regression.

Why this model: a few thousand short sentences, CPU only, 7.7 GB RAM, three languages with spelling variation.
Character n-grams cope with typos and with French / Kinyarwanda word forms; logistic regression gives calibrated-
enough probabilities, trains in seconds and is easy to inspect. A transformer would need far more data and memory.

Split: stratified 70 % train / 15 % validation / 15 % test (fixed seed). The validation set picks C (regularisation);
the test set is touched once, for the reported metrics.

Safety: the classifier never REMOVES an emergency found by the safety rules. Emergency recall (how many real
emergency messages are caught) is the gate metric; a candidate whose test emergency recall is below
GATE_EMERGENCY_RECALL is refused automatically.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, precision_recall_fscore_support
from sklearn.model_selection import train_test_split
from sklearn.pipeline import FeatureUnion, Pipeline

MODELS_DIR = Path(__file__).resolve().parent / "models"
GATE_EMERGENCY_RECALL = 0.95
GATE_MACRO_F1 = 0.75


def make_pipeline(c=4.0):
    return Pipeline([
        ("features", FeatureUnion([
            ("words", TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, min_df=1)),
            ("chars", TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), sublinear_tf=True, min_df=2)),
        ])),
        ("clf", LogisticRegression(C=c, max_iter=3000, class_weight="balanced")),
    ])


def _metrics(y_true, y_pred, labels):
    p, r, f, s = precision_recall_fscore_support(y_true, y_pred, labels=labels, zero_division=0)
    per = {lab: {"precision": round(float(p[i]), 3), "recall": round(float(r[i]), 3), "f1": round(float(f[i]), 3),
                 "support": int(s[i])} for i, lab in enumerate(labels)}
    mp, mr, mf, _ = precision_recall_fscore_support(y_true, y_pred, labels=labels, average="macro", zero_division=0)
    return {"accuracy": round(float(accuracy_score(y_true, y_pred)), 3), "macro_precision": round(float(mp), 3),
            "macro_recall": round(float(mr), 3), "macro_f1": round(float(mf), 3), "per_label": per}


def train(rows, data_report, seed=13):
    texts = [t for t, _, _ in rows]
    labels_all = [lab for _, lab, _ in rows]
    labels = sorted(set(labels_all))
    x_tmp, x_test, y_tmp, y_test = train_test_split(texts, labels_all, test_size=0.15, stratify=labels_all,
                                                    random_state=seed)
    x_tr, x_val, y_tr, y_val = train_test_split(x_tmp, y_tmp, test_size=0.15 / 0.85, stratify=y_tmp,
                                                random_state=seed)
    # model selection on the validation set only
    tried = {}
    for c in (1.0, 4.0, 16.0):
        m = make_pipeline(c).fit(x_tr, y_tr)
        tried[c] = _metrics(y_val, m.predict(x_val), labels)["macro_f1"]
    best_c = max(tried, key=lambda c: (tried[c], -c))
    model = make_pipeline(best_c).fit(x_tr, y_tr)
    train_m = _metrics(y_tr, model.predict(x_tr), labels)
    val_m = _metrics(y_val, model.predict(x_val), labels)
    pred = model.predict(x_test)
    test_m = _metrics(y_test, pred, labels)
    cm = confusion_matrix(y_test, pred, labels=labels)
    missed = [t for t, y, p in zip(x_test, y_test, pred) if y == "emergency" and p != "emergency"]
    false_alarm = [t for t, y, p in zip(x_test, y_test, pred) if y != "emergency" and p == "emergency"]
    # refit on train+validation for the saved model (test set still never seen)
    final = make_pipeline(best_c).fit(x_tr + x_val, y_tr + y_val)

    version = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    path = MODELS_DIR / f"intent-{version}.joblib"
    joblib.dump({"model": final, "labels": labels, "version": version}, path)
    emergency_recall = test_m["per_label"].get("emergency", {}).get("recall", 0.0)
    gate = {"emergency_recall": emergency_recall, "emergency_recall_min": GATE_EMERGENCY_RECALL,
            "macro_f1": test_m["macro_f1"], "macro_f1_min": GATE_MACRO_F1,
            "passed": emergency_recall >= GATE_EMERGENCY_RECALL and test_m["macro_f1"] >= GATE_MACRO_F1}
    metrics = {"model": f"tfidf(word1-2+char2-5)+logreg C={best_c}", "seed": seed,
               "split_sizes": {"train": len(x_tr), "validation": len(x_val), "test": len(x_test)},
               "validation_macro_f1_by_C": {str(k): v for k, v in tried.items()},
               "train": train_m, "validation": val_m, "test": test_m,
               "confusion_matrix": {"labels": labels, "matrix": cm.tolist()},
               "test_emergency_missed": missed, "test_false_emergency": false_alarm,
               "report_text": classification_report(y_test, pred, labels=labels, zero_division=0)}
    (MODELS_DIR / f"intent-{version}.json").write_text(json.dumps(
        {"version": version, "metrics": metrics, "gate": gate, "data": data_report}, indent=2, ensure_ascii=False),
        encoding="utf-8")
    return path, version, metrics, gate


def load(path):
    bundle = joblib.load(path)
    return bundle


def predict(bundle, text):
    model = bundle["model"]
    proba = model.predict_proba([text])[0]
    classes = list(model.classes_)
    i = int(np.argmax(proba))
    return classes[i], float(proba[i]), {c: float(p) for c, p in zip(classes, proba)}
