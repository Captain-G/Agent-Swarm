"""
Retrieval primitives: BM25 keyword search, cosine similarity over embeddings,
and reciprocal-rank fusion of both. Works with zero dependencies beyond numpy,
so retrieval keeps working even when no embedding model is installed.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Sequence

import numpy as np

from .textutils import tokenize


class BM25:
    def __init__(self, documents: Sequence[str], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.docs = [tokenize(d) for d in documents]
        self.doc_len = [len(d) for d in self.docs]
        self.avgdl = (sum(self.doc_len) / len(self.docs)) if self.docs else 0.0
        self.tf = [Counter(d) for d in self.docs]
        df: Counter = Counter()
        for doc in self.docs:
            df.update(set(doc))
        n = len(self.docs)
        self.idf = {term: math.log(1 + (n - freq + 0.5) / (freq + 0.5)) for term, freq in df.items()}

    def scores(self, query: str) -> list[float]:
        terms = tokenize(query)
        out = []
        for tf, dl in zip(self.tf, self.doc_len):
            score = 0.0
            for term in terms:
                if term not in tf:
                    continue
                freq = tf[term]
                denom = freq + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1))
                score += self.idf.get(term, 0.0) * freq * (self.k1 + 1) / denom
            out.append(score)
        return out

    def top(self, query: str, k: int = 10) -> list[tuple[int, float]]:
        scored = [(i, s) for i, s in enumerate(self.scores(query)) if s > 0]
        scored.sort(key=lambda x: -x[1])
        return scored[:k]


def to_bytes(vector: Sequence[float]) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def from_bytes(blob: bytes | memoryview | None) -> np.ndarray | None:
    if not blob:
        return None
    return np.frombuffer(bytes(blob), dtype=np.float32)


def cosine_top(query_vec: Sequence[float], matrix: np.ndarray, k: int = 10) -> list[tuple[int, float]]:
    if matrix is None or len(matrix) == 0:
        return []
    q = np.asarray(query_vec, dtype=np.float32)
    qn = np.linalg.norm(q) or 1.0
    norms = np.linalg.norm(matrix, axis=1)
    norms[norms == 0] = 1.0
    sims = (matrix @ q) / (norms * qn)
    idx = np.argsort(-sims)[:k]
    return [(int(i), float(sims[i])) for i in idx]


def rrf(rankings: list[list[tuple[int, float]]], k: int = 60, top_k: int = 10) -> list[tuple[int, float]]:
    """Reciprocal-rank fusion of several ranked lists of (doc_index, score)."""
    fused: dict[int, float] = {}
    for ranking in rankings:
        for rank, (idx, _score) in enumerate(ranking):
            fused[idx] = fused.get(idx, 0.0) + 1.0 / (k + rank + 1)
    return sorted(fused.items(), key=lambda kv: -kv[1])[:top_k]


def mmr_select(candidates: list[str], k: int, lambda_: float = 0.7) -> list[int]:
    """Maximal marginal relevance over token sets: pick diverse items in rank order."""
    chosen: list[int] = []
    token_sets = [set(tokenize(c)) for c in candidates]
    for i, tokens in enumerate(token_sets):
        if len(chosen) >= k:
            break
        redundancy = max((len(tokens & token_sets[j]) / (len(tokens | token_sets[j]) or 1) for j in chosen), default=0.0)
        if redundancy <= 1 - lambda_ + 0.3:
            chosen.append(i)
    return chosen
