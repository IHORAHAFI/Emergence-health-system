import json
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from . import retriever


class RetrieverTests(TestCase):
    def test_retrieves_relevant_document(self):
        docs = retriever.retrieve("what are the signs of a stroke face drooping")
        self.assertEqual(docs[0][1]["id"], "stroke")

    def test_answer_has_sources_and_disclaimer(self):
        a = retriever.answer("How do I treat a burn from hot water?")
        self.assertTrue(a["ok"])
        self.assertEqual(a["mode"], "extractive")
        self.assertTrue(a["sources"][0]["url"].startswith("https://"))
        self.assertIn("not a diagnosis", a["disclaimer"])

    def test_red_flags_trigger_emergency(self):
        a = retriever.answer("my father is unconscious and not breathing")
        self.assertTrue(a["emergency"])

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


class AssistantViewTests(TestCase):
    def test_page_and_api(self):
        self.assertEqual(self.client.get(reverse("assistant")).status_code, 200)
        r = self.client.post(reverse("assistant_ask"), json.dumps({"question": "choking adult"}),
                             content_type="application/json")
        self.assertEqual(r.json()["sources"][0]["title"], "Choking (adult)")

    def test_get_not_allowed(self):
        self.assertEqual(self.client.get(reverse("assistant_ask")).status_code, 405)
