"""All setup and application settings; no cloud inference endpoints."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIN_PYTHON = (3, 11)
MIN_MEMORY_GIB = 8
RECOMMENDED_MEMORY_GIB = 16
GIB = 1024**3
CHAT_MODELS = ("qwen2.5:7b-instruct", "qwen2.5:3b-instruct")
EMBED_MODELS = ("BAAI/bge-m3", "sentence-transformers/all-MiniLM-L6-v2")
CHAT_SIZE_GB = {CHAT_MODELS[0]: 4.7, CHAT_MODELS[1]: 1.9}
EMBED_SIZE_GB = {EMBED_MODELS[0]: 2.3, EMBED_MODELS[1]: 0.1}
LOCAL_OLLAMA_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal"}
TORCH_REQUIREMENT = "torch>=2.4,<3"
TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"
DEPENDENCY_IMPORTS = (
    "torch", "sentence_transformers", "transformers", "faiss",
    "numpy", "streamlit", "requests", "bs4",
)


@dataclass(frozen=True)
class Settings:
    """Validated configuration shared by setup and future application phases."""

    project_root: Path = PROJECT_ROOT
    data_dir: Path = PROJECT_ROOT / "data"
    chat_model: str | None = None  # None selects 7B/3B from physical RAM.
    embed_model: str = EMBED_MODELS[0]
    ollama_url: str = "http://localhost:11434"
    num_ctx: int = 8192
    keep_alive: str = "30m"
    llm_timeout_s: int = 120
    llm_retries: int = 2
    health_timeout_s: int = 5
    command_timeout_s: int = 60
    install_timeout_s: int = 3600
    model_timeout_s: int = 7200
    minimum_free_disk_gib: int = 15
    estimated_environment_gb: int = 4
    embed_device: str = "cpu"

    @property
    def venv_dir(self) -> Path:
        """Return the project-local virtual environment directory."""
        return self.project_root / ".venv"

    @property
    def embedding_cache(self) -> Path:
        """Return the ignored embedding model cache directory."""
        return self.data_dir / "models" / "embedding"


def load_settings() -> Settings:
    """Load environment overrides and reject unsupported models or cloud hosts."""
    model = os.environ.get("IOT_CHAT_MODEL") or None
    embedding = os.environ.get("IOT_EMBED_MODEL", EMBED_MODELS[0])
    url = os.environ.get("IOT_OLLAMA_URL", "http://localhost:11434").rstrip("/")
    parsed = urlsplit(url)
    try:
        parsed.port  # Validate malformed ports as well as the hostname.
    except ValueError as exc:
        raise ValueError("IOT_OLLAMA_URL 端口无效，请使用 http://localhost:11434") from exc
    if (
        parsed.scheme != "http" or parsed.hostname not in LOCAL_OLLAMA_HOSTS
        or parsed.username or parsed.password or parsed.path
        or parsed.query or parsed.fragment
    ):
        raise ValueError("IOT_OLLAMA_URL 仅允许本机或 Docker 宿主机的 HTTP 地址")
    if model is not None and model not in CHAT_MODELS:
        raise ValueError("IOT_CHAT_MODEL 仅允许 AGENTS.md 指定的本地 Qwen 7B/3B 模型")
    if embedding not in EMBED_MODELS:
        raise ValueError("IOT_EMBED_MODEL 仅允许 bge-m3 或英文场景 MiniLM")
    data = Path(os.environ.get("IOT_DATA_DIR", "data"))
    if not data.is_absolute():
        data = PROJECT_ROOT / data
    settings = Settings(
        data_dir=data.resolve(), chat_model=model, embed_model=embedding,
        ollama_url=url, num_ctx=int(os.environ.get("IOT_NUM_CTX", "8192")),
        keep_alive=os.environ.get("IOT_KEEP_ALIVE", "30m"),
    )
    if settings.num_ctx <= 0 or not settings.keep_alive:
        raise ValueError("IOT_NUM_CTX 必须为正整数，IOT_KEEP_ALIVE 不得为空")
    return settings
