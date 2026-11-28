"""Project-specific labelled examples, written by the project team for this system (not taken from any dataset).

LIMITATIONS (read before trusting the metrics):
* These are hand-written, so they are cleaner and less varied than real user messages. Test scores on them are
  optimistic. Replace / extend them with real, de-identified, reviewed messages when available.
* Kinyarwanda (rw) examples are short common phrases and MUST be reviewed by a native speaker before the model's
  Kinyarwanda performance is reported as reliable.
* No medical diagnosis is learnt here: "medical_info" only means "send this to the knowledge-base search".

Labels
  greeting, how_are_you, thanks, goodbye   small talk
  capabilities                              what can you do / who are you
  app_help                                  how to use a feature of THIS app (request, tests, results, heart rate)
  tech_support                              permissions, connection, login, app problems
  operator_support                          staff questions about emergencies, readings, resources
  emergency                                 the user is reporting / asking for urgent help
  medical_info                              health information question -> knowledge base
  out_of_scope                              anything else
"""

GREETING = [
    "hello", "hi", "hey", "hi there", "hello there", "good morning", "good afternoon", "good evening",
    "hey assistant", "morning", "hiya", "greetings", "hello assistant, are you there?", "hi, anyone there",
    "bonjour", "salut", "bonsoir", "bonjour assistant", "coucou",
    "muraho", "mwaramutse", "mwiriwe", "muraho neza", "bite",
]
HOW_ARE_YOU = [
    "how are you", "how are you doing", "how are you today", "how's it going", "are you ok", "how do you do",
    "what's up", "how have you been", "comment ça va", "ça va ?", "comment allez-vous", "amakuru", "amakuru yawe",
    "umeze ute",
]
THANKS = [
    "thank you", "thanks", "thanks a lot", "thank you so much", "many thanks", "thanks for your help",
    "that was helpful, thanks", "cheers", "great, thank you", "merci", "merci beaucoup", "merci pour votre aide",
    "murakoze", "murakoze cyane", "urakoze",
]
GOODBYE = [
    "bye", "goodbye", "see you", "see you later", "bye bye", "that's all", "that is all for now", "good night",
    "talk to you later", "i'm done", "au revoir", "à bientôt", "bonne nuit", "murabeho", "mwirirwe", "ni aho",
]
CAPABILITIES = [
    "what can you do", "what can you help me with", "who are you", "are you a robot", "are you a doctor",
    "what are you", "how can you help me", "what questions can i ask", "what is this assistant",
    "tell me what you can do", "can you help me", "what services do you offer", "are you a real person",
    "qui êtes-vous", "que pouvez-vous faire", "comment pouvez-vous m'aider", "wowe uri nde", "wamfasha iki",
]
APP_HELP = [
    "how do i request a medical test", "how do i see my results", "where can i find my test results",
    "how do i measure my heart rate", "how do i use the heart rate test", "where is the heart rate test",
    "how do i send an emergency request", "how do i track the ambulance", "how do i add a caregiver",
    "how do i change my password", "where are my previous measurements", "how do i view my vital signs history",
    "how do i find a nearby hospital", "how do i use voice mode", "can i speak instead of typing",
    "how do i contact an operator", "how do i update my profile", "how do i cancel my request",
    "where can i see my emergency history", "how does the temperature test work", "how do i turn on notifications",
    "show me how to use the app", "how do i delete my chat history", "how do i start a new conversation",
    "comment demander un test médical", "où sont mes résultats", "comment mesurer mon rythme cardiaque",
    "comment envoyer une demande d'urgence",
]
TECH_SUPPORT = [
    "the camera is not working", "camera permission denied", "the app can't access the camera",
    "the microphone is not working", "it says microphone permission denied", "i can't log in",
    "i forgot my password", "the app is not loading", "no internet connection error", "the map is not showing",
    "location permission is off", "my measurement was not sent", "it says submission failed",
    "the heart rate test keeps failing", "the flashlight does not turn on", "the app crashed",
    "notifications are not arriving", "voice recognition doesn't understand me", "the page is blank",
    "bluetooth thermometer won't connect", "la caméra ne marche pas", "je n'arrive pas à me connecter",
    "le micro ne fonctionne pas",
]
OPERATOR_SUPPORT = [
    "show me new emergency requests", "which requests are unanswered", "what are the latest readings for this patient",
    "summarise this patient's vital signs", "which ambulance is available", "assign an ambulance to this emergency",
    "is this reading reliable", "review the latest heart rate measurement", "which emergencies are critical",
    "list pending test requests", "who is waiting the longest", "check the patient's measurement history",
    "has the patient's temperature changed", "which hospital has free beds", "follow up on the unanswered request",
    "show the operator dashboard", "verify this abnormal reading", "start the vital signs simulator",
]
EMERGENCY = [
    "i need emergency assistance", "emergency", "this is an emergency", "help me please", "please help",
    "call an ambulance", "i need an ambulance", "send an ambulance", "i need help now", "someone collapsed",
    "my father is not breathing", "she is unconscious", "he fainted and won't wake up", "i have crushing chest pain",
    "i can't breathe", "my child is having a seizure", "there was a car accident", "he is bleeding a lot",
    "i think i'm having a heart attack", "her face is drooping and her speech is slurred", "i took too many pills",
    "i want to kill myself", "my baby is not breathing", "severe allergic reaction, throat swelling",
    "urgent help needed", "i fell and can't get up", "medical emergency at home", "we need a doctor urgently",
    "au secours", "urgence", "aidez-moi", "j'ai besoin d'une ambulance", "il ne respire plus",
    "j'ai une douleur à la poitrine", "mfasha", "ubutabazi", "ndakeneye ubutabazi", "mutabare",
]
MEDICAL_EXTRA = [
    "what is a normal temperature", "what does high blood pressure mean", "what causes fever",
    "is 120/80 normal", "what is malaria", "how is diabetes treated", "what are the symptoms of covid",
    "what does tachycardia mean", "how can i prevent malaria", "what is a heart rate", "what is spo2",
    "why do i have a headache", "what is asthma", "what is hypertension", "explain my blood pressure reading",
    "what does the camera heart rate mean", "is a temperature of 38 a fever", "what is anaemia",
    "qu'est-ce que le paludisme", "quels sont les symptômes du diabète", "c'est quoi la tension artérielle",
    "umuriro ni iki", "malariya ni iki",
]
OUT_OF_SCOPE_EXTRA = [
    "what's the weather tomorrow", "tell me a joke", "who won the football match", "what is the capital of france",
    "book a flight", "play some music", "what is bitcoin worth", "write me a poem", "how do i cook rice",
    "translate hello to spanish", "what time is it", "recommend a movie",
]


def _topic(title):
    """'Urinary tract infection (UTI)' -> 'urinary tract infection'; 'Gastritis and peptic ulcer' -> 'gastritis'."""
    import re
    t = re.sub(r"\(.*?\)", "", title).split(" - ")[0].split(":")[0]
    return re.split(r"\band\b|,", t)[0].strip().lower()


def synthetic_examples(seed=13):
    """TEMPLATE-GENERATED variants (labelled origin 'synthetic:*'). They widen the wording the model sees; they are
    not real user messages, so a model trained on them still needs evaluation on real, reviewed messages."""
    import random

    from medical_rag.ingest import load_corpus
    from medical_rag.knowledge_base import RED_FLAGS
    rng = random.Random(seed)
    rows = []
    # medical_info: questions about every knowledge-base topic (the same topics the search can answer)
    q_templates = ["what is {t}", "tell me about {t}", "what are the symptoms of {t}", "what causes {t}",
                   "how is {t} treated", "how can i prevent {t}", "is {t} dangerous", "explain {t} simply",
                   "qu'est-ce que {t}", "signs of {t}"]
    for d in load_corpus():
        topic = _topic(d["title"])
        for tpl in rng.sample(q_templates, 3):
            rows.append((tpl.format(t=topic), "medical_info", "synthetic:kb-topics"))
    # emergency: first-person / bystander reports built from the existing red-flag list
    who = ["my mother", "my father", "my child", "my husband", "my wife", "a man here", "my friend", "i"]
    e_templates = ["help, {w} {v}", "{w} {v}, what do i do", "please come, {w} {v}", "{w} {v} right now",
                   "urgent: {w} {v}"]
    verbs = {"not breathing": "is not breathing", "unconscious": "is unconscious", "unresponsive": "is unresponsive",
             "chest pain": "has chest pain", "heart attack": "is having a heart attack", "stroke": "is having a stroke",
             "severe bleeding": "has severe bleeding", "bleeding heavily": "is bleeding heavily",
             "choking": "is choking", "seizure": "is having a seizure", "convulsion": "has convulsions",
             "overdose": "took an overdose", "poison": "swallowed poison", "collapsed": "collapsed",
             "can't breathe": "can't breathe", "blue lips": "has blue lips"}
    for flag in RED_FLAGS:
        if flag not in verbs:
            continue
        for tpl in rng.sample(e_templates, 2):
            w = rng.choice(who)
            v = verbs[flag].replace("is ", "am ", 1) if w == "i" and verbs[flag].startswith("is ") else verbs[flag]
            v = v.replace("has ", "have ", 1) if w == "i" else v
            rows.append((tpl.format(w=w, v=v), "emergency", "synthetic:red-flags"))
    # app_help / tech_support / operator_support / how_are_you: feature x phrasing
    features = ["heart rate test", "vital signs", "test results", "emergency request", "ambulance tracking",
                "voice assistant", "hospital map", "my profile", "caregivers", "temperature test", "notifications"]
    for f in features:
        for tpl in rng.sample(["how do i use the {f}", "where is the {f}", "how does the {f} work",
                               "can you show me the {f}", "i can't find the {f}", "how to open {f}"], 3):
            rows.append((tpl.format(f=f), "app_help", "synthetic:features"))
        for tpl in rng.sample(["the {f} is not working", "the {f} shows an error", "the {f} won't load",
                               "the {f} keeps crashing"], 2):
            rows.append((tpl.format(f=f), "tech_support", "synthetic:features"))
    for x in ["patient readings", "new emergencies", "unanswered requests", "ambulance availability",
              "abnormal readings", "test requests", "hospital capacity", "heart rate submissions"]:
        for tpl in rng.sample(["show me the {x}", "list the {x}", "any {x} to review", "summarise the {x}",
                               "check the {x}"], 3):
            rows.append((tpl.format(x=x), "operator_support", "synthetic:operator"))
    for g in ["how are you", "how are you doing", "how's your day", "are you doing well", "how are things"]:
        for p in ["", "hi, ", "hello, ", "good morning, ", "hey "]:
            rows.append((p + g, "how_are_you", "synthetic:smalltalk"))
    return rows


def examples():
    from medical_rag.evaluation import CASES
    from medical_rag.knowledge_base import RED_FLAGS
    rows = []
    for label, items in (("greeting", GREETING), ("how_are_you", HOW_ARE_YOU), ("thanks", THANKS),
                         ("goodbye", GOODBYE), ("capabilities", CAPABILITIES), ("app_help", APP_HELP),
                         ("tech_support", TECH_SUPPORT), ("operator_support", OPERATOR_SUPPORT),
                         ("emergency", EMERGENCY), ("medical_info", MEDICAL_EXTRA),
                         ("out_of_scope", OUT_OF_SCOPE_EXTRA)):
        rows += [(t, label, "project") for t in items]
    # existing assistant evaluation questions: emergency reports vs. health-information questions
    for _cat, q, exp in CASES:
        if exp.get("unknown"):
            continue
        rows.append((q, "emergency" if exp.get("emergency") else "medical_info", "project-eval"))
    # existing red-flag phrases used as first-person reports
    for p in RED_FLAGS:
        rows.append((f"help, {p} right now", "emergency", "project-redflags"))
    return rows + synthetic_examples()
