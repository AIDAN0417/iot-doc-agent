"""Offline Qwen token counting shared by chunking and prompt budgeting."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from src.config import TOKENIZER_MODEL, Settings


@lru_cache(maxsize=1)
def tokenizer(cache_dir: Path) -> Any:
    """Load the pre-cached tokenizer without contacting Hugging Face."""
    from transformers import AutoTokenizer
    from huggingface_hub import snapshot_download
    snapshot = snapshot_download(TOKENIZER_MODEL, cache_dir=str(cache_dir), local_files_only=True)
    return AutoTokenizer.from_pretrained(
        snapshot, local_files_only=True,
        trust_remote_code=False, token=False,
    )


def token_count(text: str, settings: Settings) -> int:
    """Count Qwen tokens without adding special chat tokens."""
    return len(tokenizer(settings.tokenizer_cache).encode(text, add_special_tokens=False))


def truncate(text: str, maximum: int, settings: Settings) -> str:
    """Keep at most the specified number of Qwen tokens."""
    model = tokenizer(settings.tokenizer_cache)
    return model.decode(model.encode(text, add_special_tokens=False)[:maximum])
