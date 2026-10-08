"""Resumable batch embeddings and atomic versioned FAISS publication."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import faiss
import numpy as np

from src.config import Settings, load_settings
from src.rag.embed import Embedder
from src.storage import read_json, write_json

LOGGER = logging.getLogger(__name__)


def corpus_fingerprint(chunks: list[dict[str, Any]], settings: Settings) -> str:
    """Identify both corpus contents and embedding settings for checkpoint reuse."""
    payload = json.dumps({
        "chunks": [(chunk["chunk_id"], chunk["text"]) for chunk in chunks],
        "model": settings.embed_model, "max_tokens": settings.embedding_max_tokens,
        "batch": settings.embedding_batch_size,
    }, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(payload).hexdigest()


def build_index(settings: Settings, embedder: Embedder | None = None) -> Path:
    """Encode durable batches, resume verified files, and publish one complete generation."""
    chunks = read_json(settings.chunks_path)
    if not chunks:
        raise ValueError("没有文档块；请先完成 crawl、clean、chunk")
    fingerprint = corpus_fingerprint(chunks, settings)
    generation = settings.index_dir / fingerprint
    manifest_path = generation / "build.json"
    generation.mkdir(parents=True, exist_ok=True)
    checkpoint = read_json(manifest_path) if manifest_path.exists() else {"fingerprint": fingerprint, "batches": {}}
    if checkpoint["fingerprint"] != fingerprint:
        raise ValueError("索引断点与语料不一致")
    if checkpoint.get("complete") and (generation / "index.faiss").exists():
        index = faiss.read_index(str(generation / "index.faiss"))
        if index.ntotal != len(chunks):
            raise ValueError("已完成索引行数与语料不一致")
        write_json(settings.index_dir / "active.json", {"generation": fingerprint})
        return generation
    embedder = embedder or Embedder(settings)
    paths: list[Path] = []
    for start in range(0, len(chunks), settings.embedding_batch_size):
        batch = chunks[start:start + settings.embedding_batch_size]
        path = generation / "batches" / f"{start:08d}.npy"
        entry = checkpoint["batches"].get(str(start))
        valid = False
        if entry and path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]:
            vectors = np.load(path, allow_pickle=False)
            valid = vectors.ndim == 2 and len(vectors) == len(batch) and np.isfinite(vectors).all()
        if not valid:
            vectors = embedder.encode([item["text"] for item in batch])
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            with temporary.open("wb") as stream:
                np.save(stream, vectors, allow_pickle=False)
            temporary.replace(path)
            checkpoint["batches"][str(start)] = {"rows": len(batch), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            write_json(manifest_path, checkpoint)
        paths.append(path)
        LOGGER.info("embedding %d/%d%s", min(start + len(batch), len(chunks)), len(chunks), "（复用）" if valid else "")
    first = np.load(paths[0], allow_pickle=False)
    index = faiss.IndexFlatIP(first.shape[1])
    for path in paths:
        vectors = np.load(path, allow_pickle=False)
        if vectors.shape[1] != index.d:
            raise ValueError("embedding 批次维度不一致")
        index.add(vectors)
    temporary_index = generation / "index.faiss.tmp"
    faiss.write_index(index, str(temporary_index))
    temporary_index.replace(generation / "index.faiss")
    write_json(generation / "chunks.json", chunks)
    checkpoint.update({"complete": True, "rows": len(chunks), "dimension": index.d,
                       "built_at": datetime.now(timezone.utc).isoformat()})
    write_json(manifest_path, checkpoint)
    write_json(settings.index_dir / "active.json", {"generation": fingerprint})
    LOGGER.info("索引发布：%d 行，%d 维；%s", index.ntotal, index.d, generation)
    return generation


def load_index(settings: Settings) -> tuple[Any, list[dict[str, Any]]]:
    """Load the single complete generation referenced by the atomic active pointer."""
    active = read_json(settings.index_dir / "active.json")["generation"]
    if not isinstance(active, str) or len(active) != len(hashlib.sha256().hexdigest()) or any(c not in "0123456789abcdef" for c in active):
        raise ValueError("索引 generation 标识不合法")
    folder = settings.index_dir / active
    manifest = read_json(folder / "build.json")
    chunks = read_json(folder / "chunks.json")
    index = faiss.read_index(str(folder / "index.faiss"))
    if not manifest.get("complete") or len(chunks) != index.ntotal:
        raise ValueError("索引尚未完成或元数据不匹配")
    return index, chunks


def main() -> None:
    """Build or resume the corpus index without external services."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s：%(message)s")
    argparse.ArgumentParser(description="构建/续建 FAISS 索引").parse_args()
    build_index(load_settings())


if __name__ == "__main__":
    main()
