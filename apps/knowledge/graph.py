"""
Knowledge-graph assembly (GraphRAG step 2): entity resolution, graph
construction, salience (PageRank) and community detection (Louvain).
"""

from __future__ import annotations

import difflib
import re
from collections import Counter, defaultdict

import networkx as nx

from apps.core.graphs import pagerank

ACRONYM_SKIP = {"of", "and", "the", "for", "on", "in", "to", "a", "an", "&"}


def entity_key(name: str) -> str:
    key = name.lower().strip()
    key = re.sub(r"^(the|a|an)\s+", "", key)
    key = re.sub(r"['’]s\b", "", key)
    key = re.sub(r"[^\w&\s-]", " ", key)
    key = re.sub(r"\s+", " ", key).strip()
    return key[:200]


def acronym(name: str) -> str:
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'’-]*", name) if w.lower() not in ACRONYM_SKIP]
    if len(words) < 2:
        return ""
    return "".join(w[0] for w in words).lower()


class EntityResolver:
    """Merges entity mentions that refer to the same actor."""

    def __init__(self):
        self.entities: dict[str, dict] = {}
        self._acronyms: dict[str, str] = {}

    def resolve(self, name: str, etype: str) -> str:
        key = entity_key(name)
        if not key:
            return ""
        if key in self.entities:
            return key
        if re.fullmatch(r"[a-z]{2,7}", key) and key in self._acronyms:
            return self._acronyms[key]
        acro = acronym(name)
        if acro and acro in self.entities:
            # An acronym entity was seen first: promote the long form.
            existing = self.entities.pop(acro)
            existing["aliases"].add(existing["name"])
            existing["name"] = name
            self.entities[key] = existing
            self._acronyms[acro] = key
            return key
        for other_key, other in self.entities.items():
            if other["type"] == etype and abs(len(other_key) - len(key)) <= 6:
                if difflib.SequenceMatcher(None, key, other_key).ratio() >= 0.9:
                    return other_key
        return key

    def add(self, name: str, etype: str, description: str = "", stance=None, mentions: int = 1) -> str:
        key = self.resolve(name, etype)
        if not key:
            return ""
        entity = self.entities.get(key)
        if entity is None:
            entity = {
                "name": name,
                "type": etype,
                "descriptions": [],
                "aliases": set(),
                "mentions": 0,
                "stances": [],
                "types": Counter(),
            }
            self.entities[key] = entity
            acro = acronym(name)
            if acro:
                self._acronyms[acro] = key
        elif name != entity["name"]:
            entity["aliases"].add(name)
            if len(name) > len(entity["name"]) and not name.isupper():
                entity["aliases"].add(entity["name"])
                entity["name"] = name
        entity["mentions"] += mentions
        entity["types"][etype] += 1
        if description and description not in entity["descriptions"]:
            entity["descriptions"].append(description)
        if stance is not None:
            entity["stances"].append(float(stance))
        return key

    def finalize(self) -> dict[str, dict]:
        out = {}
        for key, e in self.entities.items():
            etype = e["types"].most_common(1)[0][0] if e["types"] else e["type"]
            out[key] = {
                "name": e["name"],
                "type": etype,
                "description": " ".join(sorted(e["descriptions"], key=len, reverse=True)[:2])[:600],
                "aliases": sorted(a for a in e["aliases"] if a != e["name"])[:8],
                "mentions": e["mentions"],
                "stance": round(sum(e["stances"]) / len(e["stances"]), 3) if e["stances"] else None,
            }
        return out


def build_graph(entities: dict[str, dict], relations: list[tuple[str, str, str, float]], cooccurrence: list[list[str]]) -> nx.Graph:
    graph = nx.Graph()
    for key, e in entities.items():
        graph.add_node(key, **{"name": e["name"], "type": e["type"], "mentions": e["mentions"]})
    for src, tgt, _rtype, weight in relations:
        if src in graph and tgt in graph and src != tgt:
            w = graph[src][tgt]["weight"] + weight if graph.has_edge(src, tgt) else weight
            graph.add_edge(src, tgt, weight=w)
    for group in cooccurrence:
        members = [k for k in dict.fromkeys(group) if k in graph]
        for i in range(len(members)):
            for j in range(i + 1, min(len(members), i + 6)):
                a, b = members[i], members[j]
                w = graph[a][b]["weight"] + 0.3 if graph.has_edge(a, b) else 0.3
                graph.add_edge(a, b, weight=w)
    return graph


def salience_and_communities(graph: nx.Graph, seed: int = 42) -> tuple[dict[str, float], dict[str, int]]:
    if graph.number_of_nodes() == 0:
        return {}, {}
    personalization = {n: 1.0 + d.get("mentions", 1) for n, d in graph.nodes(data=True)}
    ranks = pagerank(graph, weight="weight", personalization=personalization)
    top = max(ranks.values()) or 1.0
    salience = {n: round(v / top, 4) for n, v in ranks.items()}
    communities: dict[str, int] = {}
    if graph.number_of_edges():
        try:
            groups = nx.community.louvain_communities(graph, weight="weight", seed=seed)
        except Exception:
            groups = list(nx.connected_components(graph))
    else:
        groups = [{n} for n in graph]
    groups = sorted(groups, key=lambda g: -sum(salience.get(n, 0) for n in g))
    for idx, group in enumerate(groups):
        for node in group:
            communities[node] = idx
    return salience, communities


def graph_json(project, max_nodes: int = 90) -> dict:
    """Nodes/links for the D3 force graph (plus an accessible table fallback)."""
    from .models import Entity, Relation

    entities = list(Entity.objects.filter(project=project).order_by("-salience", "-mentions")[:max_nodes])
    ids = {e.pk for e in entities}
    nodes = [
        {
            "id": e.pk,
            "label": e.name,
            "type": e.type,
            "salience": e.salience,
            "mentions": e.mentions,
            "community": e.community if e.community is not None else -1,
            "stance": e.stance,
            "description": e.description[:240],
        }
        for e in entities
    ]
    weights: dict[tuple[int, int], dict] = defaultdict(lambda: {"weight": 0.0, "types": Counter()})
    for rel in Relation.objects.filter(project=project, source_id__in=ids, target_id__in=ids).only("source_id", "target_id", "type", "weight"):
        key = (rel.source_id, rel.target_id)
        weights[key]["weight"] += rel.weight
        weights[key]["types"][rel.type] += 1
    links = [
        {"source": s, "target": t, "weight": round(v["weight"], 2), "type": v["types"].most_common(1)[0][0]}
        for (s, t), v in weights.items()
    ]
    return {"nodes": nodes, "links": links}
