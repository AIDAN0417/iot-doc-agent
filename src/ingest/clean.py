"""Extract section-aware Markdown while retaining official code and table content."""

from __future__ import annotations

import logging
from typing import Any

from bs4 import BeautifulSoup, Tag

from src.config import Settings, load_settings
from src.storage import read_json, write_json

LOGGER = logging.getLogger(__name__)


def block_text(node: Tag) -> str:
    """Render a content block, preserving code language and table row relationships."""
    if node.name == "pre":
        language = "text"
        for ancestor in [node, *list(node.parents)]:
            if not isinstance(ancestor, Tag):
                continue
            for name in ancestor.get("class", []):
                if name.startswith(("highlight-", "language-")):
                    language = name.split("-", 1)[1]
                    break
            if language != "text":
                break
        return f"```{language}\n{node.get_text().rstrip()}\n```"
    if node.name == "table":
        rows = [[cell.get_text(" ", strip=True).replace("|", "\\|")
                 for cell in row.find_all(["th", "td"], recursive=False)] for row in node.select("tr")]
        rows = [row for row in rows if row]
        if not rows:
            return ""
        width = max(map(len, rows))
        rows = [row + [""] * (width - len(row)) for row in rows]
        return "\n".join(["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * width) + " |",
                          *["| " + " | ".join(row) + " |" for row in rows[1:]]])
    text = " ".join(str(value).strip() for value in node.strings
                    if not value.parent.find_parent("pre") and value.parent.name != "pre")
    if node.name and node.name.startswith("h") and node.name[1:].isdigit():
        return "#" * int(node.name[1:]) + " " + text
    return ("- " if node.name == "li" else "") + text


def clean_html(html: str, url: str) -> list[dict[str, Any]]:
    """Extract h1/h2 section paths and anchor URLs from a Sphinx content area."""
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one('div[role="main"], article, .document')
    if root is None:
        raise ValueError("未找到正文结构；先小样本检查官方文档 HTML")
    for node in root.select("script, style, nav, footer, .headerlink, .rst-footer-buttons, .breadcrumbs, .admonition-feedback"):
        node.decompose()
    sections: list[dict[str, Any]] = []
    path: list[str] = []
    current: dict[str, Any] | None = None
    selected = root.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "pre", "li", "dt", "table"])
    for node in selected:
        if node.name != "pre" and any(isinstance(parent, Tag) and parent.name in ("pre", "li", "table", "dt") for parent in node.parents if parent != root):
            continue
        if node.name in ("h1", "h2"):
            level = int(node.name[1:])
            path = path[:level - 1] + [node.get_text(" ", strip=True)]
            anchor = node.get("id") or (node.parent.get("id") if isinstance(node.parent, Tag) else None)
            current = {"section_path": list(path), "url": url + ("#" + anchor if anchor else ""), "text": block_text(node)}
            sections.append(current)
        elif current is not None:
            text = block_text(node)
            if text:
                current["text"] += "\n\n" + text
    return sections


def clean_manifest(settings: Settings) -> list[dict[str, Any]]:
    """Clean every intact manifest page and write a deterministic local corpus."""
    manifest = read_json(settings.crawl_manifest_path)
    sections: list[dict[str, Any]] = []
    for url, entry in sorted(manifest["pages"].items()):
        if entry.get("redirect_to"):
            continue  # A moved-page notice is not documentary evidence.
        html = (settings.data_dir / entry["path"]).read_text(encoding="utf-8")
        sections.extend(clean_html(html, url))
    write_json(settings.sections_path, sections)
    LOGGER.info("清洗 %d 页，得到 %d 个章节", len(manifest["pages"]), len(sections))
    return sections


def main() -> None:
    """Clean the current crawl checkpoint."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s：%(message)s")
    clean_manifest(load_settings())


if __name__ == "__main__":
    main()
