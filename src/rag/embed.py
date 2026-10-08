"""CPU-only bge-m3 encoding with normalized float32 dense embeddings."""

from __future__ import annotations

from typing import Any
import os

import numpy as np

from src.config import Settings


class Embedder:
    """Load one offline embedding model and reuse it for batches and queries."""

    def __init__(self, settings: Settings, model: Any | None = None) -> None:
        """Initialize local CPU inference, or inject a model for deterministic tests."""
        self.settings = settings
        if model is None:
            import torch
            from sentence_transformers import SentenceTransformer
            from huggingface_hub import snapshot_download
            torch.set_num_threads(min(settings.embedding_threads, os.cpu_count() or settings.embedding_threads))
            snapshot = snapshot_download(settings.embed_model, cache_dir=str(settings.embedding_cache), local_files_only=True)
            model = SentenceTransformer(
                snapshot, device=settings.embed_device,
                cache_folder=str(settings.embedding_cache), local_files_only=True,
                token=False, trust_remote_code=False,
            )
            model.max_seq_length = settings.embedding_max_tokens
        self.model = model

    def encode(self, texts: list[str]) -> np.ndarray:
        """Return normalized embeddings in the configured CPU batch size."""
        vectors = np.asarray(self.model.encode(
            texts, batch_size=self.settings.embedding_batch_size,
            normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False,
        ), dtype=np.float32)
        if vectors.ndim != 2 or len(vectors) != len(texts) or not np.isfinite(vectors).all():
            raise ValueError("embedding 返回的形状或数值异常")
        return vectors
