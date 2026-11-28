"""Conversation layer, recommendations, feedback, model registry and the offline policy."""
import uuid

from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from ai_training import policy, registry
from . import conversation, recommendations, retriever, safety, service
from .models import AssistantFeedback, ChatMessage, ModelVersion

# Hand-written challenge set (never used for training): (message, expected intent, expected emergency banner)
CHALLENGE = [
    ("Hello", "greeting", False), ("hi there", "greeting", False), ("Good evening", "greeting", False),
    ("How are you?", "how_are_you", False), ("Thank you", "thanks", False), ("thanks a lot!", "thanks", False),
    ("Goodbye", "goodbye", False), ("What can you do?", "capabilities", False), ("Are you a doctor?", "capabilities", False),
    ("Bonjour", "greeting", False), ("Merci beaucoup", "thanks", False), ("Muraho", "greeting", False),
    ("Murakoze", "thanks", False),
    ("I need emergency assistance", "emergency", True), ("Please help", "emergency", True),
    ("call an ambulance now", "emergency", True), ("Au secours", "emergency", True),
    ("my dad collapsed and is not breathing", "emergency", True), ("ndakeneye ubutabazi", "emergency", True),
    ("What is malaria?", "medical_info", False), ("What is a normal heart rate?", "medical_info", False),
]


class ConversationRulesTests(TestCase):
    """Rules only (no approved model in the test database) - the assistant must work without any model."""

    def test_challenge_set(self):
        wrong = []
        for text, intent, emergency in CHALLENGE:
            r = retriever.answer(text, use_llm=False, role="patient")
            got = r["intent"]["intent"]
            if got != intent or r["emergency"] != emergency:
                wrong.append((text, got, r["emergency"]))
        self.assertEqual(wrong, [])

    def test_greeting_is_not_refused(self):
        r = retriever.answer("Hello", use_llm=False)
        self.assertEqual(r["mode"], "conversation")
        self.assertIn("Welcome", r["answer"])
        self.assertNotIn("I can only answer", r["answer"])

    def test_reply_in_users_language(self):
        self.assertIn("Bonjour", retriever.answer("Bonjour", use_llm=False)["answer"])
        self.assertEqual(safety.detect_language("Muraho"), "rw")
        self.assertEqual(safety.detect_language("Bonjour"), "fr")

    def test_greeting_plus_symptom_goes_to_knowledge_base(self):
        r = retriever.answer("hi, what is malaria?", use_llm=False)
        self.assertNotEqual(r["mode"], "conversation")

    def test_emergency_request_text_and_pinned_suggestions(self):
        r = retriever.answer("I need emergency assistance", use_llm=False, role="patient")
        self.assertTrue(r["emergency"])
        self.assertIn("Emergency button", r["answer"])
        self.assertEqual(r["suggestions"][0]["id"], "emergency_button")
        self.assertTrue(r["suggestions"][0]["pinned"])

    def test_asking_about_emergency_topic_is_not_an_emergency(self):
        r = retriever.answer("What are the signs of a stroke?", use_llm=False)
        self.assertFalse(r["emergency"])

    def test_help_understanding_is_not_an_emergency(self):
        r = retriever.answer("please help me understand malaria", use_llm=False)
        self.assertFalse(r["emergency"])

    def test_medication_request_never_small_talk(self):
        r = retriever.answer("thanks, what dose of paracetamol should i take", use_llm=False)
        self.assertNotEqual(r["mode"], "conversation")
        self.assertIn("personal dose", r["answer"])


class RecommendationTests(TestCase):
    def test_role_filtering(self):
        staff = {s["id"] for s in recommendations.suggest("medical_info", "operator")}
        self.assertNotIn("heart_rate_test", staff)
        self.assertIn("review_new_requests", staff)
        patient = {s["id"] for s in recommendations.suggest("operator_support", "patient")}
        self.assertFalse(patient & {"review_new_requests", "check_patient_vitals"})

    def test_heart_question_suggests_camera_test(self):
        s = recommendations.suggest("medical_info", "patient", text="what is a normal heart rate")
        self.assertEqual(s[0]["id"], "heart_rate_test")

    def test_no_repetition(self):
        first = [s["id"] for s in recommendations.suggest("greeting", "patient")]
        again = [s["id"] for s in recommendations.suggest("greeting", "patient", recently_shown=first)]
        self.assertFalse(set(first) & set(again))

    def test_emergency_pins_never_removed_by_repetition(self):
        s = recommendations.suggest("emergency", "patient", True, recently_shown=["emergency_button"])
        self.assertEqual(s[0]["id"], "emergency_button")


class FeedbackTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_data", verbosity=0)

    def setUp(self):
        self.p1 = User.objects.get(username="patient1")
        self.p2 = User.objects.get(username="patient2")
        self.api = APIClient()
        self.api.force_authenticate(self.p1)

    def test_rate_saved_answer_and_idempotent(self):
        d = self.api.post("/api/assistant/ask/", {"question": "Hello"}, format="json").json()
        self.assertTrue(d["response_id"])
        self.assertEqual(d["mode"], "conversation")
        body = {"response_id": d["response_id"], "message_id": d["message_id"], "rating": "helpful"}
        self.assertEqual(self.api.post("/api/assistant/feedback/", body, format="json").status_code, 201)
        self.assertEqual(self.api.post("/api/assistant/feedback/", body, format="json").status_code, 200)
        fb = AssistantFeedback.objects.get()
        self.assertEqual(fb.intent, "greeting")
        self.assertTrue(fb.suggestions_shown)

    def test_cannot_rate_someone_elses_message(self):
        d = self.api.post("/api/assistant/ask/", {"question": "Hello"}, format="json").json()
        other = APIClient()
        other.force_authenticate(self.p2)
        r = other.post("/api/assistant/feedback/", {"response_id": d["response_id"], "message_id": d["message_id"],
                                                     "rating": "unhelpful"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_rejects_bad_input(self):
        for body in ({"response_id": "x", "rating": "helpful"},
                     {"response_id": str(uuid.uuid4()), "rating": "love"},
                     {"response_id": str(uuid.uuid4()), "rating": "suggestion", "suggestion": "dispatch_ambulance"}):
            self.assertEqual(self.api.post("/api/assistant/feedback/", body, format="json").status_code, 400)

    def test_web_feedback_anonymous_stores_no_text(self):
        r = self.client.post("/assistant/feedback/", {"response_id": str(uuid.uuid4()), "rating": "unclear"},
                             content_type="application/json")
        self.assertEqual(r.status_code, 201)
        fb = AssistantFeedback.objects.get()
        self.assertIsNone(fb.message)
        self.assertEqual(fb.role, "anonymous")

    def test_feedback_deleted_with_history(self):
        d = self.api.post("/api/assistant/ask/", {"question": "Hello"}, format="json").json()
        self.api.post("/api/assistant/feedback/", {"response_id": d["response_id"], "message_id": d["message_id"],
                                                   "rating": "helpful"}, format="json")
        service.delete_conversations(self.p1)
        self.assertFalse(ChatMessage.objects.exists())
        self.assertFalse(AssistantFeedback.objects.exists())


class RegistryAndPolicyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_data", verbosity=0)

    def _fake(self, kind, passed, tmp_name):
        p = registry.MODELS_DIR / tmp_name
        registry.MODELS_DIR.mkdir(parents=True, exist_ok=True)
        p.write_text('{"scores": {"view_vitals": 0.9}}', encoding="utf-8")
        self.addCleanup(lambda: p.unlink(missing_ok=True))
        return registry.register(kind, tmp_name, p, {}, {"passed": passed}, {})

    def test_gate_admin_and_rollback(self):
        admin, patient = User.objects.get(username="admin"), User.objects.get(username="patient1")
        bad = self._fake("recommendation", False, "test-bad.json")
        with self.assertRaises(registry.RegistryError):
            registry.approve(bad, admin)
        good1 = self._fake("recommendation", True, "test-good1.json")
        with self.assertRaises(registry.RegistryError):
            registry.approve(good1, patient)
        registry.approve(good1, admin)
        good2 = self._fake("recommendation", True, "test-good2.json")
        registry.approve(good2, admin)
        self.assertEqual(ModelVersion.objects.get(pk=good1.pk).status, "retired")
        registry.rollback("recommendation", admin)
        self.assertEqual(ModelVersion.objects.get(pk=good1.pk).status, "active")
        self.assertEqual(ModelVersion.objects.get(pk=good2.pk).status, "rejected")

    def test_tampered_file_is_ignored(self):
        admin = User.objects.get(username="admin")
        mv = self._fake("recommendation", True, "test-tamper.json")
        registry.approve(mv, admin)
        (registry.MODELS_DIR / "test-tamper.json").write_text('{"scores": {"emergency_button": 0}}', encoding="utf-8")
        registry._clear_cache()
        self.assertEqual(registry.active_artifact("recommendation"), (None, None))

    def test_policy_never_learns_from_emergencies_or_pinned(self):
        rows = [{"rating": "unhelpful", "suggestion": "", "suggestions_shown": ["emergency_button"], "emergency": True}] * 50
        rows += [{"rating": "suggestion", "suggestion": "emergency_button", "suggestions_shown": [], "emergency": False}] * 5
        rows += [{"rating": "suggestion", "suggestion": "view_vitals", "suggestions_shown": ["view_vitals"],
                  "emergency": False}] * 25
        path, _v, metrics, gate = policy.train(rows)
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        self.assertNotIn("emergency_button", metrics["per_suggestion"])
        self.assertEqual(metrics["skipped_emergency_answers"], 50)
        self.assertTrue(gate["passed"])


class ClassifierCannotRemoveEmergencyTests(TestCase):
    def test_rules_win(self):
        prof = safety.assess("my baby is not breathing")
        self.assertTrue(prof["emergency"])
        self.assertEqual(conversation.classify("my baby is not breathing", prof)["source"], "safety_rules")
