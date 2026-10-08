"""
Monte Carlo ensembles and parameter sweeps.

LLM-written runs are expensive, so a single run cannot tell you how much of an
outcome is signal and how much is chance. Ensembles re-run the *same*
population and scenario many times with different random seeds using the
dynamics-only engine (no text, about a second per run) and report median
trajectories with 10-90% bands. Sweeps vary one parameter (e.g. echo-chamber
strength or the share of coordinated accounts) to measure its effect with
95% confidence intervals.
"""

from __future__ import annotations

import copy
import math

import numpy as np

from .engine import NullWriter, SimConfig, World
from .engine.metrics import summarize_history
from .models import Agent, Experiment
from .runner import agent_to_sim, load_arguments, run_config

SWEEPABLE = {
    "echo_chamber": ("Echo-chamber strength", [0.0, 0.25, 0.5, 0.75, 1.0]),
    "influence_scale": ("Social influence strength", [0.5, 0.8, 1.2, 1.6, 2.0]),
    "backfire": ("Backfire strength", [0.0, 0.2, 0.4, 0.6, 0.8]),
    "intensify": ("Group polarisation strength", [0.0, 0.3, 0.6, 0.9, 1.2]),
    "anchoring": ("Anchoring to initial views", [0.1, 0.25, 0.35, 0.6, 1.0]),
    "activity_scale": ("Overall activity", [0.5, 0.75, 1.0, 1.5, 2.0]),
    "bot_share": ("Share of coordinated accounts", [0.0, 0.03, 0.06, 0.1, 0.15]),
}
TRACKED = ["mean", "support", "oppose", "neutral", "esteban_ray", "bimodality", "arousal", "mobilisation_index", "cross_cutting"]


def _base_agents(project):
    return [agent_to_sim(a, None) for a in Agent.objects.filter(project=project).order_by("idx")]


def _add_bots(agents, share: float, stance: float, seed: int):
    """Turn a share of ordinary citizens into coordinated accounts (for sweeps)."""
    import random

    rng = random.Random(seed)
    public = [a for a in agents if a.kind == "citizen"]
    k = int(round(len(public) * share))
    for a in rng.sample(public, min(k, len(public))):
        a.kind = "bot"
        a.bot_target = stance
        a.stance = stance
        a.initial = stance
        a.activity = 0.85
        a.credibility = 0.12
    return agents


def simulate_once(base_agents, groups, cfg: SimConfig, arguments, interventions_by_round: dict, seed: int, bot_share: float = 0.0, bot_stance: float = -0.8):
    agents = copy.deepcopy(base_agents)
    if bot_share:
        _add_bots(agents, bot_share, bot_stance, seed)
    cfg = copy.copy(cfg)
    cfg.seed = seed
    cfg.mode = "dynamics"
    world = World(agents, groups, cfg, writer=NullWriter(), arguments=arguments, start_time=cfg.start_datetime())
    from .engine.metrics import round_metrics

    first = round_metrics(world, 0, [], [])
    first["cross_cutting"] = 0.0
    history = [first]
    for r in range(1, cfg.rounds + 1):
        specs = [dict(s) for s in interventions_by_round.get(r, [])]
        history.append(world.step(specs).metrics)
    return history


def _bands(histories: list[list[dict]], key: str) -> dict:
    arr = np.array([[h.get(key, 0.0) or 0.0 for h in hist] for hist in histories], dtype=float)
    return {
        "p10": np.round(np.percentile(arr, 10, axis=0), 4).tolist(),
        "p50": np.round(np.percentile(arr, 50, axis=0), 4).tolist(),
        "p90": np.round(np.percentile(arr, 90, axis=0), 4).tolist(),
        "mean": np.round(arr.mean(axis=0), 4).tolist(),
    }


# Two-sided 95% critical values of Student's t (df 1-30); 1.96 beyond.
T95 = [12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201, 2.179, 2.160, 2.145, 2.131,
       2.120, 2.110, 2.101, 2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042]


def _ci(values: list[float]) -> dict:
    """Mean with a 95% confidence interval (Student's t), clamped to the metric's range."""
    arr = np.array(values, dtype=float)
    n = len(arr)
    mean = float(arr.mean())
    sd = float(arr.std(ddof=1)) if n > 1 else 0.0
    t = T95[n - 2] if 1 < n <= 31 else 1.96
    half = t * sd / math.sqrt(n) if n > 1 else 0.0
    low, high = mean - half, mean + half
    if arr.min() >= 0:  # shares, indices and other non-negative metrics
        low = max(0.0, low)
    if arr.min() >= -1 and arr.max() <= 1:
        low, high = max(-1.0, low), min(1.0, high)
    return {"mean": round(mean, 4), "low": round(low, 4), "high": round(high, 4), "sd": round(sd, 4)}


def _scenario_interventions(run) -> dict:
    out: dict[int, list] = {}
    if run is None:
        return out
    for iv in run.interventions.exclude(kind="web_refresh"):
        out.setdefault(iv.round, []).append(
            {"id": None, "kind": iv.kind, "title": iv.title, "content": iv.content or iv.title, "stance": iv.stance, "strength": iv.strength, "duration": iv.duration, "target_segments": iv.target_segments or []}
        )
    return out


def run_experiment(ctx, experiment_id):
    exp = Experiment.objects.select_related("project").get(pk=experiment_id)
    project = exp.project
    conf = dict(exp.config)
    runs = int(conf.get("runs", 30))
    base_run = project.runs.filter(pk=conf.get("run_id")).first() if conf.get("run_id") else None
    cfg = run_config(base_run) if base_run else SimConfig.from_dict({**conf.get("sim", {}), "language_style": project.language_style})
    if conf.get("rounds"):
        cfg.rounds = int(conf["rounds"])
    cfg.start_time = cfg.start_datetime().isoformat()  # one clock for every run in the experiment
    base_agents = _base_agents(project)
    if not base_agents:
        raise RuntimeError("Generate a population first.")
    groups = {g.idx: list(g.members) for g in project.chat_groups.all()}
    arguments = load_arguments(project)
    scenario = _scenario_interventions(base_run) if conf.get("include_interventions", True) else {}
    Experiment.objects.filter(pk=exp.pk).update(status="running", job_id=ctx.job_id)

    if exp.kind == Experiment.Kind.ENSEMBLE:
        compare_baseline = bool(scenario) and conf.get("compare_baseline", True)
        variants = {"scenario": scenario}
        if compare_baseline:
            variants = {"baseline": {}, "scenario": scenario}
        results = {"rounds": cfg.rounds, "runs": runs, "variants": {}}
        total = runs * len(variants)
        done = 0
        for name, ivs in variants.items():
            histories = []
            for i in range(runs):
                ctx.check_cancel()
                histories.append(simulate_once(base_agents, groups, cfg, arguments, ivs, seed=1000 + i))
                done += 1
                ctx.progress(100.0 * done / total, f"Ensemble: {name}", f"run {i + 1} of {runs}")
            finals = [h[-1] for h in histories]
            summaries = [summarize_history(h) for h in histories]
            results["variants"][name] = {
                "bands": {key: _bands(histories, key) for key in TRACKED},
                "final": {key: _ci([f.get(key, 0.0) or 0.0 for f in finals]) for key in TRACKED},
                "change": _ci([s.get("change", 0.0) for s in summaries]),
                "p_toward_support": round(sum(1 for s in summaries if s.get("change", 0) > 0) / len(summaries), 3),
            }
        if compare_baseline:
            b, s = results["variants"]["baseline"]["final"], results["variants"]["scenario"]["final"]
            results["effect"] = {key: round(s[key]["mean"] - b[key]["mean"], 4) for key in TRACKED}
    else:
        param = conf.get("parameter", "echo_chamber")
        label, default_values = SWEEPABLE.get(param, (param, [0.0, 0.5, 1.0]))
        values = conf.get("values") or default_values
        per_value = max(3, runs // max(1, len(values)))
        rows = []
        total = per_value * len(values)
        done = 0
        for value in values:
            finals = []
            for i in range(per_value):
                ctx.check_cancel()
                vcfg = copy.copy(cfg)
                bot_share = 0.0
                if param == "bot_share":
                    bot_share = float(value)
                else:
                    setattr(vcfg, param, float(value))
                hist = simulate_once(base_agents, groups, vcfg, arguments, scenario, seed=2000 + i, bot_share=bot_share, bot_stance=float(conf.get("bot_stance", -0.8)))
                finals.append(hist[-1])
                done += 1
                ctx.progress(100.0 * done / total, f"Sweep {label} = {value}", f"run {i + 1} of {per_value}")
            rows.append({"value": value, **{key: _ci([f.get(key, 0.0) or 0.0 for f in finals]) for key in TRACKED}})
        results = {"parameter": param, "label": label, "runs_per_value": per_value, "rows": rows, "rounds": cfg.rounds}
    Experiment.objects.filter(pk=exp.pk).update(status="completed", results=results)
    return {"experiment": str(exp.pk)}
