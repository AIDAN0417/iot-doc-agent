"""Strict protocol, tool values, conversation state, budgets and local retry checks."""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

import requests

from src.agent.llm import Completion, OllamaClient
from src.agent.loop import Agent, parse_action, strict_json
from src.agent.prompts import build_messages, normalize_code_layout, resolve_citations, validate_answer
from src.agent.tools import estimate_power
from src.config import Settings
from src.rag.answer import answer_question

SOURCE = {"url": "https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/system/sleep_modes.html#timer",
          "section_path": ["Sleep Modes", "Timer"], "text": "Timer wakeup is in microseconds.", "vector_score": .8}
ANSWER = "Use microseconds. [Sleep Modes > Timer](" + SOURCE["url"] + ")"


class WordTokenizer:
    """Lightweight context-budget fixture with explicit chat overhead."""

    def encode(self, text: str, add_special_tokens: bool = False) -> list[str]:
        """Split fixture words into reversible tokens."""
        return text.split()

    def decode(self, tokens: list[str]) -> str:
        """Join fixture tokens."""
        return " ".join(tokens)

    def apply_chat_template(self, messages: list[dict[str, str]], **kwargs: object) -> list[str]:
        """Count messages with a fixed per-message chat overhead."""
        return self.encode(" ".join(m["content"] for m in messages)) + ["overhead"] * len(messages)


def final_completion(answer: str = ANSWER) -> Completion:
    """Create one syntactically valid final action for orchestration fixtures."""
    return Completion(json.dumps({"thought": "Sources suffice", "action": "final", "action_input": {"answer": answer}}), 1, 10, 1)


class AgentTests(unittest.TestCase):
    """Check bounded externally observable behavior without downloading model weights."""

    def setUp(self) -> None:
        """Inject token counting and a local document retriever."""
        self.token_patch = patch("src.tokens.tokenizer", return_value=WordTokenizer())
        self.prompt_patch = patch("src.agent.prompts.tokenizer", return_value=WordTokenizer())
        self.token_patch.start()
        self.prompt_patch.start()
        self.addCleanup(self.token_patch.stop)
        self.addCleanup(self.prompt_patch.stop)
        self.retriever = Mock()
        self.retriever.search.return_value = [SOURCE]

    def test_strict_json_rejects_duplicate_nonfinite_and_wrong_arguments(self) -> None:
        """Reject values that Python's permissive JSON decoder would otherwise accept."""
        for value in ('{"a":1,"a":2}', '{"x":NaN}', '[]', '```json\n{}\n```'):
            with self.assertRaises(ValueError):
                strict_json(value)
        with self.assertRaises(ValueError):
            parse_action('{"thought":"x","action":"estimate_power","action_input":{"mode":"deep_sleep","duration_h":true}}')

    def test_datasheet_current_profiles_and_nonfinite_inputs(self) -> None:
        """Use documented typical currents and reject negative/boolean/nonfinite duration."""
        self.assertAlmostEqual(estimate_power("deep_sleep", 24)["charge_mah"], .24)
        self.assertAlmostEqual(estimate_power("light_sleep", 10)["charge_mah"], 8)
        self.assertEqual(estimate_power("wifi_tx_11b", 1)["charge_mah"], 240)
        for value in (-1, True, float("nan"), float("inf"), "10"):
            with self.assertRaises(ValueError):
                estimate_power("deep_sleep", value)

    def test_source_ids_resolve_only_to_supplied_official_urls(self) -> None:
        """Render compact model markers deterministically and reject unknown source IDs."""
        answer = resolve_citations("Microseconds. [S1]", [SOURCE])
        self.assertIn(SOURCE["url"], answer)
        self.assertTrue(validate_answer(answer, [SOURCE], "q", Settings()))
        with self.assertRaises(ValueError):
            resolve_citations("Claim [S99]", [SOURCE])

    def test_double_escaped_code_layout_preserves_c_string_escape(self) -> None:
        """Correct the observed model formatting error without corrupting printf strings."""
        text = r'Example:\n```c\nprintf("value\n");\n```\n[S1]'
        fixed = normalize_code_layout(text)
        self.assertIn('```c\nprintf("value\\n");\n```', fixed)
        self.assertNotIn(r'Example:\n', fixed)

    def test_wrong_task_watchdog_signature_is_not_published(self) -> None:
        """Reject the real observed confusion between task and user watchdog handles."""
        source = {**SOURCE, "text": "esp_err_t esp_task_wdt_delete (TaskHandle_t task_handle)"}
        wrong = "esp_err_t esp_task_wdt_delete(esp_task_wdt_user_handle_t user_handle) " + ANSWER
        correct = "esp_err_t esp_task_wdt_delete(TaskHandle_t task_handle) " + ANSWER
        self.assertFalse(validate_answer(wrong, [source], "q", Settings()))
        self.assertTrue(validate_answer(correct, [source], "q", Settings()))
        self.assertTrue(validate_answer("esp_task_wdt_delete(NULL); " + ANSWER, [source], "q", Settings()))

    def test_invented_nvs_call_is_not_published(self) -> None:
        """Reject the observed invented nvs_flash_commit even when its URL is genuine."""
        source = {**SOURCE, "text": "Call nvs_commit() after nvs_set_i32()."}
        self.assertFalse(validate_answer("Call nvs_flash_commit(). " + ANSWER, [source], "q", Settings()))
        self.assertTrue(validate_answer("Call nvs_commit(). " + ANSWER, [source], "q", Settings()))

    def test_three_turns_and_history_clear(self) -> None:
        """Keep three complete turns and use the previous topic for ambiguous follow-ups."""
        client = Mock()
        client.chat.return_value = final_completion()
        agent = Agent(Settings(), self.retriever, client)
        for question in ("How do I set timer wakeup?", "What about its unit?", "And how do I disable it?", "And its limits?"):
            self.assertEqual(agent.ask(question).answer, ANSWER)
        self.assertEqual(len(agent.history), 3)
        self.assertIn("Follow-up:", self.retriever.search.call_args.args[0])
        self.assertTrue(any("What about its unit?" in m["content"] for m in client.chat.call_args.args[0]))
        agent.clear()
        self.assertEqual(agent.history, [])

    def test_invalid_json_repairs_once_then_direct_fallback(self) -> None:
        """Two malformed actions trigger one direct answer with no extra tool execution."""
        bad = Completion("not JSON", 1, 1, 1)
        client = Mock()
        client.chat.side_effect = [bad, bad, Completion(json.dumps({"answer": ANSWER}), 1, 1, 1)]
        result = Agent(Settings(), self.retriever, client).ask("timer wakeup unit?")
        self.assertTrue(result.fallback)
        self.assertEqual(client.chat.call_count, 3)
        self.assertEqual(len(result.events), 1)
        self.assertEqual(result.answer, ANSWER)

    def test_explicit_unknown_api_refuses_before_generation(self) -> None:
        """An absent official API cannot acquire an invented signature from the model."""
        self.retriever.has_identifier.return_value = False
        client = Mock()
        answer = Agent(Settings(), self.retriever, client).ask("Give esp_quantum_sleep_enable() signature")
        self.assertIn("could not find", answer.answer)
        client.chat.assert_not_called()

    def test_power_dispatch_and_exact_result_rendering(self) -> None:
        """A clear charge query requires the tool and always displays its verified mAh value."""
        client = Mock()
        power = Completion('{"thought":"Estimate","action":"estimate_power","action_input":{"mode":"light_sleep","duration_h":10}}', 1, 1, 1)
        client.chat.side_effect = [power, final_completion("[P1] Typical light-sleep current is 0.8 mA.")]
        result = Agent(Settings(), self.retriever, client).ask("Estimate light_sleep over 10 hours in mAh")
        self.assertIn("8 mAh", result.answer)
        self.assertIn("esp32_datasheet_en.pdf#page=30", result.answer)
        self.assertEqual(result.events[1]["input"], {"mode": "light_sleep", "duration_h": 10})
        first_schema = client.chat.call_args_list[0].args[1]
        self.assertEqual(first_schema["properties"]["action"]["enum"], ["estimate_power"])

    def test_repeated_tools_stop_at_four_and_fall_back(self) -> None:
        """A model that repeatedly requests search cannot run an unlimited tool loop."""
        search = Completion('{"thought":"Search","action":"search_docs","action_input":{"query":"timer"}}', 1, 1, 1)
        client = Mock()
        client.chat.side_effect = [search] * 5 + [Completion(json.dumps({"answer": ANSWER}), 1, 1, 1)]
        result = Agent(Settings(), self.retriever, client).ask("timer unit?")
        self.assertEqual(len(result.events), 4)
        self.assertEqual(self.retriever.search.call_count, 4)
        self.assertTrue(result.fallback)

    def test_context_drops_oldest_history_and_rejects_fabricated_urls(self) -> None:
        """Budget overflow removes complete oldest turns and citations stay whitelisted."""
        settings = replace(Settings(), num_ctx=700, llm_output_tokens=100, context_reserve_tokens=50)
        history = [{"question": "q " * 150, "answer": "a " * 350} for _ in range(4)]
        messages = build_messages("timer unit?", [SOURCE], history, settings)
        self.assertLessEqual(len(WordTokenizer().apply_chat_template(messages)), 550)
        self.assertEqual(messages[0]["role"], "system")
        self.assertFalse(validate_answer("Claim [Bad](https://evil.example)", [SOURCE], "q", settings))
        self.assertTrue(validate_answer(ANSWER, [SOURCE], "q", settings))

    def test_direct_rag_invalid_output_refuses_safely(self) -> None:
        """Phase 2 direct generation refuses after the single allowed format repair."""
        client = Mock()
        client.chat.return_value = Completion('{"answer":"uncited claim"}', 1, 1, 1)
        answer, _, completions = answer_question("timer?", self.retriever, client, Settings())
        self.assertIn("could not find", answer)
        self.assertEqual(len(completions), 2)

    def test_local_client_retries_and_failure_hint(self) -> None:
        """Connection failures get two retries, a fixed timeout and actionable guidance."""
        client = OllamaClient(replace(Settings(), chat_model="qwen2.5:7b-instruct"))
        with patch.object(client.session, "post", side_effect=requests.ConnectionError()) as post, patch("src.agent.llm.time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "ollama serve"):
                client.chat([])
            self.assertEqual(post.call_count, 3)
            self.assertEqual(post.call_args.kwargs["timeout"], 120)
        with self.assertRaises(ValueError):
            OllamaClient(replace(Settings(), ollama_url="https://cloud.example", chat_model="qwen2.5:7b-instruct"))


if __name__ == "__main__":
    unittest.main()
