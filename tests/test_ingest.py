"""Acquisition, cleaning, chunk boundaries and checkpoint regression checks."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from src.config import Settings
from src.ingest.chunk import chunk_sections, split_section
from src.ingest.clean import clean_html
from src.ingest.crawl import Crawler, canonical_url
from src.storage import read_json, write_json


class SpaceTokenizer:
    """Deterministic tokenizer fixture for source-window boundary assertions."""

    def encode(self, text: str, add_special_tokens: bool = False) -> list[str]:
        """Return words as fixture tokens."""
        return text.split()

    def decode(self, tokens: list[str]) -> str:
        """Join fixture tokens reversibly."""
        return " ".join(tokens)


class IngestTests(unittest.TestCase):
    """Test observable ingest behavior with controlled HTML and HTTP fixtures."""

    def test_scope_rejects_external_and_wrong_soc(self) -> None:
        """Only ESP32 reference/guide HTML can enter the crawl queue."""
        settings = Settings()
        good = settings.docs_root + "api-reference/system/sleep_modes.html"
        self.assertEqual(canonical_url(good + "#timer", settings), good)
        for url in ("https://evil.example/page.html", good.replace("esp32/", "esp32s3/"), good + "?x=1"):
            self.assertIsNone(canonical_url(url, settings))

    def test_cleaning_preserves_language_and_metadata(self) -> None:
        """Remove navigation while retaining a nested C code block and section anchors."""
        html = '''<nav>Unwanted</nav><div role="main"><section id="sleep"><h1>Sleep Modes<a class="headerlink">¶</a></h1>
        <section id="timer"><h2>Timer</h2><p>Use microseconds.</p><ul><li>Example<div class="highlight-c"><pre>esp_sleep_enable_timer_wakeup(1000000);</pre></div></li></ul>
        <table><tr><th>Mode</th><th>Current</th></tr><tr><td>sleep</td><td>0.8 mA</td></tr></table></section>
        <footer>Unwanted footer</footer></section></div>'''
        sections = clean_html(html, "https://docs.example/sleep.html")
        self.assertEqual(sections[1]["section_path"], ["Sleep Modes", "Timer"])
        self.assertEqual(sections[1]["url"], "https://docs.example/sleep.html#timer")
        self.assertIn("```c\nesp_sleep_enable_timer_wakeup(1000000);\n```", sections[1]["text"])
        self.assertIn("| sleep | 0.8 mA |", sections[1]["text"])
        self.assertNotIn("Unwanted", str(sections))

    def test_chunk_size_overlap_and_small_tail(self) -> None:
        """Every chunk stays within budget and repeats exactly fifty source tokens."""
        settings = Settings()
        with patch("src.tokens.tokenizer", return_value=SpaceTokenizer()), \
             patch("src.ingest.chunk.tokenizer", return_value=SpaceTokenizer()):
            for count in (200, 388, 389, 700, 1000):
                parts = split_section(" ".join(f"word{i}" for i in range(count)), settings)
                self.assertTrue(all(200 <= len(part.split()) <= 400 for part in parts))
                for left, right in zip(parts, parts[1:]):
                    self.assertEqual(left.split()[-50:], right.split()[:50])

    def test_short_sections_merge_without_crossing_page_sources(self) -> None:
        """Short sections retain their own page and contributing chapter paths."""
        records = [{"text": " ".join(["word"] * 150), "url": "https://docs/page.html#" + str(i),
                    "section_path": ["Page", str(i)]} for i in range(3)]
        with patch("src.tokens.tokenizer", return_value=SpaceTokenizer()), \
             patch("src.ingest.chunk.tokenizer", return_value=SpaceTokenizer()):
            chunks = chunk_sections(records, Settings())
        self.assertTrue(chunks)
        self.assertEqual(len(chunks[0]["section_paths"]), 3)
        self.assertEqual(chunks[0]["url"], records[0]["url"])

    def test_manifest_resume_does_not_redownload_intact_page(self) -> None:
        """Reload a cached page's frontier and avoid requesting already durable bytes."""
        with tempfile.TemporaryDirectory() as folder:
            settings = replace(Settings(), data_dir=Path(folder))
            page = settings.docs_root + "api-reference/index.html"
            file = Path(folder) / "raw" / "test.html"
            file.parent.mkdir()
            file.write_bytes(b"cached content")
            write_json(Path(folder) / "crawl_manifest.json", {"root": settings.docs_root, "failed": {}, "pages": {
                page: {"path": "raw/test.html", "sha256": hashlib.sha256(file.read_bytes()).hexdigest(), "links": []},
            }})
            crawler = Crawler(settings)
            crawler.robots.parse(["User-agent: *", "Disallow:"])
            with patch.object(crawler, "load_robots"), patch.object(crawler, "request") as request:
                result = crawler.crawl([page])
            request.assert_not_called()
            self.assertEqual(len(result["pages"]), 1)

    def test_retry_count_and_atomic_checkpoint(self) -> None:
        """Transient HTTP failures get three retries and JSON remains readable."""
        import requests
        crawler = Crawler(replace(Settings(), data_dir=Path(tempfile.gettempdir()) / "uncreated-iot-test"))
        response = Mock(status_code=200)
        with patch.object(crawler.session, "get", side_effect=[requests.ConnectionError()] * 3 + [response]) as get, \
             patch("src.ingest.crawl.time.sleep"):
            self.assertIs(crawler.request("https://docs.espressif.com/robots.txt"), response)
            self.assertEqual(get.call_count, 4)
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "checkpoint.json"
            write_json(file, {"中文": True})
            self.assertEqual(read_json(file), {"中文": True})
            self.assertFalse(file.with_name(file.name + ".tmp").exists())


if __name__ == "__main__":
    unittest.main()
