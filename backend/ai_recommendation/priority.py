"""
Emergency priority (triage-support) model.

A RandomForest classifier trained on SYNTHETIC data generated from simple
clinical-style rules. It outputs one of: low, medium, high, critical.

IMPORTANT: This is decision SUPPORT for a student/research prototype. It is not a
validated clinical triage tool. Operators must be able to override the priority.
To improve it, replace `generate_synthetic_dataset` with a real, de-identified,
ethically-approved dataset.
"""
from pathlib import Path

import joblib
import numpy as np
from django.conf import settings
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report
from sklearn.model_selection import train_test_split

EMERGENCY_TYPES = ["cardiac", "trauma", "respiratory", "stroke", "maternity", "burns", "poisoning", "other"]
PRIORITIES = ["low", "medium", "high", "critical"]
FEATURES = ["type_idx", "age", "conscious", "breathing_difficulty", "severe_bleeding",
            "chest_pain", "pain_level", "pregnant"]

# Which hospital specialty each emergency type needs
TYPE_TO_SPECIALTY = {
    "cardiac": "cardiology", "trauma": "trauma", "respiratory": "pulmonology",
    "stroke": "neurology", "maternity": "maternity", "burns": "burns",
    "poisoning": "toxicology", "other": "general",
}


def _rule_label(t, age, conscious, breathing, bleeding, chest, pain, pregnant):
    score = 0
    if not conscious:
        score += 4
    if breathing:
        score += 3
    if bleeding:
        score += 3
    if chest:
        score += 2
    score += pain // 4
    if t in ("cardiac", "stroke"):
        score += 2
    if t == "maternity" and pregnant:
        score += 1
    if age >= 65 or age <= 5:
        score += 1
    if score >= 8:
        return "critical"
    if score >= 5:
        return "high"
    if score >= 3:
        return "medium"
    return "low"


def generate_synthetic_dataset(n=5000, seed=42):
    rng = np.random.default_rng(seed)
    X, y = [], []
    for _ in range(n):
        ti = int(rng.integers(0, len(EMERGENCY_TYPES)))
        t = EMERGENCY_TYPES[ti]
        age = int(rng.integers(0, 95))
        conscious = int(rng.random() > 0.15)
        breathing = int(rng.random() < (0.5 if t in ("respiratory", "cardiac") else 0.15))
        bleeding = int(rng.random() < (0.5 if t == "trauma" else 0.08))
        chest = int(rng.random() < (0.7 if t == "cardiac" else 0.1))
        pain = int(rng.integers(0, 11))
        pregnant = int(t == "maternity" or rng.random() < 0.03)
        label = _rule_label(t, age, conscious, breathing, bleeding, chest, pain, pregnant)
        if rng.random() < 0.05:  # label noise so the model must generalise
            label = PRIORITIES[int(rng.integers(0, 4))]
        X.append([ti, age, conscious, breathing, bleeding, chest, pain, pregnant])
        y.append(label)
    return np.array(X), np.array(y)


def train_model(save_path=None, verbose=True):
    X, y = generate_synthetic_dataset()
    X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.2, random_state=0, stratify=y)
    clf = RandomForestClassifier(n_estimators=150, max_depth=12, random_state=0)
    clf.fit(X_tr, y_tr)
    report = classification_report(y_te, clf.predict(X_te), output_dict=True, zero_division=0)
    if verbose:
        print(classification_report(y_te, clf.predict(X_te), zero_division=0))
    path = Path(save_path or settings.AI_MODEL_PATH)
    joblib.dump(clf, path)
    return clf, report


_model = None


def get_model():
    global _model
    if _model is None:
        path = Path(settings.AI_MODEL_PATH)
        if path.exists():
            _model = joblib.load(path)
        else:
            _model, _ = train_model(path, verbose=False)
    return _model


def to_features(data):
    t = data.get("emergency_type", "other")
    ti = EMERGENCY_TYPES.index(t) if t in EMERGENCY_TYPES else EMERGENCY_TYPES.index("other")
    return [ti, int(data.get("age") or 35), int(bool(data.get("conscious", True))),
            int(bool(data.get("breathing_difficulty"))), int(bool(data.get("severe_bleeding"))),
            int(bool(data.get("chest_pain"))), int(data.get("pain_level") or 0),
            int(bool(data.get("pregnant")))]


def predict_priority(data):
    """Returns (priority_label, confidence 0..1)."""
    model = get_model()
    x = np.array([to_features(data)])
    probs = model.predict_proba(x)[0]
    idx = int(np.argmax(probs))
    return str(model.classes_[idx]), round(float(probs[idx]), 3)
