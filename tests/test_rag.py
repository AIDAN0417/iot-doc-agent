"""Embedding normalization, durable resume, reranking and citation regression tests."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import faiss
import numpy as np

from src.config import Settings
from src.rag.embed import Embedder
from src.rag.index import build_index, load_index
from src.rag.retrieve import Retriever, query_keywords
from src.storage import write_json


class RagTests(unittest.TestCase):
    """Exercise persistence and bilingual ranking independently of large models."""

    def test_embedder_rejects_nonfinite_vectors(self) -> None:
        """Reject a broken model response instead of publishing NaNs to FAISS."""
        model = Mock()
        model.encode.return_value = np.array([[np.nan, 1]])
        with self.assertRaises(ValueError):
            Embedder(Settings(), model).encode(["test"])
        self.assertTrue(model.encode.call_args.kwargs["normalize_embeddings"])

    def test_interrupted_build_resumes_only_verified_batches(self) -> None:
        """An interrupted build reuses intact files and rebuilds a corrupted checkpoint."""
        with tempfile.TemporaryDirectory() as folder:
            settings = replace(Settings(), data_dir=Path(folder), embedding_batch_size=2)
            chunks = [{"chunk_id": str(i), "text": str(i), "section_path": ["Test"], "url": "https://docs/test"} for i in range(5)]
            write_json(settings.chunks_path, chunks)
            encoder = Mock()
            encoder.encode.side_effect = [np.array([[1, 0], [0, 1]], dtype="float32"), RuntimeError("interrupted")]
            with self.assertRaises(RuntimeError):
                build_index(settings, encoder)
            generation = next(p for p in settings.index_dir.iterdir() if p.is_dir())
            first = generation / "batches" / "00000000.npy"
            original = first.read_bytes()
            encoder.encode.side_effect = [np.array([[1, 0], [0, 1]], dtype="float32"), np.array([[1, 0]], dtype="float32")]
            build_index(settings, encoder)
            self.assertEqual(first.read_bytes(), original)
            index, metadata = load_index(settings)
            self.assertEqual(index.ntotal, 5)
            self.assertEqual(len(metadata), 5)
            encoder.reset_mock()
            build_index(settings, encoder)
            encoder.encode.assert_not_called()

    def test_exact_api_keyword_changes_top_candidate(self) -> None:
        """Keyword reranking promotes an API match from the vector candidate set."""
        vectors = np.array([[1, 0], [.99, .1]], dtype="float32")
        index = faiss.IndexFlatIP(2)
        index.add(vectors)
        chunks = [{"text": "general sleep", "section_path": ["Sleep"], "url": "a"},
                  {"text": "esp_sleep_enable_timer_wakeup microseconds", "section_path": ["Timer"], "url": "b"}]
        encoder = Mock()
        encoder.encode.return_value = np.array([[1, 0]], dtype="float32")
        hits = Retriever(Settings(), encoder, (index, chunks)).search("esp_sleep_enable_timer_wakeup 定时器唤醒")
        self.assertEqual(hits[0]["url"], "b")
        self.assertIn("wakeup", query_keywords("定时器唤醒"))

    def test_corrupted_batch_is_rebuilt(self) -> None:
        """Checksum failure forces a fresh batch before the complete index is published."""
        with tempfile.TemporaryDirectory() as folder:
            settings = replace(Settings(), data_dir=Path(folder), embedding_batch_size=2)
            write_json(settings.chunks_path, [{"chunk_id": str(i), "text": str(i)} for i in range(3)])
            encoder = Mock()
            pair = np.array([[1, 0], [0, 1]], dtype="float32")
            encoder.encode.side_effect = [pair, RuntimeError("interrupted")]
            with self.assertRaises(RuntimeError):
                build_index(settings, encoder)
            generation = next(p for p in settings.index_dir.iterdir() if p.is_dir())
            batch = generation / "batches" / "00000000.npy"
            batch.write_bytes(b"broken")
            encoder.reset_mock()
            encoder.encode.side_effect = [pair, np.array([[1, 0]], dtype="float32")]
            build_index(settings, encoder)
            self.assertEqual(encoder.encode.call_count, 2)
            self.assertEqual(load_index(settings)[0].ntotal, 3)

    def test_similar_function_names_do_not_receive_exact_api_bonus(self) -> None:
        """Prefer task deletion over user deletion when the exact API is requested."""
        index = faiss.IndexFlatIP(2)
        index.add(np.array([[1, 0], [.99, .1]], dtype="float32"))
        chunks = [{"text": "esp_task_wdt_delete_user", "section_path": ["User"], "url": "a"},
                  {"text": "esp_task_wdt_delete", "section_path": ["Task"], "url": "b"}]
        encoder = Mock()
        encoder.encode.return_value = np.array([[1, 0]], dtype="float32")
        hits = Retriever(Settings(), encoder, (index, chunks)).search("esp_task_wdt_delete")
        self.assertEqual(hits[0]["url"], "b")


if __name__ == "__main__":
    unittest.main()
