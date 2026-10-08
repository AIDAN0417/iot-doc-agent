"""Phase 0 regression checks; no large downloads or paid services required."""

from __future__ import annotations

import io
import json
import subprocess
import tempfile
import threading
import unittest
from contextlib import ExitStack
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from src import bootstrap
from src.config import CHAT_MODELS, GIB, Settings, load_settings


class HardwareTests(unittest.TestCase):
    """Check hardware boundaries and prevent a forced model from bypassing them."""

    def test_memory_boundaries(self) -> None:
        """Stop below 8 GiB and select the intended model on both boundaries."""
        with self.assertRaisesRegex(bootstrap.SetupError, "内存不足"):
            bootstrap.select_model(7.99)
        for memory in (8, 12, 15.99):
            self.assertEqual(bootstrap.select_model(memory), CHAT_MODELS[1])
        for memory in (16, 31.43):
            self.assertEqual(bootstrap.select_model(memory), CHAT_MODELS[0])

    def test_override_cannot_bypass_hardware_or_model_constraints(self) -> None:
        """Accept 3B on a strong machine but reject 7B on a weak machine and cloud models."""
        self.assertEqual(bootstrap.select_model(32, CHAT_MODELS[1]), CHAT_MODELS[1])
        for memory, model in ((4, CHAT_MODELS[1]), (12, CHAT_MODELS[0]), (32, "qwen-cloud")):
            with self.assertRaises(bootstrap.SetupError):
                bootstrap.select_model(memory, model)


class ConfigurationTests(unittest.TestCase):
    """Validate environment overrides at the application's local-only boundary."""

    def test_cloud_urls_and_models_are_rejected(self) -> None:
        """Disallow external inference endpoints, credentials, paths, and model substitution."""
        invalid = (
            {"IOT_OLLAMA_URL": "https://api.example.com"},
            {"IOT_OLLAMA_URL": "http://localhost:11434/api/chat"},
            {"IOT_OLLAMA_URL": "http://user:password@localhost:11434"},
            {"IOT_OLLAMA_URL": "http://localhost:invalid"},
            {"IOT_CHAT_MODEL": "qwen-cloud"},
            {"IOT_EMBED_MODEL": "other/model"},
            {"IOT_NUM_CTX": "0"},
        )
        for env in invalid:
            with self.subTest(env=env), patch.dict("os.environ", env, clear=True):
                with self.assertRaises(ValueError):
                    load_settings()

    def test_supported_local_overrides(self) -> None:
        """Load Docker's local host, model override, and an anchored relative data path."""
        with patch.dict("os.environ", {
            "IOT_OLLAMA_URL": "http://host.docker.internal:11434/",
            "IOT_CHAT_MODEL": CHAT_MODELS[1], "IOT_DATA_DIR": "data-custom",
        }, clear=True):
            settings = load_settings()
        self.assertEqual(settings.ollama_url, "http://host.docker.internal:11434")
        self.assertEqual(settings.chat_model, CHAT_MODELS[1])
        self.assertEqual(settings.data_dir, settings.project_root / "data-custom")
        self.assertEqual((settings.num_ctx, settings.keep_alive), (8192, "30m"))


class LocalOllamaTests(unittest.TestCase):
    """Test actual HTTP transport against a tiny loopback Ollama fixture."""

    def test_loopback_health_and_models_ignore_proxy(self) -> None:
        """Read local health/models successfully even with an unusable system HTTP proxy."""
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                """Serve the two Ollama readiness endpoints."""
                self.send_response(200)
                self.end_headers()
                body = b"Ollama is running" if self.path == "/" else json.dumps({
                    "models": [{"name": CHAT_MODELS[0], "size": 4_700_000_000}],
                }).encode()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                """Keep the fixture quiet."""

        with HTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            settings = replace(Settings(), ollama_url=f"http://127.0.0.1:{server.server_port}")
            try:
                with patch("src.bootstrap.shutil.which", return_value="ollama"), \
                     patch("src.bootstrap.run_command", return_value="ollama version is test"), \
                     patch.dict("os.environ", {"HTTP_PROXY": "http://127.0.0.1:1", "NO_PROXY": ""}):
                    models = bootstrap.ollama_models(settings)
                self.assertEqual(models[0]["name"], CHAT_MODELS[0])
            finally:
                server.shutdown()
                thread.join(timeout=2)

    def test_missing_ollama_and_stopped_service_have_repairs(self) -> None:
        """Give the download URL or ollama serve instruction for the two common failures."""
        with patch("src.bootstrap.shutil.which", return_value=None):
            with self.assertRaisesRegex(bootstrap.SetupError, "ollama.com/download"):
                bootstrap.ollama_models(Settings())
        opener = Mock()
        opener.open.side_effect = OSError("connection refused")
        with patch("src.bootstrap.shutil.which", return_value="ollama"), \
             patch("src.bootstrap.run_command", return_value="ollama version test"), \
             patch("src.bootstrap.build_opener", return_value=opener):
            with self.assertRaisesRegex(bootstrap.SetupError, "先运行 ollama serve"):
                bootstrap.ollama_models(Settings())

    def test_invalid_tags_is_not_a_success(self) -> None:
        """Reject a non-Ollama or malformed response instead of declaring readiness."""
        root = Mock()
        root.__enter__ = Mock(return_value=io.BytesIO(b"Ollama is running"))
        root.__exit__ = Mock(return_value=False)
        tags = Mock()
        tags.__enter__ = Mock(return_value=io.BytesIO(b'{"models": "wrong"}'))
        tags.__exit__ = Mock(return_value=False)
        opener = Mock()
        opener.open.side_effect = [root, tags]
        with patch("src.bootstrap.shutil.which", return_value="ollama"), \
             patch("src.bootstrap.run_command", return_value="ollama test"), \
             patch("src.bootstrap.build_opener", return_value=opener):
            with self.assertRaises(bootstrap.SetupError):
                bootstrap.ollama_models(Settings())


class SetupFlowTests(unittest.TestCase):
    """Test setup orchestration's failure boundaries and offline guarantees."""

    def test_low_memory_stops_before_install_or_model_download(self) -> None:
        """No side effects are attempted on a machine below the hardware minimum."""
        with patch("src.bootstrap.detect_memory_gib", return_value=4), \
             patch("src.bootstrap.run_command") as command, \
             patch("src.bootstrap.prepare_embedding") as embedding:
            with self.assertRaises(bootstrap.SetupError):
                bootstrap.setup(Settings())
        command.assert_not_called()
        embedding.assert_not_called()

    def test_missing_ollama_stops_before_dependency_download(self) -> None:
        """Fail with repair guidance before downloading gigabytes of unused dependencies."""
        with patch("src.bootstrap.detect_memory_gib", return_value=32), \
             patch("src.bootstrap.check_python_and_pip"), \
             patch("src.bootstrap.ollama_models", side_effect=bootstrap.SetupError("未安装 Ollama")), \
             patch("src.bootstrap.install_dependencies") as install, \
             patch("src.bootstrap.environment_python") as venv:
            with self.assertRaises(bootstrap.SetupError):
                bootstrap.setup(Settings())
        install.assert_not_called()
        venv.assert_not_called()

    def test_complete_setup_and_offline_check(self) -> None:
        """Success requires dependencies, both models, and an embedding offline load."""
        for check_only in (False, True):
            with self.subTest(check_only=check_only), ExitStack() as stack:
                stack.enter_context(patch("src.bootstrap.detect_memory_gib", return_value=32))
                stack.enter_context(patch("src.bootstrap.check_python_and_pip"))
                stack.enter_context(patch("src.bootstrap.ollama_models", return_value=[{
                    "name": CHAT_MODELS[0], "size": 4_700_000_000,
                }]))
                stack.enter_context(patch("src.bootstrap.shutil.disk_usage", return_value=Mock(free=100 * GIB)))
                venv = stack.enter_context(patch("src.bootstrap.environment_python", return_value=Path("venv-python")))
                install = stack.enter_context(patch("src.bootstrap.install_dependencies"))
                deps = stack.enter_context(patch("src.bootstrap.check_dependencies"))
                command = stack.enter_context(patch("src.bootstrap.run_command"))
                embedding = stack.enter_context(patch("src.bootstrap.prepare_embedding"))
                stack.enter_context(patch("src.bootstrap.prepare_tokenizer"))
                bootstrap.setup(Settings(), check_only=check_only)
                venv.assert_called_once_with(Settings(), check_only=check_only)
                deps.assert_called_once()
                embedding.assert_called_once_with(Path("venv-python"), Settings(), check_only=check_only)
                self.assertEqual(install.call_count, 0 if check_only else 1)
                self.assertEqual(command.call_count, 0 if check_only else 1)
                if not check_only:
                    self.assertIn(CHAT_MODELS[0], command.call_args.args[0])
                    self.assertEqual(command.call_args.kwargs["env"]["OLLAMA_NO_CLOUD"], "1")

    def test_pull_without_installed_model_is_not_success(self) -> None:
        """Do not trust a successful pull exit code if Ollama still lacks the model."""
        with ExitStack() as stack:
            stack.enter_context(patch("src.bootstrap.detect_memory_gib", return_value=32))
            stack.enter_context(patch("src.bootstrap.check_python_and_pip"))
            stack.enter_context(patch("src.bootstrap.ollama_models", return_value=[]))
            stack.enter_context(patch("src.bootstrap.shutil.disk_usage", return_value=Mock(free=100 * GIB)))
            stack.enter_context(patch("src.bootstrap.environment_python", return_value=Path("venv-python")))
            stack.enter_context(patch("src.bootstrap.install_dependencies"))
            stack.enter_context(patch("src.bootstrap.check_dependencies"))
            stack.enter_context(patch("src.bootstrap.run_command"))
            embedding = stack.enter_context(patch("src.bootstrap.prepare_embedding"))
            with self.assertRaisesRegex(bootstrap.SetupError, "本地未找到"):
                bootstrap.setup(Settings())
            embedding.assert_not_called()

    def test_low_disk_stops_before_environment_creation(self) -> None:
        """Check download space before creating or modifying the virtual environment."""
        with patch("src.bootstrap.detect_memory_gib", return_value=32), \
             patch("src.bootstrap.check_python_and_pip"), \
             patch("src.bootstrap.ollama_models", return_value=[]), \
             patch("src.bootstrap.shutil.disk_usage", return_value=Mock(free=GIB)), \
             patch("src.bootstrap.environment_python") as venv:
            with self.assertRaisesRegex(bootstrap.SetupError, "磁盘"):
                bootstrap.setup(Settings())
        venv.assert_not_called()

    def test_embedding_offline_check_never_downloads_or_creates_cache(self) -> None:
        """Check mode passes local_files_only and offline flags without touching disk."""
        with tempfile.TemporaryDirectory() as directory:
            settings = replace(Settings(), data_dir=Path(directory) / "data")
            with patch("src.bootstrap.run_command") as command:
                bootstrap.prepare_embedding(Path("python"), settings, check_only=True)
            command.assert_called_once()
            self.assertIn("local_files_only=True", command.call_args.args[0][-1])
            self.assertEqual(command.call_args.kwargs["env"]["HF_HUB_OFFLINE"], "1")
            self.assertFalse(settings.embedding_cache.exists())

    def test_command_failure_and_timeout_include_repair(self) -> None:
        """Failed subprocesses produce actionable messages, including a timeout."""
        errors = (OSError("missing"), subprocess.TimeoutExpired("pip", 1), subprocess.CalledProcessError(1, "pip"))
        for error in errors:
            with self.subTest(error=type(error).__name__), \
                 patch("src.bootstrap.subprocess.run", side_effect=error):
                with self.assertRaisesRegex(bootstrap.SetupError, "修复建议"):
                    bootstrap.run_command(["pip"], timeout=1, repair="修复建议")

    def test_cli_exits_nonzero_without_traceback(self) -> None:
        """An expected setup failure is logged in Chinese and returns an error code."""
        with patch("src.bootstrap.setup", side_effect=bootstrap.SetupError("先运行 ollama serve")), \
             self.assertLogs("src.bootstrap", level="ERROR") as output:
            result = bootstrap.main(["--check"])
        self.assertEqual(result, 1)
        self.assertIn("先运行 ollama serve", output.output[0])


if __name__ == "__main__":
    unittest.main()
