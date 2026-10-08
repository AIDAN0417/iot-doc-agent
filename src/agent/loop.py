"""Bounded strict-JSON ReAct orchestration with three-turn local history."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from src.agent.llm import Completion, OllamaClient
from src.agent.prompts import ANSWER_SCHEMA, REACT_SCHEMA, action_schema, build_messages, is_chinese, refusal, resolve_citations, validate_answer
from src.agent.tools import estimate_power, format_power_estimate, search_docs
from src.config import POWER_MODE_ALIASES, PUBLIC_TOOL_NOTE_MAX_CHARS, Settings
from src.rag.retrieve import Retriever
from src.tokens import token_count, truncate


def strict_json(text: str) -> dict[str, Any]:
    """Reject markdown wrappers, duplicate keys, nonfinite numbers and nonobject JSON."""
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        """Build one JSON object while rejecting repeated keys."""
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key: " + key)
            result[key] = value
        return result

    def invalid(value: str) -> Any:
        """Fail on Python's permissive NaN/Infinity extensions."""
        raise ValueError("Nonfinite JSON number: " + value)

    data = json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object")
    return data


def parse_action(text: str) -> dict[str, Any]:
    """Validate the complete ReAct envelope and action-specific argument types."""
    data = strict_json(text)
    if set(data) != {"thought", "action", "action_input"} or not isinstance(data["thought"], str):
        raise ValueError("Expected thought:string, action, action_input only")
    if len(data["thought"]) > PUBLIC_TOOL_NOTE_MAX_CHARS:
        raise ValueError("Tool-choice note is too long")
    action, args = data["action"], data["action_input"]
    if not isinstance(args, dict):
        raise ValueError("action_input must be an object")
    if action == "search_docs":
        if set(args) != {"query"} or not isinstance(args["query"], str) or not args["query"].strip():
            raise ValueError("search_docs needs query:string")
    elif action == "estimate_power":
        if set(args) != {"mode", "duration_h"}:
            raise ValueError("estimate_power needs mode and duration_h")
        estimate_power(**args)  # Validate before dispatch; no external side effects.
    elif action == "final":
        if set(args) != {"answer"} or not isinstance(args["answer"], str) or not args["answer"].strip():
            raise ValueError("final needs answer:string")
    else:
        raise ValueError("Unknown action")
    return data


@dataclass
class AgentResult:
    """Public answer, source metadata, tool events and actual generation measurements."""

    answer: str
    sources: list[dict[str, Any]]
    events: list[dict[str, Any]]
    elapsed_s: float
    completions: list[Completion] = field(default_factory=list)
    json_attempts: int = 0
    json_valid: int = 0
    fallback: bool = False


class Agent:
    """Keep bounded per-session conversation state with at most four tool calls."""

    def __init__(self, settings: Settings, retriever: Retriever, client: OllamaClient) -> None:
        """Reuse expensive inference resources while isolating chat history."""
        self.settings, self.retriever, self.client = settings, retriever, client
        self.history: list[dict[str, str]] = []

    def clear(self) -> None:
        """Clear all retained conversation turns."""
        self.history.clear()

    def ask(self, question: str) -> AgentResult:
        """Answer one turn, repair JSON once, and fall back without an unbounded loop."""
        if not question.strip() or token_count(question, self.settings) > self.settings.question_max_tokens:
            raise ValueError("问题为空或过长，请缩短后重试")
        started = time.perf_counter()
        followup = self.history and token_count(question, self.settings) <= self.settings.followup_max_tokens and (
            question.startswith(("那", "它", "这个", "What about", "And ", "How about"))
        )
        query = "Follow-up: " + question + "\nContext: " + "\n".join(turn["question"] for turn in self.history) if followup else question
        hits = search_docs(query, self.retriever)
        events: list[dict[str, Any]] = [{"action": "search_docs", "input": {"query": query}, "sources": [h["url"] for h in hits]}]
        sources = list(hits)
        completions: list[Completion] = []
        observations: list[dict[str, Any]] = []
        calls, attempts, valid = 1, 0, 0
        fallback = False
        power_requested = bool(re.search(r"估算|estimate|计算|算一下", question, re.IGNORECASE) and
                               re.search(r"mAh|电量|耗电|charge|consum", question, re.IGNORECASE))
        power_query = question.lower()
        for alias in sorted(POWER_MODE_ALIASES, key=len, reverse=True):
            power_query = power_query.replace(alias.lower(), POWER_MODE_ALIASES[alias])
        clear_power_input = len(re.findall(r"\b(deep_sleep_ulp|deep_sleep|light_sleep|hibernation|wifi_tx_11b)\b", power_query)) == 1 and \
                            len(re.findall(r"\d+(?:\.\d+)?\s*(?:hours?|小时|h\b)", power_query, re.IGNORECASE)) == 1
        answer = ""
        identifiers = re.findall(r"\b(?:esp_|nvs_|gpio_|adc_|uart_|i2c_)[a-z0-9_]+(?=\s*\()", question)
        unknown_api = any(not self.retriever.has_identifier(identifier) for identifier in identifiers)
        if unknown_api or not hits or max(h["vector_score"] for h in hits) < self.settings.relevance_threshold:
            answer = refusal(question, hits, self.settings)
        while not answer:
            error = ""
            action = None
            for repair in range(self.settings.json_repair_attempts + 1):
                observation = json.dumps(observations, ensure_ascii=False) + ("\nFORMAT ERROR: " + error if error else "")
                messages = build_messages(question, hits, self.history, self.settings, react=True,
                                          observation=truncate(observation, self.settings.tool_result_max_tokens, self.settings),
                                          exhausted=calls >= self.settings.max_tool_calls)
                power_ready = power_requested and clear_power_input and any(e["action"] == "estimate_power" for e in events)
                schema = action_schema("final") if calls >= self.settings.max_tool_calls or power_ready else (
                    action_schema("estimate_power") if power_requested and clear_power_input and
                    not any(e["action"] == "estimate_power" for e in events) else REACT_SCHEMA
                )
                if power_ready:
                    schema["properties"]["action_input"]["properties"]["answer"]["pattern"] = r"^\[P1\] .*$"
                completion = self.client.chat(messages, schema)
                completions.append(completion)
                attempts += 1
                try:
                    action = parse_action(completion.content)
                    valid += 1
                    if action["action"] == "final":
                        action["action_input"]["answer"] = resolve_citations(action["action_input"]["answer"], hits, observations)
                    if action["action"] == "final" and power_requested and not any(e["action"] == "estimate_power" for e in events):
                        raise ValueError("Call estimate_power before answering a charge calculation; otherwise refuse insufficient evidence")
                    if action["action"] == "final" and not validate_answer(action["action_input"]["answer"], sources, question, self.settings):
                        raise ValueError("Use the user's language and supplied citations. Every API call must occur in supplied text. Any C declaration must match the documented parameter types exactly; correct it or omit the unsolicited declaration.")
                    if action["action"] != "final" and calls >= self.settings.max_tool_calls:
                        raise ValueError("Tool budget exhausted: return final")
                    break
                except (ValueError, TypeError) as exc:
                    error, action = str(exc), None
            if action is None:
                fallback = True
                break
            name, args = action["action"], action["action_input"]
            if name == "final":
                answer = args["answer"]
                break
            calls += 1
            if name == "search_docs":
                if token_count(args["query"], self.settings) > self.settings.question_max_tokens:
                    observations.append({"error": "Search query too long"})
                    continue
                hits = search_docs(args["query"], self.retriever)
                sources.extend(h for h in hits if h["url"] not in {s["url"] for s in sources})
                observation = {"action": name, "chapters": [{"section_path": h["section_path"], "url": h["url"]} for h in hits]}
            else:
                observation = estimate_power(**args)
                observation["source_id"] = "P" + str(sum(e["action"] == "estimate_power" for e in events) + 1)
                sources.append(observation)
            observations.append(observation)
            events.append({"action": name, "input": args, "result": observation})
        if fallback:
            if power_requested and not any(e["action"] == "estimate_power" for e in events):
                answer = refusal(question, hits, self.settings)
                self.history = (self.history + [{"question": question, "answer": answer}])[-self.settings.history_turns:]
                return AgentResult(answer, sources, events, time.perf_counter() - started, completions, attempts, valid, fallback)
            messages = build_messages(question, hits, self.history, self.settings,
                                      observation=truncate(json.dumps(observations, ensure_ascii=False), self.settings.tool_result_max_tokens, self.settings))
            completion = self.client.chat(messages, ANSWER_SCHEMA)
            completions.append(completion)
            try:
                parsed = strict_json(completion.content)
                answer = resolve_citations(parsed["answer"], hits, observations)
                if set(parsed) != {"answer"} or not isinstance(answer, str) or not validate_answer(answer, sources, question, self.settings):
                    raise ValueError("Unsupported direct answer")
            except (ValueError, TypeError, KeyError):
                answer = refusal(question, hits, self.settings)
        if power_requested and clear_power_input:
            powers = [event["result"] for event in events if event["action"] == "estimate_power"]
            if powers:
                answer = format_power_estimate(powers[-1], chinese=is_chinese(question))
        self.history = (self.history + [{"question": question, "answer": answer}])[-self.settings.history_turns:]
        return AgentResult(answer, sources, events, time.perf_counter() - started, completions, attempts, valid, fallback)
