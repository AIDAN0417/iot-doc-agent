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
TOKENIZER_MODEL = "Qwen/Qwen2.5-7B-Instruct"
PUBLIC_TOOL_NOTE_MAX_CHARS = 160
DATASHEET_URL = "https://www.espressif.com/sites/default/files/documentation/esp32_datasheet_en.pdf"
POWER_MODE_ALIASES = {"ULP 深度睡眠": "deep_sleep_ulp", "深度睡眠": "deep_sleep", "深睡": "deep_sleep",
                      "轻睡眠": "light_sleep", "浅睡眠": "light_sleep", "light-sleep": "light_sleep", "deep-sleep": "deep_sleep"}
DOC_ROOT = "https://docs.espressif.com/projects/esp-idf/en/latest/esp32/"
CRAWL_SECTIONS = ("api-reference/", "api-guides/")
CRAWLER_USER_AGENT = "iot-doc-agent/1.0 (+https://github.com/AIDAN0417/iot-doc-agent)"
CHINESE_KEYWORDS = {
    "深度睡眠": ("deep-sleep", "esp_deep_sleep_start", "sleep"),
    "浅睡眠": ("light-sleep", "esp_light_sleep_start", "sleep"),
    "唤醒": ("wakeup", "sleep"), "定时器": ("timer",),
    "所有唤醒": ("esp_sleep_disable_wakeup_source", "esp_sleep_wakeup_all"),
    "关闭": ("disable",),
    "功耗": ("power", "consumption", "sleep"), "电流": ("current", "power"),
    "看门狗": ("watchdog", "wdt"), "任务": ("task", "freertos"),
    "订阅": ("subscribe",), "喂狗": ("feed", "reset"), "当前": ("current",),
    "中断": ("interrupt",), "模数": ("adc",), "校准": ("calibration",),
    "蓝牙": ("bluetooth", "ble"), "无线": ("wifi", "wi-fi"),
    "闪存": ("flash",), "加密": ("encryption",), "安全启动": ("secure", "boot"),
    "串口": ("uart",), "引脚": ("gpio", "pin"), "存储": ("nvs", "storage"),
    "键名": ("key", "names"), "最长": ("maximum", "length"),
    "字符": ("characters",), "写入": ("write", "commit"),
}
QUERY_STOPWORDS = set("the and for this that with what which how why does should please when are can use using configure configuration esp32 will while keep documented alternative explain directly official signature its must any just from into called after before".split())
API_SEARCH_ALIASES = {"ext0": "esp_sleep_enable_ext0_wakeup", "ext1": "esp_sleep_enable_ext1_wakeup"}
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
    docs_root: str = DOC_ROOT
    crawl_interval_s: float = 1.0
    crawl_retries: int = 3
    crawl_timeout_s: int = 30
    crawl_max_pages: int = 0
    chunk_min_tokens: int = 200
    chunk_max_tokens: int = 400
    chunk_overlap_tokens: int = 50
    chunk_fence_reserve: int = 12
    embedding_batch_size: int = 32
    embedding_max_tokens: int = 768
    embedding_threads: int = 8
    retrieval_candidates: int = 20
    retrieval_top_k: int = 5
    keyword_boost: float = 0.12
    api_reference_boost: float = 0.25
    expand_retrieval_query: bool = True
    relevance_threshold: float = 0.30
    llm_temperature: float = 0.0
    llm_output_tokens: int = 800
    system_prompt_tokens: int = 800
    context_reserve_tokens: int = 256
    history_turns: int = 3
    history_turn_tokens: int = 500
    max_tool_calls: int = 4
    json_repair_attempts: int = 1
    random_seed: int = 42
    eval_accuracy_target: float = 0.8
    ui_port: int = 8501
    question_max_tokens: int = 300
    history_question_tokens: int = 150
    llm_retry_backoff_s: float = 1.0
    followup_max_tokens: int = 48
    tool_result_max_tokens: int = 1600
    answer_refusal_zh: str = "文档中没有找到相关内容"
    answer_refusal_en: str = "I could not find this information in the retrieved documentation."

    @property
    def venv_dir(self) -> Path:
        """Return the project-local virtual environment directory."""
        return self.project_root / ".venv"

    @property
    def embedding_cache(self) -> Path:
        """Return the ignored embedding model cache directory."""
        return self.data_dir / "models" / "embedding"

    @property
    def tokenizer_cache(self) -> Path:
        """Return the local cache for exact Qwen context-budget token counting."""
        return self.data_dir / "models" / "tokenizer"

    @property
    def crawl_manifest_path(self) -> Path:
        """Return the resumable crawl manifest path."""
        return self.data_dir / "crawl_manifest.json"

    @property
    def sections_path(self) -> Path:
        """Return the cleaned section corpus path."""
        return self.data_dir / "clean" / "sections.json"

    @property
    def chunks_path(self) -> Path:
        """Return the source-linked token chunk corpus path."""
        return self.data_dir / "chunks.json"

    @property
    def index_dir(self) -> Path:
        """Return the versioned FAISS index directory."""
        return self.data_dir / "index"

    @property
    def questions_path(self) -> Path:
        """Return the thirty-question evaluation fixture path."""
        return self.project_root / "eval" / "questions.json"

    @property
    def evaluation_report_path(self) -> Path:
        """Return the generated local evaluation report path."""
        return self.project_root / "eval" / "report.txt"


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
        crawl_max_pages=int(os.environ.get("IOT_CRAWL_MAX_PAGES", "0")),
        retrieval_top_k=int(os.environ.get("IOT_TOP_K", "5")),
        embedding_threads=int(os.environ.get("IOT_EMBED_THREADS", "8")),
    )
    if settings.num_ctx <= 0 or not settings.keep_alive:
        raise ValueError("IOT_NUM_CTX 必须为正整数，IOT_KEEP_ALIVE 不得为空")
    if not 1 <= settings.retrieval_top_k <= 5 or settings.crawl_max_pages < 0:
        raise ValueError("IOT_TOP_K 需在 1–5 之间，IOT_CRAWL_MAX_PAGES 不得为负数")
    if settings.embedding_threads < 1:
        raise ValueError("IOT_EMBED_THREADS 必须为正整数")
    return settings
