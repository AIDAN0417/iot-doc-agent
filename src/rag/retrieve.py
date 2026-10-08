"""Top-20 vector recall followed by API/Chinese keyword weighting and top-5 selection."""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from src.config import API_SEARCH_ALIASES, CHINESE_KEYWORDS, QUERY_STOPWORDS, Settings
from src.rag.embed import Embedder
from src.rag.index import load_index


def query_keywords(query: str) -> set[str]:
    """Extract exact API/register tokens and map Chinese topic words to document terms."""
    terms = set(re.findall(r"[a-z][a-z0-9_/-]{2,}", query.lower()))
    for word, expansions in CHINESE_KEYWORDS.items():
        if word in query:
            terms.update(expansions)
    terms.update(api for alias, api in API_SEARCH_ALIASES.items() if alias in terms)
    return terms - QUERY_STOPWORDS


class Retriever:
    """Reuse an offline embedder and complete FAISS snapshot for source-linked searches."""

    def __init__(self, settings: Settings, embedder: Embedder | None = None,
                 snapshot: tuple[Any, list[dict[str, Any]]] | None = None) -> None:
        """Load the active snapshot, or inject one for deterministic retrieval tests."""
        self.settings = settings
        self.embedder = embedder or Embedder(settings)
        self.index, self.chunks = snapshot or load_index(settings)
        self.documents = [(" ".join(chunk["section_path"]) + " " + chunk["text"]).lower() for chunk in self.chunks]

    def search(self, query: str) -> list[dict[str, Any]]:
        """Return source text and separate vector/reranking scores for each top hit."""
        if not query.strip():
            return []
        terms = query_keywords(query)
        semantic_query = query + "\nTechnical keywords: " + " ".join(sorted(terms)) if self.settings.expand_retrieval_query else query
        vector = self.embedder.encode([semantic_query])
        scores, rows = self.index.search(vector, min(self.settings.retrieval_candidates, len(self.chunks)))
        patterns = {term: re.compile(r"(?<![a-z0-9_])" + re.escape(term) + r"(?![a-z0-9_])") for term in terms}
        frequencies = Counter({term: sum(bool(pattern.search(document)) for document in self.documents)
                               for term, pattern in patterns.items()})
        hits: list[dict[str, Any]] = []
        for score, row in zip(scores[0], rows[0]):
            if row < 0:
                continue
            text = self.documents[int(row)]
            bonus = sum(math.log1p(len(self.chunks) / (frequencies[term] + 1)) for term in terms if patterns[term].search(text))
            api_reference = "/api-reference/" in self.chunks[int(row)]["url"] and "kconfig-reference.html" not in self.chunks[int(row)]["url"]
            hits.append({**self.chunks[int(row)], "vector_score": float(score),
                         "score": float(score) + self.settings.keyword_boost * bonus + (self.settings.api_reference_boost if api_reference else 0.0)})
        return sorted(hits, key=lambda hit: hit["score"], reverse=True)[:self.settings.retrieval_top_k]

    def has_identifier(self, identifier: str) -> bool:
        """Check whether an explicitly requested API name exists anywhere in the corpus."""
        pattern = re.compile(r"\b" + re.escape(identifier.lower()) + r"\b")
        return any(pattern.search(document) for document in self.documents)
