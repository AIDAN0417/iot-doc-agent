"""Evaluation-fixture semantics and visible application controls."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from src.config import Settings
from src.eval.run_eval import check_answer, summarize


class EvaluationTests(unittest.TestCase):
    """Keep evaluation honest about citations, language balance and refusal behavior."""

    def test_questions_balanced_and_expected_sources(self) -> None:
        """The fixed fixture contains thirty distinct questions, equally split by language."""
        questions = json.loads(Settings().questions_path.read_text(encoding="utf-8"))
        self.assertEqual(len(questions), 30)
        self.assertEqual(sum(q["language"] == "zh" for q in questions), 15)
        self.assertEqual(len({q["id"] for q in questions}), 30)

    def test_expected_citation_and_facts_both_required(self) -> None:
        """A matching keyword without the expected source does not pass evaluation."""
        settings = Settings()
        question = {"source": "api-reference/system/sleep_modes.html", "facts": [["microseconds"]]}
        url = settings.docs_root + question["source"] + "#timer"
        source = {"url": url}
        self.assertTrue(check_answer(question, "microseconds [Timer](" + url + ")", [source], settings)["passed"])
        self.assertFalse(check_answer(question, "microseconds [Wrong](https://invented.example)", [source], settings)["passed"])

    def test_refusal_is_distinguished_from_answered_questions(self) -> None:
        """Expected unsupported questions pass on honest refusal without fabricated links."""
        settings = Settings()
        question = {"facts": [], "expect_refusal": True}
        checks = check_answer(question, settings.answer_refusal_en, [], settings)
        self.assertTrue(checks["passed"])
        self.assertTrue(checks["refused"])

    def test_related_api_name_cannot_inflate_evaluation_score(self) -> None:
        """An API prefix alone does not count as the required complete function name."""
        settings = Settings()
        question = {"source": "api-reference/system/wdts.html", "facts": [["esp_task_wdt_delete"]]}
        url = settings.docs_root + question["source"]
        answer = "esp_task_wdt_delete_user [Watchdogs](" + url + ")"
        self.assertFalse(check_answer(question, answer, [{"url": url}], settings)["passed"])

    def test_streamlit_first_screen_and_clear_control(self) -> None:
        """The real app renders model status and a clear-history control without models loaded."""
        from streamlit.testing.v1 import AppTest
        with patch("src.bootstrap.detect_memory_gib", return_value=32), patch("requests.Session.get", side_effect=ConnectionError("offline")):
            # Simulate a requests-layer offline error, not a cloud connection.
            import requests
            with patch("requests.Session.get", side_effect=requests.ConnectionError("offline")):
                app = AppTest.from_file(str(Settings().project_root / "src" / "app.py")).run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.title[0].value, "ESP32 文档助手")
        self.assertTrue(any("qwen2.5:7b-instruct" in item.value for item in app.caption))
        self.assertTrue(any(button.label == "清空对话" for button in app.button))


if __name__ == "__main__":
    unittest.main()
