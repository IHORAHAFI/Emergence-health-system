import json
from types import SimpleNamespace
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import ingest, llm, retriever, safety, service
from .models import ChatMessage, Conversation, KnowledgeDocument


class RetrieverTests(TestCase):
    def test_retrieves_relevant_document(self):
        docs = retriever.retrieve("what are the signs of a stroke face drooping")
        self.assertIn(docs[0][1]["id"], {"stroke", "stroke-condition"})

    def test_answer_has_sources_and_disclaimer(self):
        a = retriever.answer("How do I treat a burn from hot water?")
        self.assertTrue(a["ok"])
        self.assertEqual(a["mode"], "extractive")
        self.assertTrue(a["sources"][0]["url"].startswith("https://"))
        self.assertIn("not a diagnosis", a["disclaimer"])

    def test_red_flags_trigger_emergency(self):
        a = retriever.answer("my father is unconscious and not breathing")
        self.assertTrue(a["emergency"])
        self.assertTrue(a["answer"].startswith("This may be a medical emergency"))
        self.assertEqual(a["sources"][0]["doc_id"], "cpr")

    def test_unknown_topic_does_not_invent(self):
        a = retriever.answer("quantum chromodynamics lattice gauge")
        self.assertEqual(a["mode"], "none")
        self.assertEqual(a["sources"], [])

    def test_empty_question(self):
        self.assertFalse(retriever.answer("  ")["ok"])

    def test_retriever_failure_is_handled(self):
        with mock.patch.object(retriever, "retrieve", side_effect=RuntimeError("index broken")):
            a = retriever.answer("chest pain")
        self.assertFalse(a["ok"])
        self.assertTrue(a["emergency"])


class KnowledgeBaseTests(TestCase):
    """Categories 1-4 of the evaluation plan: terminology, diseases, symptoms, treatment."""

    def top(self, q):
        return retriever.answer(q)["sources"][0]["doc_id"]

    def test_coverage_of_required_examples(self):
        cases = {
            "What is malaria?": "malaria",
            "What causes chest pain?": "symptom-chest-pain",
            "What is the difference between hypertension and hypotension?": "blood-pressure",
            "What is a normal heart rate?": "heart-rate",
            "What does low oxygen saturation mean?": "oxygen-saturation",
            "What are the symptoms of diabetes?": "diabetes",
            "When should I seek emergency medical assistance?": "when-to-seek-emergency",
            "what does -itis mean": "medical-word-parts",
            "What does BP stand for": "medical-abbreviations",
            "how is tuberculosis tested": "tuberculosis",
            "what is a CBC blood test": "common-lab-tests",
        }
        for q, doc in cases.items():
            with self.subTest(q=q):
                self.assertEqual(self.top(q), doc)

    def test_everyday_language_and_typos(self):
        self.assertEqual(self.top("diabetis symtoms"), "diabetes")
        self.assertEqual(self.top("what causes high blood pressure"), "hypertension")
        self.assertIn(self.top("my tummy hurts"), {"symptom-abdominal-pain", "gastritis-ulcer"})

    def test_answer_leads_with_requested_section(self):
        a = retriever.answer("What are the symptoms of diabetes?")
        self.assertTrue(a["answer"].startswith("Symptoms:"))

    def test_follow_up_uses_previous_topic(self):
        a = retriever.answer("what about prevention?", history=[{"role": "user", "text": "What is malaria?"}])
        self.assertEqual(a["sources"][0]["doc_id"], "malaria")
        self.assertIn("mosquito net", a["answer"])

    def test_sources_carry_provenance(self):
        s = retriever.answer("What is malaria?")["sources"][0]
        self.assertEqual(s["source"], "WHO fact sheet - Malaria")
        self.assertFalse(s["reviewed"])  # corpus summaries start as pending review
        self.assertIn("summary_version", s)


class SafetyTests(TestCase):
    """Categories 5-9: medication safety, emergencies, insufficient info, out of scope, languages."""

    def test_personal_dose_is_refused(self):
        a = retriever.answer("How many mg of paracetamol should I take?")
        self.assertEqual(a["safety"]["medication"], "dose")
        self.assertTrue(a["answer"].startswith("I can't tell you a personal dose"))

    def test_stop_medicine_is_refused(self):
        a = retriever.answer("Can I stop taking my blood pressure tablets?")
        self.assertEqual(a["safety"]["medication"], "stop_change")
        self.assertIn("do not stop", a["answer"])

    def test_prescribing_is_refused(self):
        self.assertEqual(safety.assess("Which antibiotic should I take for my cough?")["medication"], "prescribe")

    def test_diagnosis_request(self):
        a = retriever.answer("I have a fever and headache, do I have malaria?")
        self.assertTrue(a["safety"]["diagnosis_request"])
        self.assertIn("can't diagnose", a["answer"])

    def test_reported_vs_asked_emergency(self):
        self.assertTrue(retriever.answer("I have crushing chest pain right now")["emergency"])
        info = retriever.answer("What causes chest pain?")
        self.assertFalse(info["emergency"])
        self.assertTrue(info["safety"]["emergency_topic"])
        self.assertIn("emergency", info["answer"].lower())  # warning signs are still part of the answer

    def test_emergency_first_no_questions(self):
        a = retriever.answer("someone collapsed and is not breathing")
        self.assertTrue(a["emergency"])
        self.assertEqual(a["follow_up"], [])

    def test_insufficient_information_asks_follow_up(self):
        a = retriever.answer("I feel dizzy")
        self.assertTrue(a["follow_up"])

    def test_out_of_scope_and_not_found(self):
        self.assertEqual(retriever.answer("who won the football match")["answer"], safety.OUT_OF_SCOPE_TEXT)
        a = retriever.answer("what is the treatment for progeria disease")
        self.assertIn(a["mode"], {"none", "extractive"})
        if a["mode"] == "none":
            self.assertEqual(a["answer"], safety.NOT_FOUND_TEXT)

    def test_languages(self):
        self.assertEqual(safety.detect_language("Qu'est-ce que le paludisme? J'ai de la fièvre"), "fr")
        a = retriever.answer("Qu'est-ce que le paludisme ?")
        self.assertEqual(a["sources"][0]["doc_id"], "malaria")
        self.assertIn("anglais", a["answer"])  # extractive mode says the answer is in English
        self.assertTrue(safety.assess("J'ai une douleur thoracique")["emergency"])


class IngestionTests(TestCase):
    def test_idempotent_and_versioned(self):
        retriever.answer("malaria")  # auto-ingests an empty database
        self.assertGreater(KnowledgeDocument.objects.count(), 60)
        self.assertEqual(ingest.ingest()["created"], 0)
        doc = KnowledgeDocument.objects.get(slug="malaria")
        doc.review_status, doc.reviewed_at = "approved", timezone.now()
        doc.content_hash = "changed"
        doc.save()
        self.assertEqual(ingest.ingest()["updated"], 1)
        doc.refresh_from_db()
        self.assertEqual((doc.summary_version, doc.review_status), (2, "pending"))  # changed text is re-reviewed
        self.assertTrue(doc.chunks.filter(heading="Prevention").exists())

    def test_parser_requires_provenance(self):
        with self.assertRaises(ValueError):
            ingest.parse_corpus_text("---\nid: x\ntitle: X\n---\n## A\ntext")

    def test_retired_documents_are_not_used(self):
        retriever.answer("malaria")
        KnowledgeDocument.objects.filter(slug="malaria").update(review_status="retired", updated_at=timezone.now())
        retriever.invalidate()
        self.assertNotIn("malaria", [s["doc_id"] for s in retriever.answer("What is malaria?")["sources"]])

    def test_management_command(self):
        call_command("ingest_medical_docs", verbosity=0)
        self.assertTrue(KnowledgeDocument.objects.filter(origin="corpus").exists())


class LlmPathTests(TestCase):
    def fake(self, text, stop="end_turn"):
        return SimpleNamespace(stop_reason=stop, stop_details=None,
                               content=[SimpleNamespace(type="text", text=text)])

    def run_with(self, response):
        client = mock.Mock()
        client.beta.messages.create.return_value = response
        with mock.patch.object(llm, "available", return_value=True), mock.patch.object(llm, "_get_client", return_value=client):
            return retriever.answer("What is malaria?"), client

    def test_grounded_answer_keeps_only_valid_citations(self):
        a, client = self.run_with(self.fake("Malaria is a parasite infection [1]. Made up [9]."))
        self.assertEqual(a["mode"], "llm_grounded")
        self.assertNotIn("[9]", a["answer"])
        self.assertEqual([s["n"] for s in a["sources"]], [1])
        kwargs = client.beta.messages.create.call_args.kwargs
        self.assertEqual(kwargs["model"], llm.model_name())
        self.assertIn("Malaria", kwargs["messages"][-1]["content"])  # retrieved context is sent

    def test_refusal_falls_back_to_extractive(self):
        a, _ = self.run_with(self.fake("", stop="refusal"))
        self.assertEqual(a["mode"], "extractive")


class ServiceAndViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_data", verbosity=0)

    def setUp(self):
        from accounts.models import User
        self.a = User.objects.get(username="patient1")
        self.b = User.objects.get(username="patient2")
        self.op = User.objects.get(username="operator")

    def post(self, **body):
        return self.client.post(reverse("assistant_ask"), json.dumps(body), content_type="application/json")

    def test_page_and_api(self):
        self.assertEqual(self.client.get(reverse("assistant")).status_code, 200)
        r = self.post(question="choking adult")
        self.assertEqual(r.json()["sources"][0]["title"], "Choking (adult)")

    def test_get_not_allowed(self):
        self.assertEqual(self.client.get(reverse("assistant_ask")).status_code, 405)

    def test_anonymous_conversations_are_not_stored(self):
        self.post(question="What is malaria?")
        self.assertEqual(ChatMessage.objects.count(), 0)

    def test_history_is_private_and_deletable(self):
        self.client.force_login(self.a)
        cid = self.post(question="What is malaria?").json()["conversation_id"]
        self.post(question="what about prevention?", conversation_id=cid)
        self.assertEqual(Conversation.objects.get(pk=cid).messages.count(), 4)
        self.client.force_login(self.b)
        self.assertEqual(self.client.get(reverse("assistant_history_item", args=[cid])).status_code, 404)
        self.assertEqual(self.post(question="hi", conversation_id=cid).status_code, 403)
        self.client.force_login(self.a)
        self.assertEqual(len(self.client.get(reverse("assistant_history")).json()["conversations"]), 1)
        self.client.delete(reverse("assistant_history"))
        self.assertFalse(Conversation.objects.filter(user=self.a).exists())

    def test_history_switch_off(self):
        self.client.force_login(self.a)
        self.post(question="What is malaria?", save_history=False)
        self.assertFalse(Conversation.objects.exists())

    def test_purge(self):
        c = Conversation.objects.create(user=self.a, title="old")
        Conversation.objects.filter(pk=c.pk).update(updated_at=timezone.now() - timezone.timedelta(days=90))
        self.assertGreaterEqual(service.purge_old(30), 1)

    def test_vitals_only_when_patient_opts_in(self):
        from patient_monitoring.models import VitalSignReading
        VitalSignReading.objects.create(patient=self.a, source="simulated", measured_at=timezone.now(), spo2_pct=89)
        with mock.patch.object(retriever.llm, "generate", return_value=None) as gen:
            self.client.force_login(self.a)
            self.post(question="what does low oxygen saturation mean", save_history=False)
            self.assertEqual(gen.call_args.args[4], "")
            r = self.post(question="what does low oxygen saturation mean", include_vitals=True, save_history=False)
            self.assertIn("SpO2 89%", gen.call_args.args[4])
            self.assertIn("SIMULATED", gen.call_args.args[4])
            self.assertTrue(r.json()["used_vitals"])

    def test_staff_patient_context_requires_authorisation_and_is_audited(self):
        from emergency import services as em_services
        from patient_monitoring.models import AccessAuditLog
        em = em_services.create_emergency(self.a, {"emergency_type": "respiratory", "latitude": -1.95,
                                                   "longitude": 30.06, "breathing_difficulty": True})
        self.client.force_login(self.b)  # another patient
        self.assertEqual(self.post(question="explain these vitals", emergency_ref=em.reference).status_code, 403)
        self.client.force_login(self.op)
        self.assertEqual(self.post(question="explain these vitals", emergency_ref=em.reference,
                                   save_history=False).status_code, 200)
        self.assertTrue(AccessAuditLog.objects.filter(user=self.op, action="assistant_vitals_context").exists())

    def test_mobile_api(self):
        from rest_framework.test import APIClient
        api = APIClient()
        api.force_authenticate(self.a)
        r = api.post("/api/assistant/ask/", {"question": "What is malaria?"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["conversation_id"])
        self.assertEqual(len(api.get("/api/assistant/history/").data["conversations"]), 1)
