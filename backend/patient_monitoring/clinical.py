"""
Clinical decision SUPPORT for vital signs.

Uses NEWS2 (National Early Warning Score 2, Royal College of Physicians, 2017) -
a published, clinically validated track-and-trigger score - with SpO2 Scale 1.

  https://www.rcp.ac.uk/improving-care/resources/national-early-warning-score-news-2/

Important limits (shown to staff):
  * It never diagnoses. It flags readings for PROFESSIONAL review and explains why.
  * Home-device readings and readings with missing parameters give a PARTIAL score;
    a partial score can under-estimate risk.
  * SpO2 Scale 2 (for patients with known hypercapnic respiratory failure) must be
    chosen by a clinician - this module always uses Scale 1.
  * Diastolic pressure is not part of NEWS2; it is flagged separately at >=120 mmHg
    (hypertensive-crisis threshold of the 2017 ACC/AHA guideline).
"""

PARAMETERS = ("respiratory_rate", "spo2_pct", "on_supplemental_oxygen", "systolic_mmhg",
              "heart_rate_bpm", "consciousness", "temperature_c")


def _band(value, bands):
    for test, points in bands:
        if test(value):
            return points
    return 0


def score_respiration(v):
    return _band(v, [(lambda x: x <= 8, 3), (lambda x: x <= 11, 1), (lambda x: x <= 20, 0),
                     (lambda x: x <= 24, 2), (lambda x: True, 3)])


def score_spo2(v):
    return _band(v, [(lambda x: x <= 91, 3), (lambda x: x <= 93, 2), (lambda x: x <= 95, 1), (lambda x: True, 0)])


def score_systolic(v):
    return _band(v, [(lambda x: x <= 90, 3), (lambda x: x <= 100, 2), (lambda x: x <= 110, 1),
                     (lambda x: x <= 219, 0), (lambda x: True, 3)])


def score_pulse(v):
    return _band(v, [(lambda x: x <= 40, 3), (lambda x: x <= 50, 1), (lambda x: x <= 90, 0),
                     (lambda x: x <= 110, 1), (lambda x: x <= 130, 2), (lambda x: True, 3)])


def score_temperature(v):
    return _band(v, [(lambda x: x <= 35.0, 3), (lambda x: x <= 36.0, 1), (lambda x: x <= 38.0, 0),
                     (lambda x: x <= 39.0, 1), (lambda x: True, 2)])


RISK_ORDER = ["none", "low", "low_medium", "medium", "high"]
RISK_LABEL = {"none": "No data", "low": "Low", "low_medium": "Low-medium (single red score)",
              "medium": "Medium - urgent review", "high": "High - emergency review"}


def assess(r):
    """r: dict with any of PARAMETERS. Returns score, partial flag, risk and explained alerts."""
    alerts, total, any_three, measured = [], 0, False, 0

    def add(name, value, points, unit, why):
        nonlocal total, any_three
        total += points
        any_three |= points == 3
        if points:
            alerts.append({"parameter": name, "value": value, "unit": unit, "points": points,
                           "severity": "red" if points == 3 else ("amber" if points == 2 else "yellow"),
                           "explanation": f"{why} - NEWS2 gives {points} point{'s' if points > 1 else ''}"})

    checks = [
        ("respiratory_rate", "Respiratory rate", "/min", score_respiration, "outside 12-20 breaths/min"),
        ("spo2_pct", "Oxygen saturation (SpO2)", "%", score_spo2, "below 96%"),
        ("systolic_mmhg", "Systolic blood pressure", "mmHg", score_systolic, "outside 111-219 mmHg"),
        ("heart_rate_bpm", "Heart rate", "bpm", score_pulse, "outside 51-90 bpm"),
        ("temperature_c", "Temperature", "°C", score_temperature, "outside 36.1-38.0 °C"),
    ]
    for key, label, unit, fn, why in checks:
        v = r.get(key)
        if v is None:
            continue
        measured += 1
        add(label, float(v), fn(float(v)), unit, f"{label} {float(v):g} {unit} is {why}")

    if r.get("on_supplemental_oxygen"):
        add("Supplemental oxygen", "yes", 2, "", "Patient is on supplemental oxygen")
    if r.get("consciousness"):
        measured += 1
        if r["consciousness"] != "alert":
            add("Consciousness", r["consciousness"], 3, "", "Not fully alert (new confusion / V / P / U)")

    if r.get("diastolic_mmhg") is not None and float(r["diastolic_mmhg"]) >= 120:
        alerts.append({"parameter": "Diastolic blood pressure", "value": float(r["diastolic_mmhg"]), "unit": "mmHg",
                       "points": 0, "severity": "amber",
                       "explanation": "Diastolic >= 120 mmHg (ACC/AHA hypertensive-crisis threshold; not part of NEWS2)"})

    if measured == 0:
        return {"score": None, "partial": True, "risk": "none", "risk_label": RISK_LABEL["none"], "alerts": alerts,
                "missing": list(PARAMETERS)}

    risk = "high" if total >= 7 else "medium" if total >= 5 else "low_medium" if any_three else "low"
    missing = [p for p in ("respiratory_rate", "spo2_pct", "systolic_mmhg", "heart_rate_bpm",
                           "consciousness", "temperature_c") if r.get(p) in (None, "")]
    if missing:
        alerts.append({"parameter": "Incomplete set", "value": ", ".join(missing), "unit": "", "points": 0,
                       "severity": "info",
                       "explanation": "Not all NEWS2 parameters were measured - the score may under-estimate risk"})
    return {"score": total, "partial": bool(missing), "risk": risk, "risk_label": RISK_LABEL[risk],
            "alerts": alerts, "missing": missing}


def needs_attention(risk):
    return RISK_ORDER.index(risk or "none") >= RISK_ORDER.index("low_medium")
