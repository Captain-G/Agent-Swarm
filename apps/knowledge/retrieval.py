"""
Hybrid retrieval over the knowledge base (GraphRAG query side).

* Local search: BM25 + embedding similarity fused with reciprocal-rank fusion,
  then expanded with graph neighbours of matched entities.
* Agent context: a compact, persona-relevant bundle of facts, stakeholder
  positions and arguments that is cheap enough to put in every agent prompt.
"""

from __future__ import annotations

import random
import threading

import numpy as np

from apps.core.llm import get_llm
from apps.core.text.retrieval import BM25, cosine_top, from_bytes, rrf
from apps.core.text.textutils import tokenize, truncate

from .models import Argument, Chunk, Dossier, Entity, Relation

_cache_lock = threading.Lock()


class KnowledgeBase:
    def __init__(self, project, use_embeddings: bool = True):
        self.project = project
        self.chunks = list(Chunk.objects.filter(project=project).select_related("source").only("id", "text", "embedding", "kind", "source__title", "source__url", "source__platform", "source__published_at"))
        self._bm25: BM25 | None = None
        self._matrix = None
        if use_embeddings:
            vectors = [from_bytes(c.embedding) for c in self.chunks]
            if vectors and all(v is not None and len(v) for v in vectors) and len({len(v) for v in vectors}) == 1:
                self._matrix = np.vstack(vectors)
        try:
            self.dossier = project.dossier
        except Dossier.DoesNotExist:
            self.dossier = None
        self.arguments = list(Argument.objects.filter(project=project))
        self.facts = [f.get("fact", "") for f in (self.dossier.key_facts if self.dossier else []) if f.get("fact")]
        self._fact_bm25 = BM25(self.facts) if self.facts else None

    @property
    def bm25(self) -> BM25:
        with _cache_lock:
            if self._bm25 is None:
                self._bm25 = BM25([c.text for c in self.chunks])
            return self._bm25

    def search(self, query: str, k: int = 6) -> list[Chunk]:
        if not self.chunks:
            return []
        rankings = [self.bm25.top(query, k * 3)]
        if self._matrix is not None:
            vectors = get_llm(self.project).embed([query])
            if vectors:
                rankings.append(cosine_top(vectors[0], self._matrix, k * 3))
        fused = rrf(rankings, top_k=k)
        return [self.chunks[i] for i, _ in fused]

    def entities_in(self, text: str, limit: int = 5) -> list[Entity]:
        tokens = set(tokenize(text))
        hits = []
        for entity in Entity.objects.filter(project=self.project).order_by("-salience")[:150]:
            name_tokens = set(tokenize(entity.name))
            if name_tokens and name_tokens <= tokens or entity.name.lower() in text.lower():
                hits.append(entity)
            if len(hits) >= limit:
                break
        return hits

    def graph_context(self, entities: list[Entity], limit: int = 8) -> list[str]:
        ids = [e.pk for e in entities]
        rels = Relation.objects.filter(project=self.project, source_id__in=ids).select_related("source", "target")[:limit]
        rels = list(rels) + list(Relation.objects.filter(project=self.project, target_id__in=ids).select_related("source", "target")[: max(0, limit - len(rels))])
        return [f"{r.source.name} {r.type.lower().replace('_', ' ')} {r.target.name}" + (f" ({truncate(r.description, 100)})" if r.description else "") for r in rels]

    def answer_context(self, question: str, k: int = 6, max_chars: int = 3500) -> str:
        """Evidence block for question answering (report chat, observer Q&A)."""
        parts = []
        for i, chunk in enumerate(self.search(question, k), start=1):
            label = chunk.source.title[:80] if chunk.source_id and chunk.source else "public posts"
            parts.append(f"[{i}] ({label}) {truncate(chunk.text.replace(chr(10), ' '), 600)}")
        entities = self.entities_in(question)
        graph = self.graph_context(entities)
        if graph:
            parts.append("GRAPH: " + "; ".join(graph))
        text = "\n".join(parts)
        return text[:max_chars]

    def facts_for(self, text: str, k: int = 2) -> list[str]:
        if not self._fact_bm25:
            return []
        top = self._fact_bm25.top(text, k)
        if not top:
            return self.facts[:k]
        return [self.facts[i] for i, _ in top]


def stakeholder_lines(dossier, limit: int = 4) -> list[str]:
    if not dossier:
        return []
    rows = sorted(dossier.stakeholders or [], key=lambda s: -s.get("influence", 0.5))[:limit]
    out = []
    for s in rows:
        pos = s.get("position", 0)
        label = "supports" if pos > 0.2 else "opposes" if pos < -0.2 else "is ambivalent about"
        out.append(f"{s['name']} {label} it" + (f" ({truncate(s.get('summary', ''), 90)})" if s.get("summary") else ""))
    return out


def pick_argument(arguments: list, stance: float, rng: random.Random, exclude: set | None = None):
    """Choose an argument matching an agent's stance (weighted by frequency and strength)."""
    side = "support" if stance > 0.15 else "oppose" if stance < -0.15 else "neutral"
    pool = [a for a in arguments if a.side == side and (not exclude or a.pk not in exclude)]
    if not pool:
        pool = [a for a in arguments if a.side == side] or arguments
    if not pool:
        return None
    weights = [(1 + a.frequency) * (0.5 + a.strength) for a in pool]
    return rng.choices(pool, weights=weights, k=1)[0]
