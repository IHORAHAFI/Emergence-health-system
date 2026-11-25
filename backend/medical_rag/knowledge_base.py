"""
Curated general first-aid / health-information documents for the assistant.

Each entry is a short plain-language summary of widely published public guidance.
The `source` links point to the organisation's public pages on that topic; summaries
were written for this project and should be reviewed by a clinician before any real
deployment. To extend the knowledge base, add entries here (or load them from files)
- the retriever indexes whatever is in DOCUMENTS.
"""

WHO = ("World Health Organization", "https://www.who.int/health-topics")
NHS = ("NHS (UK National Health Service)", "https://www.nhs.uk/conditions/")
IFRC = ("IFRC First Aid guidance", "https://www.ifrc.org/our-work/health-and-care/community-health/first-aid")
AHA = ("American Heart Association - CPR", "https://cpr.heart.org/")

DOCUMENTS = [
    {
        "id": "heart-attack", "title": "Heart attack warning signs",
        "keywords": "heart attack chest pain pressure tightness arm jaw cardiac myocardial infarction",
        "emergency": True, "source": NHS,
        "text": "Common signs of a heart attack are chest pain or pressure that may spread to the arms, "
                "jaw, neck or back, shortness of breath, sweating, nausea and light-headedness. Symptoms "
                "can be milder in women, older people and people with diabetes. A suspected heart attack "
                "is an emergency: request an ambulance immediately, stop activity and sit down, and loosen "
                "tight clothing. Do not drive yourself to hospital.",
    },
    {
        "id": "cpr", "title": "Unresponsive and not breathing normally (CPR)",
        "keywords": "cpr unconscious unresponsive not breathing collapse cardiac arrest chest compressions defibrillator aed",
        "emergency": True, "source": AHA,
        "text": "If a person is unresponsive and not breathing normally, call for emergency help at once and "
                "start chest compressions: push hard and fast in the centre of the chest, about 100 to 120 "
                "compressions per minute, letting the chest come back up between pushes. If an automated "
                "external defibrillator (AED) is available, switch it on and follow its spoken instructions. "
                "Continue until help arrives or the person starts breathing normally.",
    },
    {
        "id": "stroke", "title": "Stroke - recognise it FAST",
        "keywords": "stroke face drooping arm weakness speech slurred numbness sudden confusion fast",
        "emergency": True, "source": NHS,
        "text": "Use FAST: Face - has it dropped on one side? Arms - can they raise both arms? Speech - is it "
                "slurred or confused? Time - request emergency help immediately. Note the time symptoms "
                "started, because some treatments only work within a few hours. Do not give food or drink.",
    },
    {
        "id": "bleeding", "title": "Severe bleeding",
        "keywords": "bleeding blood wound cut haemorrhage hemorrhage injury pressure bandage",
        "emergency": True, "source": IFRC,
        "text": "For heavy bleeding, press firmly on the wound with a clean cloth or dressing and keep "
                "pressing. If blood soaks through, add more cloth on top rather than removing the first. "
                "Keep the person lying down and warm. Severe bleeding is an emergency - request help "
                "immediately.",
    },
    {
        "id": "burns", "title": "Burns and scalds",
        "keywords": "burn burns scald fire hot water chemical skin blister",
        "emergency": False, "source": NHS,
        "text": "Cool a burn under cool or lukewarm running water for 20 minutes as soon as possible. Remove "
                "clothing or jewellery near the burn unless it is stuck to the skin. Cover loosely with cling "
                "film or a clean plastic bag. Do not use ice, butter or creams. Large, deep, electrical or "
                "chemical burns, and burns to the face, hands, feet or genitals, need emergency care.",
    },
    {
        "id": "choking", "title": "Choking (adult)",
        "keywords": "choking choke airway blocked cannot breathe food stuck throat back blows abdominal thrusts",
        "emergency": True, "source": NHS,
        "text": "If the person can cough, encourage them to keep coughing. If they cannot cough, speak or "
                "breathe, give up to 5 firm back blows between the shoulder blades, then up to 5 abdominal "
                "thrusts. Request emergency help if the blockage does not clear, and start CPR if they "
                "become unresponsive.",
    },
    {
        "id": "breathing", "title": "Breathing difficulty and asthma attack",
        "keywords": "breathing difficulty shortness of breath asthma wheeze inhaler respiratory breathless",
        "emergency": True, "source": NHS,
        "text": "During an asthma attack, sit upright, take slow steady breaths and use the reliever (usually "
                "blue) inhaler as prescribed. Severe breathlessness, blue or grey lips, being unable to "
                "speak in full sentences, or confusion are emergency signs - request an ambulance "
                "immediately.",
    },
    {
        "id": "poisoning", "title": "Poisoning",
        "keywords": "poison poisoning swallowed overdose chemical medicine pesticide toxic",
        "emergency": True, "source": WHO,
        "text": "If someone may have swallowed poison or too much medicine, request emergency help and keep "
                "the container or packet to show responders. Do not make the person vomit unless a medical "
                "professional tells you to. If they are unresponsive but breathing, place them on their side "
                "in the recovery position.",
    },
    {
        "id": "pregnancy", "title": "Pregnancy warning signs",
        "keywords": "pregnancy pregnant labour labor bleeding contractions waters broke baby maternity",
        "emergency": True, "source": WHO,
        "text": "Heavy vaginal bleeding, severe abdominal pain, fits (convulsions), severe headache with "
                "blurred vision, fever, or reduced baby movements during pregnancy need urgent medical care. "
                "Regular strong contractions or waters breaking mean labour may have started - contact the "
                "maternity service or request help.",
    },
    {
        "id": "fracture", "title": "Suspected broken bone",
        "keywords": "fracture broken bone fall injury swelling deformity sprain",
        "emergency": False, "source": IFRC,
        "text": "Keep the injured part still and supported in the position found. Apply a cold pack wrapped "
                "in cloth to reduce swelling. Do not try to straighten it. Request emergency help for open "
                "wounds over the bone, suspected neck or back injury, or if the limb is cold, pale or numb.",
    },
    {
        "id": "fever-malaria", "title": "Fever and malaria",
        "keywords": "fever malaria high temperature chills mosquito shivering",
        "emergency": False, "source": WHO,
        "text": "Fever after possible mosquito exposure can be malaria, which should be tested promptly at a "
                "health facility. Seek urgent care for fever with confusion, fits, difficulty breathing, "
                "repeated vomiting, very dark urine, or for fever in young children and pregnant women.",
    },
    {
        "id": "recovery-position", "title": "Recovery position",
        "keywords": "recovery position unconscious breathing side lying vomit airway",
        "emergency": False, "source": IFRC,
        "text": "If a person is unresponsive but breathing normally and has no suspected spinal injury, roll "
                "them onto their side with the top leg bent and head tilted back so the airway stays open "
                "and fluids can drain. Keep checking their breathing until help arrives.",
    },
]

# Words that should trigger an immediate "request emergency help" banner,
# regardless of what the retriever finds.
RED_FLAGS = [
    "not breathing", "unconscious", "unresponsive", "chest pain", "heart attack", "stroke",
    "severe bleeding", "bleeding heavily", "choking", "seizure", "fitting", "convulsion",
    "overdose", "poison", "suicide", "can't breathe", "cannot breathe", "blue lips", "collapsed",
]
