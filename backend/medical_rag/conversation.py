"""Conversation layer in front of the medical knowledge base: understands WHAT KIND of message this is.

    message -> safety.assess (emergency / medication / diagnosis rules - always first, never overridden)
            -> rules for small talk (whole-message patterns, en / fr / rw)
            -> approved intent classifier (ai_training, ACTIVE ModelVersion) for everything else
            -> intent: greeting | how_are_you | thanks | goodbye | capabilities | app_help | tech_support |
                       operator_support | emergency | medical_info | out_of_scope

Only medical_info (and anything medical-sounding) goes on to the knowledge-base search; conversational intents
get a short reply here. The classifier can ADD an emergency (recall first) but can never remove one found by the
rules. Replies in Kinyarwanda are short fixed phrases that still need native-speaker review.
"""
import logging
import re

from . import safety

log = logging.getLogger(__name__)

# Intents answered here. "out_of_scope" is deliberately NOT one of them: such messages still go through the
# knowledge-base search, which only says "I can only answer health questions" when nothing relevant is found.
CONVERSATIONAL = {"greeting", "how_are_you", "thanks", "goodbye", "capabilities", "app_help", "tech_support",
                  "operator_support"}
MODEL_MIN_CONFIDENCE = 0.55
MODEL_EMERGENCY_MIN = 0.50  # deliberately lower: a missed emergency is worse than an extra warning

_W = r"[\s,.!?;:]*"
RULES = [  # whole message only (short messages), so "hi, my chest hurts" is NOT treated as a greeting
    ("emergency", re.compile(rf"^{_W}(please{_W})?help({_W}me)?({_W}please)?{_W}$|^{_W}(sos|emergency){_W}$|"
                             rf"^{_W}(au secours|aidez[- ]moi|urgence){_W}$", re.I)),
    ("greeting", re.compile(rf"^{_W}(hello|hi|hey|hiya|hi there|hello there|greetings|good (morning|afternoon|evening|day)"
                            rf"|morning|bonjour|bonsoir|salut|coucou|muraho( neza)?|mwaramutse|mwiriwe|bite)"
                            rf"({_W}(assistant|there|everyone|madame|monsieur))?{_W}$", re.I)),
    ("how_are_you", re.compile(rf"^{_W}((hi|hello|hey|bonjour|muraho){_W})?(how are you( doing| today)?|how's it going|"
                               rf"how do you do|what's up|comment (ça|ca) va|(ça|ca) va|comment allez[- ]vous|amakuru( yawe)?|"
                               rf"umeze ute){_W}$", re.I)),
    ("thanks", re.compile(rf"^{_W}((ok|okay|great|good|perfect){_W})?(thank you( so much| very much)?|thanks( a lot)?|"
                          rf"many thanks|cheers|merci( beaucoup)?|murakoze( cyane)?|urakoze)({_W}(for (your|the) help))?{_W}$",
                          re.I)),
    ("goodbye", re.compile(rf"^{_W}(bye|goodbye|bye bye|see you( later)?|good night|that's all|au revoir|à bientôt|"
                           rf"bonne nuit|murabeho|mwirirwe|ni aho)({_W}(for now|thanks|thank you))?{_W}$", re.I)),
    ("capabilities", re.compile(rf"^{_W}(what can you do|who are you|what are you|are you (a )?(robot|bot|doctor|human|"
                                rf"real person)|how can you help( me)?|what can (i|you) ask|qui êtes[- ]vous|"
                                rf"que pouvez[- ]vous faire|wowe uri nde){_W}$", re.I)),
]

REPLIES = {
    "greeting": {
        "en": "Hello! Welcome to the Smart Medical Emergency System. How can I help you today?",
        "fr": "Bonjour ! Bienvenue dans le système d'urgence médicale. Comment puis-je vous aider aujourd'hui ?",
        "rw": "Muraho! Murakaza neza. (I can reply fully in English or French.)"},
    "how_are_you": {
        "en": "I'm here and ready to help. What would you like assistance with?",
        "fr": "Je suis là et prêt à vous aider. De quoi avez-vous besoin ?",
        "rw": "Ndi hano kugira ngo ngufashe. (I can reply fully in English or French.)"},
    "thanks": {
        "en": "You're welcome. I'm happy to help. Is there anything else you need?",
        "fr": "Je vous en prie. Avez-vous besoin d'autre chose ?",
        "rw": "Ntacyo! (I can reply fully in English or French.)"},
    "goodbye": {
        "en": "Goodbye, take care. If you ever feel very unwell, use the Emergency button straight away.",
        "fr": "Au revoir, prenez soin de vous. En cas de malaise grave, utilisez tout de suite le bouton Urgence.",
        "rw": "Murabeho! If you feel very unwell, press the Emergency button."},
    "capabilities": {
        "en": "I'm the assistant of this emergency system - not a doctor. I can: explain health topics from trusted "
              "sources (WHO, CDC, NHS and others), help you use the app (emergency requests, tests, results, the "
              "camera heart-rate test), explain your readings in general terms, and tell you when you need urgent "
              "help. I can't diagnose you or prescribe medicine.",
        "fr": "Je suis l'assistant de ce système d'urgence, pas un médecin. Je peux expliquer des sujets de santé à "
              "partir de sources fiables, vous aider à utiliser l'application et vous dire quand demander de l'aide "
              "urgente. Je ne peux pas poser de diagnostic ni prescrire de médicament."},
    "out_of_scope": {
        "en": "I can help with health questions and with using this emergency app. Try asking about a symptom, a "
              "disease, a reading, or how to request help.",
        "fr": "Je peux répondre aux questions de santé et vous aider à utiliser l'application d'urgence."},
    "tech_support": {
        "en": "Let's sort that out. The suggestions below cover the usual fixes. If it still fails, contact the "
              "operator from the app.",
        "fr": "Essayons de régler cela. Les suggestions ci-dessous couvrent les solutions habituelles."},
    "app_help": {
        "en": "Here is where to find that in the app:",
        "fr": "Voici où trouver cela dans l'application :"},
    "operator_support": {
        "en": "Here are the places on the operator dashboard for that. I only show what your account is allowed to "
              "see, and decisions stay with you.",
        "fr": "Voici les sections du tableau de bord opérateur. Les décisions restent les vôtres."},
}

# app_help / tech_support: specific answers by keyword (first match wins), English text.
APP_TOPICS = [
    (r"heart.?rate|pulse|bpm|rythme", "Open Vital signs (or Heart rate) on the home screen and press Heart Rate "
     "Test. The camera opens by itself: cover the rear camera and flashlight with a fingertip and keep still for up to "
     "60 seconds. The result is sent to the operator automatically."),
    (r"emergenc|ambulance|urgence", "Press the red Emergency button on the home screen. Describe what is happening; "
     "your location is shared so the operator can send help. You can follow the ambulance on the tracking screen."),
    (r"result|résultat|history|previous|measurement|reading|vital", "Open Results for test results, or Vital signs "
     "for your heart rate, temperature and blood pressure with history charts."),
    (r"test|mesur", "Open Request test on the home screen, choose the tests and send. An operator accepts the request."),
    (r"hospital|hôpital", "Open Hospitals on the home screen to see nearby hospitals on the map."),
    (r"voice|speak|microphone|talk|parler", "Open the Voice assistant and press the microphone. Speak normally; the "
     "app shows what it understood before answering, and reads the answer aloud."),
    (r"caregiver|family", "Open Caregivers from your profile to invite a family member to follow your alerts."),
    (r"password|mot de passe", "Open Profile then Change password. On the login screen use Forgot password if needed."),
    (r"conversation|chat history|delete", "In the assistant, use New conversation, or the history menu to delete "
     "saved conversations. History is deleted automatically after a while."),
    (r"camera|caméra|flash", "Allow the camera: phone Settings > Apps > Smart Emergency > Permissions > Camera. "
     "Close other apps using the camera, then try the test again. Without a flashlight, use a bright light."),
    (r"micro", "Allow the microphone: phone Settings > Apps > Smart Emergency > Permissions > Microphone. Speak "
     "close to the phone in a quiet place."),
    (r"log ?in|connect|password|forgot", "Check your username and password; use Forgot password on the login screen. "
     "If the app says it cannot reach the server, check your internet connection."),
    (r"internet|network|connection|offline|réseau", "Check mobile data or Wi-Fi. Measurements are kept on the phone "
     "and sent automatically when the connection returns."),
    (r"location|map|gps", "Turn on location: phone Settings > Location, and allow it for Smart Emergency."),
]


def _bundle():
    """The ACTIVE, approved intent model (verified file), or None -> rules only."""
    try:
        from ai_training import registry
        mv, path = registry.active_artifact("intent")
        if path is None:
            return None, None
        cached = _bundle.cache
        if cached and cached[0] == mv.id:
            return mv, cached[1]
        from ai_training.intent import load
        b = load(path)
        _bundle.cache = (mv.id, b)
        return mv, b
    except Exception:
        log.exception("intent model unavailable - rules only")
        return None, None


_bundle.cache = None


def classify(text, profile=None):
    """Returns {intent, confidence, source, model_version}."""
    profile = profile or safety.assess(text)
    t = (text or "").strip()
    if profile["emergency"]:
        return {"intent": "emergency", "confidence": 1.0, "source": "safety_rules", "model_version": None}
    for intent, rx in RULES:
        if len(t) <= 80 and rx.search(t):
            return {"intent": intent, "confidence": 1.0, "source": "rules", "model_version": None}
    mv, bundle = _bundle()
    if bundle is None:
        return {"intent": "medical_info", "confidence": 0.0, "source": "fallback", "model_version": None}
    from ai_training.intent import predict
    from .safety import _INFO_ONLY
    label, p, probs = predict(bundle, t.lower())
    if probs.get("emergency", 0) >= MODEL_EMERGENCY_MIN and not _INFO_ONLY.search(t):
        return {"intent": "emergency", "confidence": round(probs["emergency"], 3), "source": "model",
                "model_version": mv.version}
    if p < MODEL_MIN_CONFIDENCE:
        label = "medical_info" if profile["medical"] else "out_of_scope"
    if label == "out_of_scope" and profile["medical"]:
        label = "medical_info"  # never refuse a question that contains medical words
    return {"intent": label, "confidence": round(p, 3), "source": "model", "model_version": mv.version}


def reply(intent, language, text):
    if intent not in REPLIES:
        return None
    by_lang = REPLIES[intent]
    base = by_lang.get(language) or by_lang["en"]
    if intent in ("app_help", "tech_support", "operator_support"):
        order = APP_TOPICS[9:] + APP_TOPICS[:9] if intent == "tech_support" else APP_TOPICS  # fixes before features
        topic = next((ans for rx, ans in order if re.search(rx, text, re.I)), None)
        if topic:
            base = f"{base}\n\n{topic}" if intent == "app_help" else topic
    return base
