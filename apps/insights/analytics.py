"""
Post-run analytics.

* Trajectories: stance, support/oppose shares, polarisation, emotions, activity.
* Segments: who moved where (archetype, region, age, gender, leaning, urban/rural).
* Influence: PageRank on the interaction network (likes, reposts, replies).
* Echo chambers: Louvain communities and modularity, stance assortativity,
  Krackhardt's E-I index of cross-cutting interaction, cross-cutting exposure.
* Persuasion: which posts and arguments actually moved people (attributed
  opinion change), and which backfired.
"""

from __future__ import annotations

from collections import Counter, defaultdict

import networkx as nx
import numpy as np
from django.db.models import Count, Sum

from apps.core.graphs import pagerank as compute_pagerank
from apps.swarm.engine.config import country_timezone
from apps.swarm.engine.metrics import distribution_metrics
from apps.swarm.models import Action, Agent, AgentState, Post, SimulationRun, stance_label

SEGMENT_FIELDS = {
    "archetype": ("Segment", lambda a: a.archetype_label),
    "region": ("Region", lambda a: a.region_group or a.region),
    "age": ("Age", lambda a: a.age_band),
    "gender": ("Gender", lambda a: (a.gender or "n/a").title()),
    "leaning": ("Political leaning", lambda a: (a.leaning or "n/a").replace("_", " ").title()),
    "setting": ("Setting", lambda a: "Urban" if a.urban else "Rural"),
}


def side(value: float) -> str:
    return "support" if value > 0.2 else "oppose" if value < -0.2 else "neutral"


def trajectory(run: SimulationRun) -> dict:
    snaps = list(run.snapshots.order_by("round").values("round", "sim_time", "metrics"))
    keys = ["mean", "support", "oppose", "neutral", "esteban_ray", "bimodality", "arousal", "sentiment", "mobilisation_index", "posts", "active", "cross_cutting", "salience"]
    series = {k: [s["metrics"].get(k) for s in snaps] for k in keys}
    emotions = sorted({e for s in snaps for e in (s["metrics"].get("emotions") or {})})
    emotion_series = {e: [(s["metrics"].get("emotions") or {}).get(e, 0) for s in snaps] for e in emotions}
    events = [{"round": s["round"], "events": s["metrics"].get("events", [])} for s in snaps if s["metrics"].get("events")]
    tz = country_timezone(run.project.country)
    labels = [s["sim_time"].astimezone(tz).strftime("%a %H:%M") if s["sim_time"] else f"R{s['round']}" for s in snaps]
    return {"rounds": [s["round"] for s in snaps], "labels": labels, "series": series, "emotions": emotion_series, "events": events}


def segments(run: SimulationRun) -> dict:
    states = {s.agent_id: s for s in AgentState.objects.filter(run=run)}
    public = [a for a in Agent.objects.filter(project=run.project, kind__in=["citizen", "influencer"])]
    out = {}
    for key, (label, fn) in SEGMENT_FIELDS.items():
        groups: dict[str, list] = defaultdict(list)
        for agent in public:
            state = states.get(agent.pk)
            if state is None:
                continue
            groups[fn(agent)].append((agent.initial_stance, state.stance, state.arousal))
        rows = []
        for name, values in groups.items():
            if not name:
                continue
            initial = float(np.mean([v[0] for v in values]))
            final = float(np.mean([v[1] for v in values]))
            finals = np.array([v[1] for v in values])
            rows.append(
                {
                    "name": name,
                    "n": len(values),
                    "initial": round(initial, 3),
                    "final": round(final, 3),
                    "change": round(final - initial, 3),
                    "support": round(float((finals > 0.2).mean()), 3),
                    "oppose": round(float((finals < -0.2).mean()), 3),
                    "arousal": round(float(np.mean([v[2] for v in values])), 3),
                    "label": stance_label(final),
                }
            )
        rows.sort(key=lambda r: r["final"])
        out[key] = {"label": label, "rows": rows}
    return out


def interaction_graph(run: SimulationRun) -> nx.DiGraph:
    graph = nx.DiGraph()
    authors = dict(Post.objects.filter(run=run).values_list("id", "author_id"))
    weights: Counter = Counter()
    for agent_id, post_id, kind in Action.objects.filter(run=run, kind__in=["like", "repost"]).values_list("agent_id", "post_id", "kind"):
        author = authors.get(post_id)
        if author and author != agent_id:
            weights[(agent_id, author)] += 2 if kind == "repost" else 1
    for author_id, parent_id in Post.objects.filter(run=run, parent__isnull=False).values_list("author_id", "parent_id"):
        target = authors.get(parent_id)
        if target and target != author_id:
            weights[(author_id, target)] += 1.5
    for (src, dst), w in weights.items():
        graph.add_edge(src, dst, weight=w)
    return graph


def network(run: SimulationRun, graph: nx.DiGraph | None = None) -> dict:
    graph = graph if graph is not None else interaction_graph(run)
    states = {s.agent_id: s for s in AgentState.objects.filter(run=run).select_related("agent")}
    result = {"nodes": graph.number_of_nodes(), "edges": graph.number_of_edges()}
    if graph.number_of_nodes() < 3:
        result.update({"influencers": [], "modularity": None, "communities": [], "assortativity": None, "ei_index": None})
        return result
    pagerank = compute_pagerank(graph, weight="weight")
    influencers = []
    for agent_id, score in sorted(pagerank.items(), key=lambda kv: -kv[1])[:12]:
        state = states.get(agent_id)
        if state is None:
            continue
        agent = state.agent
        influencers.append(
            {
                "id": agent_id, "handle": agent.handle, "name": agent.name, "kind": agent.get_kind_display(),
                "archetype": agent.archetype_label, "stance": round(state.stance, 3), "label": stance_label(state.stance),
                "followers": state.followers, "posts": state.posts, "pagerank": round(score, 4),
            }
        )
    undirected = graph.to_undirected()
    try:
        communities = nx.community.louvain_communities(undirected, weight="weight", seed=7)
        modularity = nx.community.modularity(undirected, communities, weight="weight")
    except Exception:
        communities, modularity = [], None
    community_rows = []
    for i, members in enumerate(sorted(communities, key=len, reverse=True)[:8]):
        stances = [states[m].stance for m in members if m in states]
        if not stances:
            continue
        community_rows.append({"index": i, "size": len(members), "mean_stance": round(float(np.mean(stances)), 3), "label": stance_label(float(np.mean(stances))), "support": round(float(np.mean([s > 0.2 for s in stances])), 3), "oppose": round(float(np.mean([s < -0.2 for s in stances])), 3)})
    for node in undirected.nodes:
        undirected.nodes[node]["stance"] = states[node].stance if node in states else 0.0
    try:
        assortativity = nx.numeric_assortativity_coefficient(undirected, "stance")
        assortativity = None if assortativity != assortativity else round(float(assortativity), 4)
    except Exception:
        assortativity = None
    internal = external = 0.0
    for u, v, data in graph.edges(data=True):
        if u in states and v in states:
            su, sv = side(states[u].stance), side(states[v].stance)
            if "neutral" in (su, sv):
                continue
            if su == sv:
                internal += data["weight"]
            else:
                external += data["weight"]
    ei = round((external - internal) / (external + internal), 4) if external + internal else None
    result.update({"influencers": influencers, "modularity": round(modularity, 4) if modularity is not None else None, "communities": community_rows, "assortativity": assortativity, "ei_index": ei})
    return result


def network_json(run: SimulationRun, limit: int = 160) -> dict:
    graph = interaction_graph(run)
    states = {s.agent_id: s for s in AgentState.objects.filter(run=run).select_related("agent")}
    degree = dict(graph.degree(weight="weight"))
    nodes = sorted(degree, key=lambda n: -degree[n])[:limit]
    keep = set(nodes)
    return {
        "nodes": [
            {"id": n, "label": f"@{states[n].agent.handle}", "kind": states[n].agent.kind, "stance": round(states[n].stance, 3), "size": round(1 + degree[n] ** 0.5, 2), "archetype": states[n].agent.archetype_label}
            for n in nodes
            if n in states
        ],
        "links": [{"source": u, "target": v, "weight": d["weight"]} for u, v, d in graph.edges(data=True) if u in keep and v in keep],
    }


def posts_summary(run: SimulationRun) -> dict:
    qs = Post.objects.filter(run=run).select_related("author")
    top_engaged = list(qs.order_by("-likes", "-reposts")[:8])
    persuasive = list(qs.filter(influence__gt=0).order_by("-influence")[:8])
    backfired = list(qs.filter(backfired__gt=0).order_by("-backfired")[:5])
    sources = dict(qs.values_list("source").annotate(n=Count("id")).values_list("source", "n"))
    platforms = dict(qs.values_list("platform").annotate(n=Count("id")).values_list("platform", "n"))
    hashtags: Counter = Counter()
    for tags in qs.values_list("hashtags", flat=True):
        hashtags.update(t.lower() for t in tags or [])
    misinfo = qs.filter(is_misinfo=True).aggregate(n=Count("id"), views=Sum("views"))
    return {
        "top_engaged": top_engaged,
        "persuasive": persuasive,
        "backfired": backfired,
        "sources": sources,
        "platforms": platforms,
        "hashtags": hashtags.most_common(12),
        "misinfo": {"posts": misinfo["n"] or 0, "views": misinfo["views"] or 0},
        "total": qs.count(),
    }


def argument_effectiveness(run: SimulationRun) -> list[dict]:
    rows = (
        Post.objects.filter(run=run, argument__isnull=False)
        .values("argument_id", "argument__side", "argument__claim", "argument__short")
        .annotate(posts=Count("id"), views=Sum("views"), influence=Sum("influence"), persuaded=Sum("persuaded"), backfired=Sum("backfired"), likes=Sum("likes"))
    )
    out = []
    for r in rows:
        views = r["views"] or 0
        out.append(
            {
                "id": r["argument_id"],
                "side": r["argument__side"],
                "claim": r["argument__short"] or r["argument__claim"],
                "posts": r["posts"],
                "views": views,
                "likes": r["likes"] or 0,
                "influence": round(r["influence"] or 0.0, 4),
                "persuaded": r["persuaded"] or 0,
                "backfired": r["backfired"] or 0,
                "persuasion_rate": round(100.0 * (r["persuaded"] or 0) / views, 2) if views else 0.0,
                "backfire_rate": round(100.0 * (r["backfired"] or 0) / views, 2) if views else 0.0,
            }
        )
    out.sort(key=lambda r: -r["influence"])
    return out


def movers(run: SimulationRun, k: int = 6) -> dict:
    states = list(AgentState.objects.filter(run=run, agent__kind__in=["citizen", "influencer"]).select_related("agent"))
    for s in states:
        s.delta = s.stance - s.agent.initial_stance
    states.sort(key=lambda s: s.delta)
    return {"toward_oppose": states[:k], "toward_support": list(reversed(states[-k:]))}


def distributions(run: SimulationRun) -> dict:
    agents = {a.pk: a for a in Agent.objects.filter(project=run.project, kind__in=["citizen", "influencer"])}
    initial = [a.initial_stance for a in agents.values()]
    final = list(AgentState.objects.filter(run=run, agent_id__in=agents.keys()).values_list("stance", flat=True))
    return {"initial": distribution_metrics(initial), "final": distribution_metrics(final)}


def full_analytics(run: SimulationRun) -> dict:
    graph = interaction_graph(run)
    return {
        "trajectory": trajectory(run),
        "segments": segments(run),
        "network": network(run, graph),
        "arguments": argument_effectiveness(run),
        "posts": posts_summary(run),
        "distributions": distributions(run),
        "summary": run.summary or {},
    }


def compare_runs(a: SimulationRun, b: SimulationRun) -> dict:
    ta, tb = trajectory(a), trajectory(b)
    sa, sb = segments(a), segments(b)
    seg_rows = []
    rows_b = {r["name"]: r for r in sb["archetype"]["rows"]}
    for row in sa["archetype"]["rows"]:
        other = rows_b.get(row["name"])
        if other:
            seg_rows.append({"name": row["name"], "a": row["final"], "b": other["final"], "diff": round(other["final"] - row["final"], 3)})
    seg_rows.sort(key=lambda r: -abs(r["diff"]))
    fa, fb = (a.summary or {}), (b.summary or {})
    return {
        "a": ta,
        "b": tb,
        "segments": seg_rows,
        "final": {
            "mean": (fa.get("end_mean"), fb.get("end_mean")),
            "support": (fa.get("end_support"), fb.get("end_support")),
            "oppose": (fa.get("end_oppose"), fb.get("end_oppose")),
            "polarisation": (fa.get("end_polarisation"), fb.get("end_polarisation")),
            "mobilisation": (fa.get("peak_mobilisation"), fb.get("peak_mobilisation")),
        },
    }
