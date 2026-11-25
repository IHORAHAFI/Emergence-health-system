"""Safety checks that run BEFORE retrieval and generation, so they never depend on search quality.

* emergency detection (English + French phrases; Kinyarwanda list left for native-speaker review)
* medication requests that need a professional (personal dose, prescribing, stopping/changing treatment)
* self-diagnosis requests ("do I have ...") -> answered as general information with a no-diagnosis reminder
* language detection (en / fr / rw) for the reply language
* out-of-scope detection (no medical words at all)
"""
import re

from .knowledge_base import RED_FLAGS

EMERGENCY_PHRASES = {
    "en": RED_FLAGS + [
        "crushing chest", "chest pressure", "can not breathe", "struggling to breathe", "severe difficulty breathing",
        "lips are blue", "turning blue", "face drooping", "slurred speech", "arm weakness", "one side weak",
        "passed out", "fainted and", "won't wake", "will not wake", "not waking", "bleeding a lot", "won't stop bleeding",
        "coughing up blood", "vomiting blood", "kill myself", "end my life", "want to die", "self harm", "self-harm",
        "anaphylaxis", "throat swelling", "swollen throat", "severe burn", "stiff neck and fever",
        "baby not breathing", "severe allergic",
    ],
    "fr": [
        "douleur thoracique", "douleur à la poitrine", "mal à la poitrine", "ne respire pas", "ne respire plus",
        "difficulté à respirer", "n'arrive pas à respirer", "inconscient", "évanoui", "saigne beaucoup",
        "hémorragie", "convulsion", "crise d'épilepsie", "avc", "paralysie", "me suicider", "suicide",
        "étouffe", "s'étouffe", "empoisonnement", "overdose",
    ],
    # Kinyarwanda phrases must be supplied and checked by a native-speaking clinician before use.
    "rw": [],
}

_DOSE = re.compile(
    r"\b(how (much|many)( mg| tablets| pills| ml)?|what dose|which dose|dosage|dose of|dose for|mg of|"
    r"how often should i take|can i take \d|double (the|my) dose)\b", re.I)
_PRESCRIBE = re.compile(
    r"\b(prescribe|give me (a |an )?(medicine|drug|antibiotic|prescription)|which (medicine|drug|antibiotic|tablet)s? "
    r"(should|can|do) i (take|use|buy)|what (medicine|drug|antibiotic|tablet)s? (should|can|do) i (take|use|buy)|"
    r"recommend (a |an )?(medicine|drug|antibiotic|tablet))\b", re.I)
_STOP_CHANGE = re.compile(
    r"\b(stop|quit|skip|reduce|increase|change|switch|come off) (taking )?(my |the )?(medicine|medication|tablets?|pills?|"
    r"insulin|treatment|arvs?|antiretrovirals?|antibiotics?|dose)\b|\bstop taking\b", re.I)
_DIAGNOSE = re.compile(r"\b(do i have|have i got|am i (sick|ill|infected|pregnant)|is it (cancer|malaria|hiv)|"
                       r"diagnose me|what do i have|what is wrong with me|am i healthy|am i ok(ay)?)\b", re.I)

_FR_WORDS = {"je", "j'ai", "le", "la", "les", "est", "une", "des", "qu'est-ce", "quels", "quelle", "pourquoi",
             "comment", "mal", "fièvre", "douleur", "avec", "pour", "mon", "ma", "symptômes", "que", "qu'est",
             "ce", "du", "c'est", "quoi", "paludisme", "maladie", "santé", "suis", "ai", "ne", "pas"}
_RW_WORDS = {"ni", "iki", "ndwara", "umuriro", "mfite", "ndwaye", "uburwayi", "mu", "kandi", "cyane", "umwana",
             "amaraso", "inkorora", "ese", "ubu", "nkora", "iyo"}

MEDICAL_HINTS = re.compile(
    r"\b(pain|ache|fever|sick|ill|disease|symptom|blood|heart|lung|breath|cough|doctor|nurse|hospital|medicine|"
    r"medication|drug|tablet|pill|dose|infection|virus|bacteria|vaccine|pregnan|baby|child|health|treat|cure|"
    r"diagnos|test|rash|skin|vomit|diarr|stomach|head|chest|pressure|sugar|diabet|malaria|hiv|tb|cancer|wound|"
    r"bleed|burn|injur|bone|kidney|liver|brain|stroke|seizure|mental|depress|anxi|sleep|tired|weak|dizz|oxygen|"
    r"pulse|temperature|bp|spo2|ecg|normal|itis|emia|osis|first aid|emergency|organ|body|anatomy|maladie|"
    r"douleur|fièvre|santé|médicament|ndwara|umuriro|uburwayi|abbreviation|meaning|mean)|(itis|emia|osis|ectomy)\b", re.I)


def detect_language(text):
    words = set(re.findall(r"[\w']+", text.lower()))
    fr, rw = len(words & _FR_WORDS), len(words & _RW_WORDS)
    if rw >= 2 and rw >= fr:
        return "rw"
    if fr >= 2:
        return "fr"
    return "en"


# Word-order-independent patterns (e.g. "her face is drooping", "speech is slurred").
EMERGENCY_PATTERNS = {
    "stroke signs": re.compile(r"\b(face\b.{0,25}\bdroop|droop\w*\b.{0,25}\bface|slurr\w*|"
                               r"(arm|leg|side)\b.{0,20}\b(weak|numb|paraly)|sudden\w* (weakness|numbness))", re.I),
    "not breathing": re.compile(r"\b(is|are|stopped|isn't|not) (not )?breathing\b|\bstopped breathing\b", re.I),
    "unresponsive": re.compile(r"\b(won't|will not|can't|cannot|doesn't|does not) (wake|respond)\b", re.I),
    "severe bleeding": re.compile(r"\bbleed\w*\b.{0,20}\b(a lot|heavily|won't stop|will not stop|badly)\b", re.I),
}


def emergency_phrases(text):
    q = " " + text.lower() + " "
    found = []
    for phrases in EMERGENCY_PHRASES.values():
        found += [p for p in phrases if p and p in q]
    found += [name for name, rx in EMERGENCY_PATTERNS.items() if rx.search(text)]
    return sorted(set(found))


_INFO_ONLY = re.compile(r"^\s*(what|which|why|how|when|explain|define|tell me about|signs of|symptoms of|"
                        r"difference|is it normal|can you explain)\b", re.I)
_PERSONAL = re.compile(r"\b(i|i'm|im|me|my|mine|we|our|he|she|his|her|they|their|someone|somebody|child|baby|"
                       r"son|daughter|father|mother|husband|wife|friend|now|right now|currently|j'ai|mon|ma)\b", re.I)


def assess(question):
    """Returns the safety profile of a question.
    emergency=True  -> the user seems to REPORT an emergency (show the urgent banner first).
    emergency_topic -> the question is ABOUT an emergency topic (answer, and list warning signs)."""
    flags = emergency_phrases(question)
    asking_about = bool(flags) and bool(_INFO_ONLY.search(question)) and not _PERSONAL.search(question)
    medication = None
    if _STOP_CHANGE.search(question):
        medication = "stop_change"
    elif _DOSE.search(question):
        medication = "dose"
    elif _PRESCRIBE.search(question):
        medication = "prescribe"
    return {
        "emergency": bool(flags) and not asking_about,
        "emergency_topic": bool(flags),
        "red_flags": flags,
        "medication": medication,
        "diagnosis_request": bool(_DIAGNOSE.search(question)),
        "language": detect_language(question),
        "medical": bool(MEDICAL_HINTS.search(question)) or bool(flags),
    }


EMERGENCY_TEXT = ("This may be a medical emergency. Get help now: use the Emergency button or call {hotline}. "
                  "Do not wait for more information.")
MEDICATION_TEXT = {
    "dose": "I can't tell you a personal dose. The right dose depends on your age, weight, other illnesses and "
            "medicines. Ask your doctor, nurse or pharmacist, and follow the instructions on your prescription.",
    "prescribe": "I can't recommend or prescribe a medicine for you. A health worker needs to examine you to decide "
                 "which treatment is safe.",
    "stop_change": "Please do not stop or change a prescribed medicine without talking to your health worker first. "
                   "Some medicines are dangerous to stop suddenly. If you have side effects, contact them soon.",
}
DIAGNOSIS_TEXT = ("I can't diagnose you or confirm whether you are healthy. Below is general information; a health "
                  "worker can examine you and arrange tests.")
NOT_FOUND_TEXT = ("I don't have trusted information about that in my medical knowledge base, so I won't guess. "
                  "Please ask a health worker.")
OUT_OF_SCOPE_TEXT = ("I can only answer health and medical questions. Try asking about a symptom, a disease, a "
                     "medical word, a test, or first aid.")
LANGUAGE_NOTE = {
    "fr": "Les réponses en français ne sont pas encore disponibles dans ce mode; la réponse est en anglais.",
    # English on purpose: a Kinyarwanda version must be written and checked by a native speaker.
    "rw": "Kinyarwanda answers are not available yet, so this answer is in English.",
}
