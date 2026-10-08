"""
Per-round opinion metrics, computed over members of the public only
(citizens and influencers; institutions, media and coordinated accounts are
excluded so they cannot inflate "public opinion").

Polarisation is measured several ways because no single index is accepted:
variance, the Esteban-Ray index (normalised), the bimodality coefficient,
group divergence and the share of extreme opinions.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict

import numpy as np

SUPPORT_T = 0.2
OPPOSE_T = -0.2
HIST_EDGES = np.linspace(-1.0, 1.0, 11)
MOBILISE_RE_WORDS = ("speak up", "make noise", "show up", "call your", "protest", "boycott", "occupy", "march", "reject", "maandamano", "tuesday", "streets")


def esteban_ray(values: np.ndarray, alpha: float = 1.6, bins: int = 10) -> float:
    if values.size < 2:
        return 0.0
    counts, edges = np.histogram(values, bins=bins, range=(-1.0, 1.0))
    pi = counts / counts.sum()
    y = (edges[:-1] + edges[1:]) / 2
    diff = np.abs(y[:, None] - y[None, :])
    raw = float(np.sum((pi[:, None] ** (1 + alpha)) * pi[None, :] * diff))
    # Normalise by the maximum: two equal masses at the extremes of the bins.
    pmax = np.zeros(bins)
    pmax[0] = pmax[-1] = 0.5
    max_raw = float(np.sum((pmax[:, None] ** (1 + alpha)) * pmax[None, :] * diff))
    return round(raw / max_raw, 4) if max_raw else 0.0


def bimodality(values: np.ndarray) -> float:
    n = values.size
    if n < 4 or float(values.std()) < 1e-9:
        return 0.0
    mean = values.mean()
    std = values.std(ddof=0)
    skew = float(np.mean(((values - mean) / std) ** 3))
    kurt = float(np.mean(((values - mean) / std) ** 4)) - 3.0
    denom = kurt + 3.0 * (n - 1) ** 2 / ((n - 2) * (n - 3))
    return round((skew**2 + 1.0) / denom, 4) if denom else 0.0


def distribution_metrics(values: list[float] | np.ndarray) -> dict:
    v = np.asarray(values, dtype=float)
    if v.size == 0:
        return {"n": 0}
    pos, neg = v[v > SUPPORT_T], v[v < OPPOSE_T]
    hist, _ = np.histogram(v, bins=HIST_EDGES)
    return {
        "n": int(v.size),
        "mean": round(float(v.mean()), 4),
        "median": round(float(np.median(v)), 4),
        "std": round(float(v.std()), 4),
        "support": round(float((v > SUPPORT_T).mean()), 4),
        "oppose": round(float((v < OPPOSE_T).mean()), 4),
        "neutral": round(float(((v >= OPPOSE_T) & (v <= SUPPORT_T)).mean()), 4),
        "extreme": round(float((np.abs(v) > 0.6).mean()), 4),
        "net": round(float((v > SUPPORT_T).mean() - (v < OPPOSE_T).mean()), 4),
        "esteban_ray": esteban_ray(v),
        "bimodality": bimodality(v),
        "divergence": round(float(pos.mean() - neg.mean()), 4) if pos.size and neg.size else 0.0,
        "histogram": [int(x) for x in hist],
    }


def segment_means(agents, key_fn) -> dict:
    groups = defaultdict(list)
    for a in agents:
        groups[key_fn(a)].append(a.stance)
    return {k: {"mean": round(sum(v) / len(v), 4), "n": len(v)} for k, v in groups.items() if k}


def round_metrics(world, active: int, new_posts: list, actions: list) -> dict:
    public = [a for a in world.agents if a.is_public]
    stances = np.array([a.stance for a in public])
    metrics = distribution_metrics(stances)
    emotions = Counter(a.emotion for a in public)
    action_counts = Counter(kind for kind, *_ in actions)
    platform_counts = Counter(p.platform for p in new_posts)
    hashtag_counts: Counter = Counter()
    sentiments = []
    mobilising = 0
    misinfo = 0
    for p in new_posts:
        hashtag_counts.update(h.lower() for h in p.hashtags)
        sentiments.append(p.sentiment)
        low = p.text.lower()
        if any(w in low for w in MOBILISE_RE_WORDS):
            mobilising += 1
        if p.is_misinfo:
            misinfo += 1
    angry_opposed = sum(1 for a in public if a.stance < -0.5 and a.arousal > 0.55)
    metrics.update(
        {
            "active": active,
            "posts": len(new_posts),
            "posts_by_platform": dict(platform_counts),
            "actions": dict(action_counts),
            "arousal": round(float(np.mean([a.arousal for a in public])) if public else 0.0, 4),
            "emotions": dict(emotions),
            "sentiment": round(float(np.mean(sentiments)), 4) if sentiments else None,
            "salience": round(world.salience, 4),
            "hashtags": hashtag_counts.most_common(8),
            "mobilising_posts": mobilising,
            "misinfo_posts": misinfo,
            "mobilisation_index": round(angry_opposed / len(public), 4) if public else 0.0,
            "segments": {
                "archetype": segment_means(public, lambda a: a.archetype_label),
                "region": segment_means(public, lambda a: a.region_group or a.region),
                "age": segment_means(public, lambda a: a.age_band),
                "gender": segment_means(public, lambda a: a.gender),
                "leaning": segment_means(public, lambda a: a.leaning),
                "setting": segment_means(public, lambda a: "Urban" if a.urban else "Rural"),
            },
            "institutions": {a.handle: round(a.stance, 3) for a in world.agents if a.kind in ("institution", "media")},
        }
    )
    return metrics


def summarize_history(snapshots: list[dict]) -> dict:
    """Headline numbers comparing the first and last rounds."""
    if not snapshots:
        return {}
    first, last = snapshots[0], snapshots[-1]
    means = [s.get("mean", 0.0) for s in snapshots]
    deltas = [b - a for a, b in zip(means, means[1:])]
    tipping = []
    if len(deltas) >= 3:
        sd = float(np.std(deltas)) or 1e-9
        mu = float(np.mean(deltas))
        tipping = [i + 2 for i, d in enumerate(deltas) if abs(d - mu) > 2 * sd and abs(d) > 0.01]
    return {
        "start_mean": first.get("mean"),
        "end_mean": last.get("mean"),
        "change": round((last.get("mean") or 0) - (first.get("mean") or 0), 4),
        "start_support": first.get("support"),
        "end_support": last.get("support"),
        "start_oppose": first.get("oppose"),
        "end_oppose": last.get("oppose"),
        "peak_oppose": max(s.get("oppose", 0) for s in snapshots),
        "peak_mobilisation": max(s.get("mobilisation_index", 0) for s in snapshots),
        "end_polarisation": last.get("esteban_ray"),
        "end_bimodality": last.get("bimodality"),
        "tipping_rounds": tipping,
        "total_posts": sum(s.get("posts", 0) for s in snapshots),
        "max_arousal": max(s.get("arousal", 0) for s in snapshots),
        "trend": "toward support" if (last.get("mean") or 0) - (first.get("mean") or 0) > 0.03 else "toward opposition" if (last.get("mean") or 0) - (first.get("mean") or 0) < -0.03 else "stable",
    }


def sigmoid(x: float) -> float:
    if x < -30:
        return 0.0
    if x > 30:
        return 1.0
    return 1.0 / (1.0 + math.exp(-x))
