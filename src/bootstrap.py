"""Phase 0: reproducible local setup with a read-only offline check mode."""

from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

from src.config import (
    CHAT_MODELS, CHAT_SIZE_GB, DEPENDENCY_IMPORTS, EMBED_SIZE_GB, GIB,
    MIN_MEMORY_GIB, MIN_PYTHON, RECOMMENDED_MEMORY_GIB, TORCH_CPU_INDEX,
    TORCH_REQUIREMENT, Settings, load_settings,
)

LOGGER = logging.getLogger(__name__)


class SetupError(RuntimeError):
    """An expected setup failure with an actionable Chinese message."""


def run_command(
    command: list[str], *, timeout: int, repair: str,
    capture: bool = False, env: dict[str, str] | None = None,
) -> str:
    """Run a command without a shell and convert errors into repair guidance."""
    try:
        result = subprocess.run(
            command, check=True, timeout=timeout, capture_output=capture,
            text=True, encoding="utf-8", errors="replace", env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SetupError(f"命令执行失败：{command[0]}。{repair}") from exc
    return (result.stdout or "").strip()


def detect_memory_gib() -> float:
    """Detect usable physical RAM on Windows, macOS, or Linux containers."""
    system = platform.system()
    if system == "Windows":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_uint32), ("load", ctypes.c_uint32),
                *[(name, ctypes.c_uint64) for name in (
                    "total_physical", "available_physical", "total_page",
                    "available_page", "total_virtual", "available_virtual",
                    "available_extended",
                )],
            ]
        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise SetupError("无法读取物理内存，请检查系统权限后重试")
        total = status.total_physical
    elif system == "Darwin":
        # 预期：sysctl -n hw.memsize 输出 17179869184（16 GiB）。
        total = int(run_command(
            ["sysctl", "-n", "hw.memsize"], timeout=Settings().command_timeout_s,
            repair="请检查 macOS sysctl 命令是否可用。", capture=True,
        ))
    elif system == "Linux":
        # 预期：/proc/meminfo 中 MemTotal: 16777216 kB（16 GiB）。
        line = next(
            line for line in Path("/proc/meminfo").read_text().splitlines()
            if line.startswith("MemTotal:")
        )
        total = int(line.split()[1]) * 1024
        # 容器中以当前 cgroup 的内存限额为上限，不把宿主机全部内存当成可用。
        for limit in (
            Path("/sys/fs/cgroup/memory.max"),
            Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
        ):
            if limit.exists():
                value = limit.read_text().strip()
                if value.isdigit() and int(value) > 0:
                    total = min(total, int(value))
    else:
        raise SetupError("暂不支持该系统；请使用 Windows、Linux 或 macOS")
    return total / GIB


def select_model(memory_gib: float, requested: str | None = None) -> str:
    """Select the required 7B/3B model and stop below the hardware minimum."""
    if memory_gib < MIN_MEMORY_GIB:
        raise SetupError("物理内存不足 8 GiB，停止搭建。请在至少 8 GiB 的机器上运行。")
    recommended = CHAT_MODELS[0] if memory_gib >= RECOMMENDED_MEMORY_GIB else CHAT_MODELS[1]
    if requested is not None and requested not in CHAT_MODELS:
        raise SetupError("只允许 AGENTS.md 指定的 Qwen 7B/3B 本地模型")
    if requested == CHAT_MODELS[0] and memory_gib < RECOMMENDED_MEMORY_GIB:
        raise SetupError("当前内存不推荐 7B；请设置 IOT_CHAT_MODEL=qwen2.5:3b-instruct")
    return requested or recommended


def check_python_and_pip(settings: Settings) -> None:
    """Verify Python and pip before creating or installing anything."""
    # 预期：Python 3.11.9；低于 3.11 时退出且不创建 .venv。
    if sys.version_info < MIN_PYTHON:
        raise SetupError("需要 Python 3.11+；请升级 Python 后重新运行")
    LOGGER.info("Python %s：OK", platform.python_version())
    # 预期：python -m pip --version 输出 pip 24.0 from ... (python 3.11)。
    pip = run_command(
        [sys.executable, "-m", "pip", "--version"],
        timeout=settings.command_timeout_s,
        repair="请运行 python -m ensurepip --upgrade，再重新运行初始化。", capture=True,
    )
    LOGGER.info("pip：OK（%s）", pip)


def ollama_models(settings: Settings) -> list[dict[str, Any]]:
    """Verify the local Ollama service and return its installed models."""
    # 预期：ollama --version 输出 ollama version is ...；缺失时给官方下载链接。
    executable = shutil.which("ollama")
    if executable is None:
        raise SetupError("未安装 Ollama；请去 https://ollama.com/download 安装，重开终端后重跑")
    version = run_command(
        [executable, "--version"], timeout=settings.command_timeout_s,
        repair="请重装 Ollama 并确认 ollama 已加入 PATH。", capture=True,
    )
    LOGGER.info("Ollama 命令：OK（%s）", version)
    # 等价于 curl localhost:11434 + curl localhost:11434/api/tags。
    # 预期：根路径为 Ollama is running，/api/tags 为 {"models": [...]}。
    # 本机请求不走系统 HTTP 代理，避免 localhost 被送到外部代理。
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(settings.ollama_url, timeout=settings.health_timeout_s) as response:
            if response.read().decode("utf-8").strip() != "Ollama is running":
                raise SetupError("本机端口未返回 Ollama 服务；请检查 IOT_OLLAMA_URL")
        with opener.open(
            settings.ollama_url + "/api/tags", timeout=settings.health_timeout_s,
        ) as response:
            data = json.load(response)
        models = data["models"]
        if not isinstance(models, list) or not all(isinstance(item, dict) for item in models):
            raise ValueError("invalid models")
    except (URLError, OSError, ValueError, KeyError, TypeError) as exc:
        raise SetupError("Ollama 服务未就绪；先运行 ollama serve，再重跑初始化") from exc
    LOGGER.info("Ollama 服务：OK（%s）", settings.ollama_url)
    return models


def environment_python(settings: Settings, *, check_only: bool) -> Path:
    """Create an isolated venv or require an existing one during an offline check."""
    executable = settings.venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not executable.exists():
        if check_only:
            raise SetupError(".venv 尚未创建；请先运行 bash setup.sh 或 python -m src.bootstrap")
        # 预期：python -m venv .venv 无输出，创建 .venv 中的 Python 和 pip。
        run_command(
            [sys.executable, "-m", "venv", str(settings.venv_dir)],
            timeout=settings.command_timeout_s,
            repair="请安装 Python venv/ensurepip 组件并检查目录写权限。",
        )
    return executable


def install_dependencies(python: Path, settings: Settings) -> None:
    """Install CPU-only Torch and the pinned major dependency series into the venv."""
    # 预期：Successfully installed torch-...+cpu，macOS 为原生 CPU/MPS wheel。
    command = [str(python), "-m", "pip", "install", TORCH_REQUIREMENT]
    if platform.system() != "Darwin":
        command.extend(["--index-url", TORCH_CPU_INDEX])
    run_command(
        command, timeout=settings.install_timeout_s,
        repair="请检查网络与 Python 架构；CPU wheel 来源为 download.pytorch.org。",
    )
    # 预期：Successfully installed ... 或 Requirement already satisfied: ...。
    run_command(
        [str(python), "-m", "pip", "install", "-r", str(settings.project_root / "requirements.txt")],
        timeout=settings.install_timeout_s,
        repair="请检查网络/磁盘及 requirements.txt；Apple Silicon 的 faiss 失败时参见 README。",
    )


def check_dependencies(python: Path, settings: Settings) -> None:
    """Verify imports and package compatibility without downloading anything."""
    # 预期：pip check 输出 No broken requirements found，所有 import 正常退出。
    run_command(
        [str(python), "-m", "pip", "check"], timeout=settings.command_timeout_s,
        repair="依赖冲突；请重新运行初始化修复 .venv 的依赖。",
    )
    code = "; ".join(f"import {module}" for module in DEPENDENCY_IMPORTS)
    run_command(
        [str(python), "-c", code], timeout=settings.llm_timeout_s,
        repair="依赖未就绪；请重新运行初始化，Windows DLL 错误时检查 VC++ 运行库。",
    )
    LOGGER.info("Python 依赖：OK")


def prepare_embedding(python: Path, settings: Settings, *, check_only: bool) -> None:
    """Download embeddings once, then verify that they load with network disabled."""
    # 预期：首次下载 bge-m3 约 2.3 GB；不调用 ollama pull embedding。
    # pip 只安装程序包，不含模型权重，必须显式预缓存才可断网运行。
    code = (
        "from sentence_transformers import SentenceTransformer; "
        f"SentenceTransformer({settings.embed_model!r}, device={settings.embed_device!r}, "
        f"cache_folder={str(settings.embedding_cache)!r}, trust_remote_code=False, token=False, "
        "local_files_only=LOCAL_ONLY)"
    )
    model_env = os.environ.copy()
    model_env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    if not check_only:
        settings.embedding_cache.mkdir(parents=True, exist_ok=True)
        run_command(
            [str(python), "-c", code.replace("LOCAL_ONLY", "False")],
            timeout=settings.model_timeout_s, env=model_env,
            repair="embedding 下载失败；请检查网络和磁盘，重跑会复用已下载缓存。",
        )
    offline_env = model_env.copy()
    offline_env.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1"})
    # 预期：禁用网络后模型成功加载，日志为 embedding 离线加载验证通过。
    run_command(
        [str(python), "-c", code.replace("LOCAL_ONLY", "True")],
        timeout=settings.model_timeout_s, env=offline_env,
        repair="embedding 缓存不完整；请联网重新运行初始化预下载模型。",
    )
    LOGGER.info("embedding 离线加载验证通过：%s（%s）", settings.embed_model, settings.embed_device)


def setup(settings: Settings, *, check_only: bool = False) -> None:
    """Prepare the environment or run a read-only, download-free readiness check."""
    memory = detect_memory_gib()
    model = select_model(memory, settings.chat_model)
    LOGGER.info("物理内存 %.2f GiB；建议模型 %s", memory, model)
    LOGGER.info(
        "模型大小估算：对话 %.1f GB + embedding %.1f GB；环境合计约 %.1f GB（不含文档索引）",
        CHAT_SIZE_GB[model], EMBED_SIZE_GB[settings.embed_model],
        CHAT_SIZE_GB[model] + EMBED_SIZE_GB[settings.embed_model] + settings.estimated_environment_gb,
    )
    check_python_and_pip(settings)
    # 安装前先查 Ollama，缺失时立即给中文修复提示，避免白下载几个 GB。
    models = ollama_models(settings)
    if not check_only:
        # 预期：可用磁盘 >=15 GiB（含下载缓存/临时空间）。
        free_gib = shutil.disk_usage(settings.project_root).free / GIB
        if free_gib < settings.minimum_free_disk_gib:
            raise SetupError(f"磁盘仅剩 {free_gib:.1f} GiB；请预留至少 {settings.minimum_free_disk_gib} GiB")
    python = environment_python(settings, check_only=check_only)
    if not check_only:
        install_dependencies(python, settings)
    check_dependencies(python, settings)
    if not check_only:
        # 预期：ollama pull qwen2.5:7b-instruct 最终输出 success；重复执行复用缓存。
        client_env = os.environ.copy()
        client_env.update({"OLLAMA_HOST": settings.ollama_url, "OLLAMA_NO_CLOUD": "1"})
        run_command(
            [shutil.which("ollama") or "ollama", "pull", model],
            timeout=settings.model_timeout_s, env=client_env,
            repair="对话模型拉取失败；请检查 Ollama 服务、网络与模型存储目录的磁盘空间。",
        )
        models = ollama_models(settings)
    installed = next((item for item in models if item.get("name") == model), None)
    if installed is None:
        raise SetupError(f"本地未找到 {model}；请先运行 ollama pull {model}")
    prepare_embedding(python, settings, check_only=check_only)
    LOGGER.info("总结：Python / pip / 内存 / 依赖 / Ollama / 对话模型 / embedding 均 OK")
    LOGGER.info("选用模型：%s；num_ctx=%s；keep_alive=%s", model, settings.num_ctx, settings.keep_alive)
    LOGGER.info("Ollama 报告的模型磁盘大小：%.2f GB", float(installed.get("size", 0)) / 1_000_000_000)
    LOGGER.info("embedding 缓存：%s；虚拟环境：%s", settings.embedding_cache, settings.venv_dir)
    LOGGER.info("Phase 0 环境准备完成；后续 Phase 需要用户确认后执行")


def main(argv: list[str] | None = None) -> int:
    """Run the bootstrap CLI and report actionable errors without a traceback."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s：%(message)s")
    parser = argparse.ArgumentParser(description="IoT Agent 全本地环境初始化")
    parser.add_argument("--check", action="store_true", help="只检查：不安装、不下载、不创建环境")
    args = parser.parse_args(argv)
    try:
        setup(load_settings(), check_only=args.check)
    except SetupError as exc:
        LOGGER.error("%s", exc)
        return 1
    except (OSError, ValueError, StopIteration) as exc:
        LOGGER.error("环境检查失败：%s。请检查配置变量、系统命令和目录读写权限后重试。", exc)
        return 1
    except KeyboardInterrupt:
        LOGGER.error("初始化已中断；重新运行可复用已有依赖与模型缓存")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
