"""Graph helpers that only need numpy (no SciPy dependency)."""

from __future__ import annotations

import networkx as nx
import numpy as np


def pagerank(graph: nx.Graph, *, alpha: float = 0.85, weight: str = "weight", personalization: dict | None = None, max_iter: int = 200, tol: float = 1e-9) -> dict:
    """Weighted PageRank by power iteration (handles dangling nodes)."""
    nodes = list(graph.nodes())
    n = len(nodes)
    if n == 0:
        return {}
    index = {node: i for i, node in enumerate(nodes)}
    matrix = np.zeros((n, n), dtype=float)
    directed = graph.is_directed()
    for u, v, data in graph.edges(data=True):
        w = float(data.get(weight, 1.0) or 0.0)
        matrix[index[u], index[v]] += w
        if not directed:
            matrix[index[v], index[u]] += w
    out = matrix.sum(axis=1)
    dangling = out == 0
    out[dangling] = 1.0
    transition = matrix / out[:, None]
    if personalization:
        p = np.array([float(personalization.get(node, 0.0)) for node in nodes])
        p = p / p.sum() if p.sum() > 0 else np.full(n, 1.0 / n)
    else:
        p = np.full(n, 1.0 / n)
    rank = np.full(n, 1.0 / n)
    for _ in range(max_iter):
        new = alpha * (rank @ transition + rank[dangling].sum() * p) + (1 - alpha) * p
        if np.abs(new - rank).sum() < tol * n:
            rank = new
            break
        rank = new
    rank = rank / rank.sum()
    return {node: float(rank[i]) for i, node in enumerate(nodes)}
