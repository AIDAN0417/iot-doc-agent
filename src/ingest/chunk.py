"""200–400 Qwen-token chunks with 50 source-token overlap and source metadata."""

from __future__ import annotations

import hashlib
import logging
import math
import random
import re
from typing import Any
from urllib.parse import urldefrag

from src.config import Settings, load_settings
from src.storage import read_json, write_json
from src.tokens import token_count, tokenizer

LOGGER = logging.getLogger(__name__)


def split_section(text: str, settings: Settings) -> list[str]:
    """Balance windows to avoid a tiny final chunk, and restore cut code fences."""
    model = tokenizer(settings.tokenizer_cache)
    tokens = model.encode(text, add_special_tokens=False)
    maximum = settings.chunk_max_tokens - settings.chunk_fence_reserve
    overlap = settings.chunk_overlap_tokens
    count = max(1, math.ceil((len(tokens) - overlap) / (maximum - overlap)))
    total = len(tokens) + (count - 1) * overlap
    lengths = [total // count + (index < total % count) for index in range(count)]
    result: list[str] = []
    start = 0
    for length in lengths:
        chunk = model.decode(tokens[start:start + length])
        before = model.decode(tokens[:start])
        fences = re.findall(r"^```([^\n]*)", before, flags=re.MULTILINE)
        if len(fences) % 2:
            chunk = "```" + fences[-1] + "\n" + chunk
        if len(re.findall(r"^```", chunk, flags=re.MULTILINE)) % 2:
            chunk += "\n```"
        if token_count(chunk, settings) > settings.chunk_max_tokens:
            raise ValueError("代码语言标记超出 chunk 预算，请检查分块策略")
        result.append(chunk)
        start += length - overlap
    return result


def chunk_sections(sections: list[dict[str, Any]], settings: Settings) -> list[dict[str, Any]]:
    """Merge short adjacent sections within one page before building source-linked chunks."""
    pages: dict[str, list[dict[str, Any]]] = {}
    for section in sections:
        pages.setdefault(urldefrag(section["url"])[0], []).append(section)
    chunks: list[dict[str, Any]] = []
    for page, records in pages.items():
        units: list[dict[str, Any]] = []
        pending: list[dict[str, Any]] = []
        for section in records:
            pending.append(section)
            body = "\n\n".join(item["text"] for item in pending)
            if token_count(body, settings) >= settings.chunk_min_tokens:
                units.append({"records": pending, "text": body})
                pending = []
        if pending:
            if units:
                units[-1]["records"].extend(pending)
                units[-1]["text"] += "\n\n" + "\n\n".join(item["text"] for item in pending)
            else:
                LOGGER.info("跳过不足 %d tokens 的导航/短页：%s", settings.chunk_min_tokens, page)
        for unit in units:
            parts = split_section(unit["text"], settings)
            source_size = token_count(unit["text"], settings)
            total = source_size + (len(parts) - 1) * settings.chunk_overlap_tokens
            lengths = [total // len(parts) + (i < total % len(parts)) for i in range(len(parts))]
            boundaries = [token_count("\n\n".join(r["text"] for r in unit["records"][:i + 1]), settings)
                          for i in range(len(unit["records"]))]
            start = 0
            for ordinal, text in enumerate(parts):
                midpoint = start + lengths[ordinal] // 2
                first = next(record for record, end in zip(unit["records"], boundaries) if midpoint <= end)
                size = token_count(text, settings)
                if size < settings.chunk_min_tokens:
                    raise ValueError("chunk 小于预算下界，需检查章节合并")
                identifier = hashlib.sha256((first["url"] + str(ordinal) + text).encode()).hexdigest()
                chunks.append({"chunk_id": identifier, "section_path": first["section_path"], "url": first["url"],
                               "section_paths": [record["section_path"] for record in unit["records"]],
                               "text": text, "tokens": size})
                start += lengths[ordinal] - settings.chunk_overlap_tokens
    return chunks


def main() -> None:
    """Write chunks and verify a deterministic random sample of five source links."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s：%(message)s")
    settings = load_settings()
    chunks = chunk_sections(read_json(settings.sections_path), settings)
    write_json(settings.chunks_path, chunks)
    LOGGER.info("共 %d 块，tokens 范围 %s", len(chunks), (min(c["tokens"] for c in chunks), max(c["tokens"] for c in chunks)) if chunks else "空")
    for chunk in random.Random(settings.random_seed).sample(chunks, min(settings.retrieval_top_k, len(chunks))):
        LOGGER.info("抽样：%s | %s | %d tokens", " > ".join(chunk["section_path"]), chunk["url"], chunk["tokens"])


if __name__ == "__main__":
    main()
