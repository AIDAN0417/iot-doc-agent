"""Rate-limited, robots-aware and resumable official ESP32 documentation crawler."""

from __future__ import annotations

import argparse
import hashlib
import logging
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from src.config import CRAWLER_USER_AGENT, CRAWL_SECTIONS, Settings, load_settings
from src.storage import read_json, write_json

LOGGER = logging.getLogger(__name__)
USER_AGENT = CRAWLER_USER_AGENT


def canonical_url(url: str, settings: Settings) -> str | None:
    """Accept only official ESP32 API-reference and API-guide HTML pages."""
    url = urldefrag(url)[0]
    parts = urlsplit(url)
    if parts.query or parts.scheme != "https" or parts.netloc != "docs.espressif.com":
        return None
    allowed = tuple(settings.docs_root + section for section in CRAWL_SECTIONS)
    return url if url.startswith(allowed) and url.endswith(".html") else None


class Crawler:
    """Own the request rate, robots policy, frontier, and durable crawl manifest."""

    def __init__(self, settings: Settings) -> None:
        """Create a crawler with an isolated HTTP session and existing checkpoint."""
        self.settings = settings
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.next_request = 0.0
        self.manifest_path = settings.crawl_manifest_path
        self.manifest = read_json(self.manifest_path) if self.manifest_path.exists() else {
            "root": settings.docs_root, "pages": {}, "failed": {},
        }
        if self.manifest["root"] != settings.docs_root:
            raise ValueError("抓取清单的文档根路径不匹配，请使用独立 data 目录")
        self.robots = RobotFileParser()

    def request(self, url: str) -> requests.Response:
        """Perform an HTTP request with one request per second and three retries."""
        for attempt in range(self.settings.crawl_retries + 1):
            time.sleep(max(0.0, self.next_request - time.monotonic()))
            self.next_request = time.monotonic() + self.settings.crawl_interval_s
            try:
                response = self.session.get(url, timeout=self.settings.crawl_timeout_s)
                if response.status_code < 500 and response.status_code != 429:
                    return response
                response.raise_for_status()
            except requests.RequestException:
                if attempt == self.settings.crawl_retries:
                    raise
                LOGGER.warning("请求失败，重试 %d/%d：%s", attempt + 1, self.settings.crawl_retries, url)
        raise RuntimeError("Unreachable request state")

    def load_robots(self) -> None:
        """Read and persist the official robots policy; fail closed on unknown rules."""
        url = "https://docs.espressif.com/robots.txt"
        response = self.request(url)
        if response.status_code == 404:
            rules = "User-agent: *\nDisallow:\n"
        else:
            response.raise_for_status()
            rules = response.text
        self.robots.set_url(url)
        self.robots.parse(rules.splitlines())
        self.manifest["robots"] = {"url": url, "text": rules, "fetched_at": datetime.now(timezone.utc).isoformat()}
        write_json(self.manifest_path, self.manifest)

    def crawl(self, seeds: list[str] | None = None, maximum: int | None = None) -> dict:
        """Visit scoped HTML pages and resume both content and frontier from a manifest."""
        self.load_robots()
        seeds = seeds or [self.settings.docs_root + part for part in (
            "api-reference/index.html", "api-guides/index.html",
        )]
        queue = deque(seeds)
        seen: set[str] = set()
        maximum = self.settings.crawl_max_pages if maximum is None else maximum
        processed = 0
        while queue and (not maximum or processed < maximum):
            url = canonical_url(queue.popleft(), self.settings)
            if url is None or url in seen:
                continue
            seen.add(url)
            if not self.robots.can_fetch(USER_AGENT, url):
                LOGGER.warning("robots 禁止抓取：%s", url)
                continue
            cached = self.manifest["pages"].get(url)
            file = self.settings.data_dir / cached["path"] if cached else None
            if cached and file is not None and file.exists() and hashlib.sha256(file.read_bytes()).hexdigest() == cached["sha256"]:
                links = cached["links"]
            else:
                try:
                    response = self.request(url)
                    response.raise_for_status()
                    if canonical_url(response.url, self.settings) is None:
                        raise ValueError("文档发生越界重定向")
                    if "text/html" not in response.headers.get("Content-Type", ""):
                        raise ValueError("文档未返回 HTML")
                    soup = BeautifulSoup(response.content, "html.parser")
                    links = sorted({target for node in soup.select("a[href]")
                                    if (target := canonical_url(urljoin(url, node["href"]), self.settings))})
                    relocation = None
                    refresh = soup.select_one('meta[http-equiv="refresh"]')
                    if refresh and "url=" in refresh.get("content", ""):
                        destination = refresh["content"].split("url=", 1)[1].strip()
                        # Espressif's moved-page stubs omit the ESP-IDF project prefix.
                        if destination.startswith(tuple("/" + part for part in CRAWL_SECTIONS)):
                            destination = self.settings.docs_root + destination.lstrip("/")
                        relocation = canonical_url(urljoin(url, destination), self.settings)
                        if relocation is None:
                            raise ValueError("迁移页的目标越出 ESP32 文档范围")
                        links = sorted(set(links + [relocation]))
                    relative = Path("raw") / (hashlib.sha256(url.encode()).hexdigest() + ".html")
                    file = self.settings.data_dir / relative
                    file.parent.mkdir(parents=True, exist_ok=True)
                    temporary = file.with_suffix(".tmp")
                    temporary.write_bytes(response.content)
                    temporary.replace(file)
                    self.manifest["pages"][url] = {
                        "path": relative.as_posix(), "url": url, "bytes": len(response.content),
                        "sha256": hashlib.sha256(response.content).hexdigest(), "links": links,
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                        "redirect_to": relocation,
                    }
                    self.manifest["failed"].pop(url, None)
                    LOGGER.info("抓取 %d bytes：%s", len(response.content), url)
                except (requests.RequestException, ValueError) as exc:
                    self.manifest["failed"][url] = {"error": str(exc), "time": datetime.now(timezone.utc).isoformat()}
                    write_json(self.manifest_path, self.manifest)
                    LOGGER.warning("保留失败记录，可重跑：%s", url)
                    continue
                write_json(self.manifest_path, self.manifest)
            processed += 1
            queue.extend(links)
        LOGGER.info("清单共 %d 页，失败 %d 页；本轮访问 %d 页", len(self.manifest["pages"]), len(self.manifest["failed"]), processed)
        return self.manifest


def main() -> None:
    """Run a full or explicitly limited crawl from the repository root."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s：%(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--seed", action="append")
    args = parser.parse_args()
    Crawler(load_settings()).crawl(args.seed, args.limit)


if __name__ == "__main__":
    main()
