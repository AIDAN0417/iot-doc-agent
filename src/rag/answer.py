"""Phase 2 direct source-grounded answering before ReAct tool orchestration."""

from __future__ import annotations

import json
from typing import Any

from src.agent.llm import Completion, OllamaClient
from src.agent.prompts import ANSWER_SCHEMA, build_messages, refusal, resolve_citations, validate_answer
from src.config import Settings
from src.rag.retrieve import Retriever


def answer_question(question: str, retriever: Retriever, client: OllamaClient,
                    settings: Settings) -> tuple[str, list[dict[str, Any]], list[Completion]]:
    """Retrieve once, request strict JSON, repair once, then refuse unsupported output."""
    hits = retriever.search(question)
    if not hits or max(hit["vector_score"] for hit in hits) < settings.relevance_threshold:
        return refusal(question, hits, settings), hits, []
    messages = build_messages(question, hits, [], settings)
    completions: list[Completion] = []
    for attempt in range(settings.json_repair_attempts + 1):
        completion = client.chat(messages, ANSWER_SCHEMA)
        completions.append(completion)
        try:
            parsed = json.loads(completion.content)
            if not isinstance(parsed, dict) or set(parsed) != {"answer"} or not isinstance(parsed["answer"], str):
                raise ValueError("Expected JSON {answer:string}")
            parsed["answer"] = resolve_citations(parsed["answer"], hits)
            if not validate_answer(parsed["answer"], hits, question, settings):
                raise ValueError("Every factual paragraph MUST include a supplied source ID such as [S1]. Never write a URL.")
            return parsed["answer"], hits, completions
        except (ValueError, TypeError) as exc:
            if attempt == settings.json_repair_attempts:
                break
            messages = build_messages(question, hits, [], settings,
                                      observation="FORMAT/REFERENCE ERROR: " + str(exc) + ". Return corrected JSON.")
    return refusal(question, hits, settings), hits, completions
