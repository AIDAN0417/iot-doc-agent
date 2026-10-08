"""The application's only Ollama /api/chat entry point, with bounded retries."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import requests

from src.bootstrap import detect_memory_gib, select_model
from src.config import CHAT_MODELS, LOCAL_OLLAMA_HOSTS, Settings

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Completion:
    """Model output and measured local generation performance."""

    content: str
    elapsed_s: float
    output_tokens: int
    generation_s: float

    @property
    def tokens_per_second(self) -> float:
        """Return measured generation speed, excluding loading and prompt ingestion."""
        return self.output_tokens / self.generation_s if self.generation_s else 0.0


class OllamaClient:
    """Call only supported local model names through the host's HTTP endpoint."""

    def __init__(self, settings: Settings) -> None:
        """Validate the endpoint again so direct Settings construction cannot bypass it."""
        parsed = urlsplit(settings.ollama_url)
        if parsed.scheme != "http" or parsed.hostname not in LOCAL_OLLAMA_HOSTS or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise ValueError("仅允许本地 Ollama 服务")
        self.settings = settings
        self.model = settings.chat_model or select_model(detect_memory_gib())
        if self.model not in CHAT_MODELS:
            raise ValueError("禁止云模型或未指定的模型名")
        self.session = requests.Session()
        self.session.trust_env = False

    def chat(self, messages: list[dict[str, str]], schema: dict[str, Any] | None = None) -> Completion:
        """Generate with a 120-second timeout and at most two retry attempts."""
        payload: dict[str, Any] = {
            "model": self.model, "messages": messages, "stream": False,
            "keep_alive": self.settings.keep_alive,
            "options": {"num_ctx": self.settings.num_ctx, "temperature": self.settings.llm_temperature,
                        "num_predict": self.settings.llm_output_tokens, "seed": self.settings.random_seed},
        }
        if schema is not None:
            payload["format"] = schema
        started = time.perf_counter()
        for attempt in range(self.settings.llm_retries + 1):
            try:
                response = self.session.post(self.settings.ollama_url + "/api/chat", json=payload,
                                             timeout=self.settings.llm_timeout_s, allow_redirects=False)
                response.raise_for_status()
                if response.status_code != 200:
                    raise ValueError("本地 Ollama 不应返回重定向")
                body = response.json()
                content = body["message"]["content"]
                if not isinstance(content, str):
                    raise ValueError("Ollama 响应不是字符串")
                return Completion(content, time.perf_counter() - started, int(body.get("eval_count", 0)),
                                  float(body.get("eval_duration", 0)) / 1_000_000_000)
            except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
                if attempt == self.settings.llm_retries:
                    raise RuntimeError("本地模型请求失败；先运行 `ollama serve`，并确认已拉取指定模型。") from exc
                LOGGER.warning("Ollama 请求失败，重试 %d/%d", attempt + 1, self.settings.llm_retries)
                time.sleep(self.settings.llm_retry_backoff_s)
        raise RuntimeError("Unreachable chat state")
