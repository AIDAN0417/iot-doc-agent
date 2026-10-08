"""Measure real local answers, expected citations, refusals, latency and JSON compliance."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

from src.agent.llm import OllamaClient
from src.agent.loop import Agent
from src.agent.prompts import cited_urls
from src.config import Settings, load_settings
from src.rag.answer import answer_question
from src.rag.retrieve import Retriever
from src.storage import read_json, write_json

LOGGER = logging.getLogger(__name__)


def is_refusal(answer: str, settings: Settings) -> bool:
    """Recognize the required Chinese/English evidence-insufficiency phrases."""
    return settings.answer_refusal_zh in answer or settings.answer_refusal_en.rstrip(".").lower() in answer.lower()


def check_answer(question: dict[str, Any], answer: str, sources: list[dict[str, Any]], settings: Settings) -> dict[str, Any]:
    """Check exact allowed citations and fixture facts without claiming semantic judging."""
    urls = cited_urls(answer)
    allowed = {source["url"] for source in sources}
    expected = question.get("sources", [question.get("source")])
    expected_urls = [settings.docs_root + part for part in expected if part]
    if question.get("source_url"):
        expected_urls.append(question["source_url"])
    correct_chapter = any(any(url.split("#")[0] == target for target in expected_urls) for url in urls)
    refused = is_refusal(answer, settings)
    def matches(term: str) -> bool:
        """Require complete API identifiers so delete_user cannot pass a delete test."""
        if re.fullmatch(r"(?:esp_|nvs_|gpio_|adc_|rtc_)[a-z0-9_]+", term):
            return bool(re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", answer, re.IGNORECASE))
        return term.lower() in answer.lower()
    facts = all(any(matches(term) for term in alternatives) for alternatives in question["facts"])
    valid_citations = bool(urls) and urls <= allowed
    expected_refusal = question.get("expect_refusal", False)
    passed = (refused and urls <= allowed) if expected_refusal else (facts and correct_chapter and valid_citations and not refused)
    return {"passed": passed, "facts_match": facts, "expected_chapter": correct_chapter,
            "citation_whitelist": urls <= allowed, "refused": refused, "citations": sorted(urls)}


def summarize(results: list[dict[str, Any]], mode: str, settings: Settings) -> str:
    """Format measured statistics, separating fixture checks from human factual review."""
    count = len(results)
    supported = [r for r in results if not r["question"].get("expect_refusal")]
    passed = sum(r["checks"]["passed"] for r in results)
    citations = sum(r["checks"]["expected_chapter"] and r["checks"]["citation_whitelist"] for r in supported)
    refused = sum(r["checks"]["refused"] for r in results)
    attempts = sum(r["json_attempts"] for r in results)
    valid = sum(r["json_valid"] for r in results)
    duration = sum(r["generation_s"] for r in results)
    tokens = sum(r["output_tokens"] for r in results)
    lines = [f"ESP32 local evaluation | {datetime.now(timezone.utc).isoformat()} | mode={mode}",
             f"模型：{settings.chat_model or '按内存自动选择 Qwen'}；embedding：{settings.embed_model}",
             f"自动事实/预期出处检查：{passed}/{count} ({passed / count:.1%})",
             "以上为关键词与预期章节检查，完整事实准确性需人工复核原始回答。",
             f"可回答题引用章节命中率：{citations}/{len(supported)} ({citations / len(supported):.1%})" if supported else "可回答题：0",
             f"编造/越界引用：{sum(not r['checks']['citation_whitelist'] for r in results)}",
             f"拒答率：{refused}/{count} ({refused / count:.1%})",
             f"平均响应时长：{sum(r['elapsed_s'] for r in results) / count:.2f} s",
             f"实测生成速度：{tokens / duration:.2f} tok/s" if duration else "未生成 tokens",
             f"ReAct JSON 协议合规率：{valid}/{attempts} ({valid / attempts:.1%})" if attempts else "直接 RAG 模式，无 ReAct 协议",
             f"JSON 降级次数：{sum(r['fallback'] for r in results)}", ""]
    lines += [f"{r['question']['id']:02d} {'PASS' if r['checks']['passed'] else 'REVIEW'} {r['elapsed_s']:.1f}s {r['question']['question']}" for r in results]
    return "\n".join(lines) + "\n"


def run_evaluation(settings: Settings, *, mode: str = "agent", limit: int = 0, resume: bool = False) -> list[dict[str, Any]]:
    """Evaluate independent questions and checkpoint every completed local answer."""
    questions = read_json(settings.questions_path)
    if limit:
        questions = questions[:limit]
    prefix = "phase2-" if mode == "rag" else ""
    result_path = settings.project_root / "eval" / (prefix + "results.json")
    report_path = settings.project_root / "eval" / (prefix + "report.txt")
    source_hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for package in ("agent", "rag")
                     for path in sorted((settings.project_root / "src" / package).glob("*.py"))]
    fingerprint = hashlib.sha256(json.dumps({"questions": questions, "mode": mode, "settings": repr(settings),
                                            "index": read_json(settings.index_dir / "active.json"), "sources": source_hashes}, sort_keys=True).encode()).hexdigest()
    cached = read_json(result_path) if resume and result_path.exists() else {}
    results = cached.get("results", []) if cached.get("fingerprint") == fingerprint else []
    completed = {r["question"]["id"] for r in results}
    retriever, client = Retriever(settings), OllamaClient(settings)
    LOGGER.info("本地模型 %s；开始 %d 题（已完成 %d）", client.model, len(questions), len(completed))
    for question in questions:
        if question["id"] in completed:
            continue
        started = time.perf_counter()
        if mode == "rag":
            answer, sources, completions = answer_question(question["question"], retriever, client, settings)
            events, attempts, valid, fallback = [], 0, 0, False
        else:
            result = Agent(settings, retriever, client).ask(question["question"])
            answer, sources, completions = result.answer, result.sources, result.completions
            events, attempts, valid, fallback = result.events, result.json_attempts, result.json_valid, result.fallback
        entry = {"question": question, "answer": answer, "sources": [{"url": s["url"], "section_path": s["section_path"]} for s in sources],
                 "events": events, "checks": check_answer(question, answer, sources, settings), "elapsed_s": time.perf_counter() - started,
                 "json_attempts": attempts, "json_valid": valid, "fallback": fallback,
                 "output_tokens": sum(c.output_tokens for c in completions), "generation_s": sum(c.generation_s for c in completions)}
        results.append(entry)
        write_json(result_path, {"fingerprint": fingerprint, "model": client.model, "results": results})
        LOGGER.info("%02d %s | %.1fs", question["id"], "PASS" if entry["checks"]["passed"] else "REVIEW", entry["elapsed_s"])
    report = summarize(results, mode, settings)
    report_path.write_text(report, encoding="utf-8")
    LOGGER.info("\n%s", report)
    return results


def main() -> None:
    """Run the thirty-question agent test or the required first-ten direct RAG test."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s：%(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("agent", "rag"), default="agent")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("--limit must be nonnegative")
    run_evaluation(load_settings(), mode=args.mode, limit=args.limit, resume=args.resume)


if __name__ == "__main__":
    main()
