"""
Ground-truth alignment: how close is the simulated discourse to real discourse?

Compares the simulated posts with real posts - either the public posts found
during research, or a CSV you upload (e.g. a historical X/Reddit export about a
past policy) - on three distributions:

* sentiment (VADER + African slang lexicon applied identically to both),
* stance toward the policy (when the real posts have LLM stance labels),
* emotion (lexicon-based).

Metrics: Jensen-Shannon divergence (base 2, 0 = identical, 1 = disjoint),
1-D Wasserstein (earth mover's) distance, the Kolmogorov-Smirnov statistic and
the difference in means. ``fit_score`` = 1 - mean JSD across available
dimensions. Vocabulary overlap is the Jaccard index of the top terms.
"""

from __future__ import annotations

from collections import Counter

import numpy as np

from apps.core.text.sentiment import EMOTIONS, emotion, sentiment
from apps.core.text.textutils import top_terms

from .models import ValidationDataset, ValidationResult

BINS = np.linspace(-1.0, 1.0, 11)


def histogram(values: list[float]) -> np.ndarray:
    counts, _ = np.histogram(np.clip(values, -1, 1), bins=BINS)
    return counts.astype(float)


def jsd(p: np.ndarray, q: np.ndarray) -> float:
    p = np.asarray(p, dtype=float) + 1e-12
    q = np.asarray(q, dtype=float) + 1e-12
    p, q = p / p.sum(), q / q.sum()
    m = 0.5 * (p + q)
    kl = lambda a, b: float(np.sum(a * np.log2(a / b)))  # noqa: E731
    return round(0.5 * kl(p, m) + 0.5 * kl(q, m), 4)


def wasserstein(a: list[float], b: list[float]) -> float:
    a, b = np.sort(np.asarray(a, dtype=float)), np.sort(np.asarray(b, dtype=float))
    if not a.size or not b.size:
        return float("nan")
    grid = np.sort(np.concatenate([a, b]))
    cdf_a = np.searchsorted(a, grid, side="right") / a.size
    cdf_b = np.searchsorted(b, grid, side="right") / b.size
    return round(float(np.sum(np.abs(cdf_a - cdf_b)[:-1] * np.diff(grid))), 4)


def ks(a: list[float], b: list[float]) -> float:
    a, b = np.sort(np.asarray(a, dtype=float)), np.sort(np.asarray(b, dtype=float))
    if not a.size or not b.size:
        return float("nan")
    grid = np.concatenate([a, b])
    return round(float(np.max(np.abs(np.searchsorted(a, grid, side="right") / a.size - np.searchsorted(b, grid, side="right") / b.size))), 4)


def compare(real: list[float], sim: list[float]) -> dict:
    return {
        "jsd": jsd(histogram(real), histogram(sim)),
        "wasserstein": wasserstein(real, sim),
        "ks": ks(real, sim),
        "mean_real": round(float(np.mean(real)), 4),
        "mean_sim": round(float(np.mean(sim)), 4),
        "hist_real": (histogram(real) / max(1, len(real))).round(4).tolist(),
        "hist_sim": (histogram(sim) / max(1, len(sim))).round(4).tolist(),
    }


def emotion_dist(texts: list[str]) -> np.ndarray:
    counts = Counter(emotion(t)[0] for t in texts)
    return np.array([counts.get(e, 0) for e in EMOTIONS], dtype=float)


def validate(run, dataset: ValidationDataset | None = None) -> ValidationResult:
    project = run.project
    if dataset is not None:
        real_texts = [t for t in dataset.texts if t]
        real_stance = [float(s) for s in dataset.stances if s is not None]
        source = "upload"
    else:
        from apps.research.models import Source

        social = project.sources.filter(included=True, platform__in=Source.SOCIAL_PLATFORMS)
        real_texts = [s.body for s in social if s.body]
        real_stance = [s.stance for s in social if s.stance is not None]
        source = "research"
    sim_posts = list(run.posts.exclude(source="intervention").exclude(text="").values_list("text", "stance"))
    sim_texts = [t for t, _ in sim_posts]
    sim_stance = [s for _, s in sim_posts]
    metrics: dict = {"n_real": len(real_texts), "n_sim": len(sim_texts), "source": source}
    if len(real_texts) < 5 or len(sim_texts) < 5:
        metrics["error"] = "Not enough posts to compare (need at least 5 real and 5 simulated posts)."
        return ValidationResult.objects.create(run=run, dataset=dataset, source=source, metrics=metrics)
    real_sent = [sentiment(t) for t in real_texts]
    sim_sent = [sentiment(t) for t in sim_texts]
    metrics["sentiment"] = compare(real_sent, sim_sent)
    metrics["sentiment_jsd"] = metrics["sentiment"]["jsd"]
    if len(real_stance) >= 5:
        metrics["stance"] = compare(real_stance, sim_stance)
        metrics["stance_jsd"] = metrics["stance"]["jsd"]
    er, es = emotion_dist(real_texts), emotion_dist(sim_texts)
    metrics["emotion"] = {"labels": EMOTIONS, "real": (er / er.sum()).round(4).tolist(), "sim": (es / es.sum()).round(4).tolist(), "jsd": jsd(er, es)}
    metrics["emotion_jsd"] = metrics["emotion"]["jsd"]
    real_terms = {t for t, _ in top_terms(real_texts, 40)}
    sim_terms = {t for t, _ in top_terms(sim_texts, 40)}
    metrics["vocabulary_overlap"] = round(len(real_terms & sim_terms) / len(real_terms | sim_terms), 4) if real_terms | sim_terms else 0.0
    metrics["shared_terms"] = sorted(real_terms & sim_terms)[:20]
    jsds = [metrics[k] for k in ("sentiment_jsd", "stance_jsd", "emotion_jsd") if k in metrics]
    metrics["fit_score"] = round(1.0 - float(np.mean(jsds)), 4)
    return ValidationResult.objects.create(run=run, dataset=dataset, source=source, metrics=metrics)


def dataset_from_upload(project, name: str, uploaded_file) -> ValidationDataset:
    from apps.research.extract import parse_social_csv

    data = uploaded_file.read()
    rows = parse_social_csv(data, limit=20000)
    texts = [r["text"] for r in rows]
    stances = []
    uploaded_file.seek(0)
    dataset = ValidationDataset(project=project, name=name or uploaded_file.name, texts=texts, stances=stances)
    dataset.file.save(uploaded_file.name, uploaded_file, save=False)
    dataset.save()
    return dataset
