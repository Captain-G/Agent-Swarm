"""Population, simulation runs (live), interventions, exports and experiments."""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.geo import ARCHETYPES, get_country
from apps.core.jobs import active_job, cancel, enqueue
from apps.core.llm import as_float
from apps.projects.models import Project

from .engine import PRESETS, SimConfig
from .engine.config import country_timezone
from .ensemble import SWEEPABLE
from .models import Agent, AgentState, ChatGroup, Experiment, Intervention, Post, SimulationRun
from .services import clone_run, create_run, start_run


def _f(value, default, lo=None, hi=None):
    return as_float(value, default, lo, hi)


# ---------------------------------------------------------------------------
# Population
# ---------------------------------------------------------------------------


def population(request, pk):
    project = get_object_or_404(Project, pk=pk)
    country = get_country(project.country)
    agents = project.agents.all()
    kind = request.GET.get("kind", "")
    archetype = request.GET.get("archetype", "")
    q = (request.GET.get("q") or "").strip()
    qs = agents
    if kind:
        qs = qs.filter(kind=kind)
    if archetype:
        qs = qs.filter(archetype=archetype)
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(handle__icontains=q) | Q(region__icontains=q))
    page = Paginator(qs.order_by("idx"), 24).get_page(request.GET.get("page"))

    public = [a for a in agents if a.kind in ("citizen", "influencer")]
    by_arch = defaultdict(list)
    by_region = defaultdict(list)
    for a in public:
        by_arch[a.archetype_label].append(a.initial_stance)
        by_region[a.region_group or a.region].append(a.initial_stance)
    arch_rows = sorted(({"name": k, "n": len(v), "mean": round(sum(v) / len(v), 3)} for k, v in by_arch.items()), key=lambda r: r["mean"])
    region_rows = sorted(({"name": k, "n": len(v), "mean": round(sum(v) / len(v), 3)} for k, v in by_region.items()), key=lambda r: r["mean"])
    hist = [0] * 10
    for a in public:
        hist[min(9, int((a.initial_stance + 1) / 0.2))] += 1
    last_job = project.jobs.filter(kind="population", status="succeeded").order_by("-created_at").first()
    return render(
        request,
        "swarm/population.html",
        {
            "project": project,
            "country": country,
            "page": page,
            "kind": kind,
            "archetype": archetype,
            "q": q,
            "kinds": Counter(a.kind for a in agents),
            "total": agents.count(),
            "groups": ChatGroup.objects.filter(project=project).count(),
            "arch_rows": arch_rows,
            "region_rows": region_rows,
            "hist": hist,
            "archetypes": sorted({(a.archetype, a.archetype_label) for a in public}, key=lambda x: x[1]),
            "all_archetypes": ARCHETYPES,
            "priors": (last_job.result or {}).get("priors", {}) if last_job else {},
            "calibration": (last_job.result or {}).get("calibration", {}) if last_job else {},
            "job": active_job(project, ["population", "autopilot"]),
            "has_runs": project.runs.exists(),
            "active_step": "population",
            "chart_data": {"arch": arch_rows, "region": region_rows, "hist": hist},
        },
    )


@require_POST
def generate_population(request, pk):
    project = get_object_or_404(Project, pk=pk)
    if active_job(project, ["population", "autopilot", "simulation"]):
        messages.info(request, "Please wait for the running task to finish.")
        return redirect("swarm:population", pk=pk)
    regions = [r.strip() for r in (request.POST.get("focus_regions") or "").split(",") if r.strip()]
    config = {
        "size": int(_f(request.POST.get("size"), 150, 20, 2000)),
        "focus_regions": regions or project.focus_regions,
        "include_institutions": request.POST.get("include_institutions") == "on",
        "max_institutions": int(_f(request.POST.get("max_institutions"), 10, 0, 30)),
        "influencer_share": _f(request.POST.get("influencer_share"), 0.04, 0, 0.3),
        "bot_share": _f(request.POST.get("bot_share"), 0.0, 0, 0.4),
        "bot_stance": _f(request.POST.get("bot_stance"), -0.8, -1, 1),
        "calibrate": request.POST.get("calibrate") == "on",
        "calibration_weight": _f(request.POST.get("calibration_weight"), 0.5, 0, 1),
        "llm_bios": int(_f(request.POST.get("llm_bios"), 24, 0, 300)),
        "seed": int(_f(request.POST.get("seed"), 42, 0, 10**6)),
    }
    enqueue("apps.swarm.population.run_population", kind="population", label=f"Population: {project.title}", project=project, payload={"project_id": str(project.pk), "config": config})
    messages.success(request, "Creating the synthetic population…")
    return redirect("swarm:population", pk=pk)


def agent_detail(request, pk, agent_id):
    agent = get_object_or_404(Agent, pk=agent_id, project_id=pk)
    run = None
    state = None
    posts = []
    run_id = request.GET.get("run")
    if run_id:
        run = get_object_or_404(SimulationRun, pk=run_id, project_id=pk)
        state = AgentState.objects.filter(run=run, agent=agent).first()
        posts = list(Post.objects.filter(run=run, author=agent).order_by("-seq")[:8])
    country = get_country(agent.project.country)
    following = Agent.objects.filter(project_id=pk, idx__in=(state.following if state else agent.following)[:12])
    return render(
        request,
        "swarm/partials/agent_detail.html",
        {"agent": agent, "run": run, "state": state, "posts": posts, "country": country, "following": following, "traits": [("Openness", "o"), ("Conscientiousness", "c"), ("Extraversion", "e"), ("Agreeableness", "a"), ("Neuroticism", "n")]},
    )


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

INTERVENTION_FIELDS = ("round", "kind", "title", "content", "actor", "stance", "strength", "duration", "target_segments")


def _parse_interventions(raw: str, max_round: int) -> list[dict]:
    try:
        rows = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or row.get("kind") not in Intervention.Kind.values:
            continue
        if not (row.get("content") or row.get("title")) and row.get("kind") not in ("web_refresh", "influencer_campaign"):
            continue
        segments = row.get("target_segments") or []
        if isinstance(segments, str):
            segments = [s.strip() for s in segments.split(",") if s.strip()]
        out.append(
            {
                "round": int(_f(row.get("round"), 1, 1, max_round)),
                "kind": row["kind"],
                "title": str(row.get("title") or "")[:200],
                "content": str(row.get("content") or "")[:2000],
                "actor": str(row.get("actor") or "")[:200],
                "stance": _f(row.get("stance"), 0.0, -1, 1),
                "strength": _f(row.get("strength"), 0.7, 0, 1),
                "duration": int(_f(row.get("duration"), 2, 1, 50)),
                "target_segments": segments[:20],
            }
        )
    return out


def runs(request, pk):
    project = get_object_or_404(Project, pk=pk)
    country = get_country(project.country)
    institutions = project.agents.filter(kind__in=["institution", "media"]).values_list("handle", "name")
    segments = sorted({(a.archetype, a.archetype_label) for a in project.agents.filter(kind__in=["citizen", "influencer"])}, key=lambda x: x[1])
    return render(
        request,
        "swarm/runs.html",
        {
            "project": project,
            "runs": project.runs.order_by("-created_at"),
            "presets": PRESETS,
            "defaults": SimConfig(),
            "intervention_kinds": Intervention.Kind.choices,
            "institutions": list(institutions),
            "segments": segments,
            "regions": [r.name for r in country.regions][:60],
            "has_population": project.agents.exists(),
            "has_arguments": project.arguments.exists(),
            "job": active_job(project, ["simulation", "autopilot"]),
            "active_step": "simulate",
            "experiments": project.experiments.all()[:10],
            "sweepable": SWEEPABLE,
        },
    )


@require_POST
def run_create(request, pk):
    project = get_object_or_404(Project, pk=pk)
    if not project.agents.exists():
        messages.error(request, "Create a population first.")
        return redirect("swarm:population", pk=pk)
    preset = request.POST.get("preset") if request.POST.get("preset") in PRESETS else "standard"
    overrides = {}
    if request.POST.get("advanced") == "1":
        overrides = {
            "rounds": int(_f(request.POST.get("rounds"), 24, 1, 500)),
            "step_hours": _f(request.POST.get("step_hours"), 4, 0.5, 168),
            "llm_posts_per_round": int(_f(request.POST.get("llm_posts_per_round"), 6, 0, 100)),
            "echo_chamber": _f(request.POST.get("echo_chamber"), 0.5, 0, 1),
            "influence_scale": _f(request.POST.get("influence_scale"), 1.2, 0, 5),
            "backfire": _f(request.POST.get("backfire"), 0.3, 0, 2),
            "intensify": _f(request.POST.get("intensify"), 0.6, 0, 2),
            "anchoring": _f(request.POST.get("anchoring"), 0.35, 0, 3),
            "feed_size": int(_f(request.POST.get("feed_size"), 8, 2, 30)),
            "activity_scale": _f(request.POST.get("activity_scale"), 1.0, 0.1, 5),
            "seed": int(_f(request.POST.get("seed"), 7, 0, 10**6)),
            "platforms": [p for p in request.POST.getlist("platforms") if p in ("microblog", "forum", "chat")] or ["microblog", "forum", "chat"],
        }
        mode = request.POST.get("mode")
        if mode in ("hybrid", "template"):
            overrides["mode"] = mode
    sim_preview = SimConfig.from_dict({**PRESETS[preset]["config"], **overrides})
    interventions = _parse_interventions(request.POST.get("interventions"), sim_preview.rounds)
    run = create_run(
        project,
        name=(request.POST.get("name") or f"{PRESETS[preset]['label']} run").strip(),
        preset=preset,
        overrides=overrides,
        description=(request.POST.get("description") or "").strip()[:2000],
        interventions=interventions,
        baseline=project.runs.filter(pk=request.POST.get("baseline")).first() if request.POST.get("baseline") else None,
    )
    start_run(run)
    messages.success(request, "Simulation started.")
    return redirect("swarm:run_detail", run_id=run.pk)


def run_detail(request, run_id):
    run = get_object_or_404(SimulationRun.objects.select_related("project"), pk=run_id)
    project = run.project
    groups = ChatGroup.objects.filter(project=project)
    return render(
        request,
        "swarm/run_detail.html",
        {
            "project": project,
            "run": run,
            "interventions": run.interventions.all(),
            "intervention_kinds": Intervention.Kind.choices,
            "institutions": list(project.agents.filter(kind__in=["institution", "media"]).values_list("handle", "name")),
            "groups": groups[:200],
            "active_step": "simulate",
            "config_rows": sorted((run.config or {}).items()),
        },
    )


def run_metrics(request, run_id):
    run = get_object_or_404(SimulationRun, pk=run_id)
    snaps = list(run.snapshots.order_by("round").values("round", "sim_time", "metrics"))
    tz = country_timezone(run.project.country)
    labels = [s["sim_time"].astimezone(tz).strftime("%a %H:%M") if s["sim_time"] else f"R{s['round']}" for s in snaps]
    series = {k: [s["metrics"].get(k) for s in snaps] for k in ("mean", "support", "oppose", "neutral", "arousal", "esteban_ray", "posts", "active", "mobilisation_index")}
    latest = snaps[-1]["metrics"] if snaps else {}
    slim = {k: latest.get(k) for k in ("mean", "support", "oppose", "neutral", "arousal", "esteban_ray", "bimodality", "posts", "active", "mobilisation_index", "salience", "cross_cutting", "events", "hashtags", "emotions")}
    return JsonResponse({"status": run.status, "round": run.current_round, "total": run.total_rounds, "series": {"labels": labels, **series}, "latest": slim})


def run_feed(request, run_id):
    run = get_object_or_404(SimulationRun, pk=run_id)
    platform = request.GET.get("platform", "microblog")
    after = int(_f(request.GET.get("after"), 0, 0))
    stance = request.GET.get("stance", "")
    if platform == "forum":
        threads = list(Post.objects.filter(run=run, platform="forum", kind="thread").select_related("author").order_by("-seq")[:15])
        comments = defaultdict(list)
        for c in Post.objects.filter(run=run, platform="forum", root__in=threads).select_related("author").order_by("seq"):
            comments[c.root_id].append(c)
        for t in threads:
            t.comment_list = comments.get(t.pk, [])[-6:]
        return render(request, "swarm/partials/forum.html", {"threads": threads, "run": run})
    if platform == "chat":
        group_idx = request.GET.get("group")
        groups = {g.idx: g for g in ChatGroup.objects.filter(project=run.project)}
        qs = Post.objects.filter(run=run, platform="chat").select_related("author")
        if group_idx not in (None, ""):
            qs = qs.filter(group_idx=int(_f(group_idx, 0)))
        msgs = list(qs.order_by("-seq")[:40])[::-1]
        for m in msgs:
            m.group_name = groups[m.group_idx].name if m.group_idx in groups else "Group"
        return render(request, "swarm/partials/chat.html", {"messages_list": msgs, "run": run, "group": groups.get(int(_f(group_idx, -1))) if group_idx not in (None, "") else None})
    qs = Post.objects.filter(run=run, platform="microblog", seq__gt=after).select_related("author", "parent__author")
    if stance == "support":
        qs = qs.filter(stance__gt=0.2)
    elif stance == "oppose":
        qs = qs.filter(stance__lt=-0.2)
    elif stance == "llm":
        qs = qs.filter(source="llm")
    posts = list(qs.order_by("-seq")[:30])
    return render(request, "swarm/partials/microblog.html", {"posts": posts, "run": run, "after": after, "latest": posts[0].seq if posts else after})


def post_thread(request, run_id, post_id):
    run = get_object_or_404(SimulationRun, pk=run_id)
    post = get_object_or_404(Post.objects.select_related("author"), pk=post_id, run=run)
    replies = list(Post.objects.filter(run=run, parent=post).select_related("author").order_by("seq")[:50])
    return render(request, "swarm/partials/post_thread.html", {"post": post, "replies": replies, "run": run})


@require_POST
def run_stop(request, run_id):
    run = get_object_or_404(SimulationRun, pk=run_id)
    if run.job and run.job.is_active:
        cancel(run.job)
        messages.info(request, "Stopping after the current round…")
    return redirect("swarm:run_detail", run_id=run.pk)


@require_POST
def run_resume(request, run_id):
    run = get_object_or_404(SimulationRun, pk=run_id)
    if run.is_active:
        messages.info(request, "This run is already running.")
    elif run.current_round >= run.total_rounds:
        extra = int(_f(request.POST.get("extra_rounds"), 0, 0, 200))
        if extra:
            run.total_rounds += extra
            run.config = {**run.config, "rounds": run.total_rounds}
            run.save(update_fields=["total_rounds", "config"])
            start_run(run)
            messages.success(request, f"Extending the run by {extra} rounds.")
        else:
            messages.info(request, "This run is finished. Add rounds to extend it.")
    else:
        start_run(run)
        messages.success(request, "Resuming the simulation.")
    return redirect("swarm:run_detail", run_id=run.pk)


@require_POST
def run_inject(request, run_id):
    """'God mode': add an intervention to the next round of a live (or paused) run."""
    run = get_object_or_404(SimulationRun, pk=run_id)
    kind = request.POST.get("kind")
    if kind not in Intervention.Kind.values:
        messages.error(request, "Choose what to inject.")
        return redirect("swarm:run_detail", run_id=run.pk)
    target_round = int(_f(request.POST.get("round"), run.current_round + 1, 1, max(1, run.total_rounds)))
    target_round = max(target_round, run.current_round + 1)
    content = (request.POST.get("content") or "").strip()
    if not content and kind not in ("web_refresh", "influencer_campaign"):
        messages.error(request, "Write the message or news to inject.")
        return redirect("swarm:run_detail", run_id=run.pk)
    Intervention.objects.create(
        run=run,
        round=target_round,
        kind=kind,
        title=(request.POST.get("title") or content[:80]).strip()[:200],
        content=content[:2000],
        actor=(request.POST.get("actor") or "").strip()[:200],
        stance=_f(request.POST.get("stance"), 0.0, -1, 1),
        strength=_f(request.POST.get("strength"), 0.7, 0, 1),
        duration=int(_f(request.POST.get("duration"), 2, 1, 50)),
        target_segments=[s.strip() for s in (request.POST.get("target_segments") or "").split(",") if s.strip()],
    )
    messages.success(request, f"Scheduled for round {target_round}.")
    if run.current_round >= run.total_rounds and not run.is_active:
        messages.info(request, "This run has finished: use 'Run again with changes' to test the intervention.")
    return redirect("swarm:run_detail", run_id=run.pk)


@require_POST
def run_clone(request, run_id):
    run = get_object_or_404(SimulationRun, pk=run_id)
    clone = clone_run(run, name=(request.POST.get("name") or f"{run.name} (variant)").strip(), include_interventions=request.POST.get("include_interventions") == "on")
    messages.success(request, "Created a copy. Add or change interventions, then start it.")
    return redirect("swarm:run_detail", run_id=clone.pk)


@require_POST
def run_start(request, run_id):
    run = get_object_or_404(SimulationRun, pk=run_id)
    if run.status in (SimulationRun.Status.DRAFT, SimulationRun.Status.FAILED, SimulationRun.Status.STOPPED):
        start_run(run)
        messages.success(request, "Simulation started.")
    return redirect("swarm:run_detail", run_id=run.pk)


@require_POST
def run_delete(request, run_id):
    run = get_object_or_404(SimulationRun, pk=run_id)
    project_id = run.project_id
    if run.job and run.job.is_active:
        cancel(run.job)
    run.delete()
    messages.success(request, "Run deleted.")
    return redirect("swarm:runs", pk=project_id)


@require_POST
def intervention_delete(request, run_id, iv_id):
    iv = get_object_or_404(Intervention, pk=iv_id, run_id=run_id)
    if iv.applied:
        messages.error(request, "That intervention already happened.")
    else:
        iv.delete()
        messages.success(request, "Intervention removed.")
    return redirect("swarm:run_detail", run_id=run_id)


def run_export(request, run_id, what):
    run = get_object_or_404(SimulationRun, pk=run_id)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="baraza-simulated-{what}-{run.pk}.csv"'
    response.write("﻿")  # Excel-friendly UTF-8
    writer = csv.writer(response)
    if what == "posts":
        writer.writerow(["seq", "round", "sim_time", "platform", "kind", "author", "author_kind", "segment", "region", "text", "stance", "sentiment", "emotion", "likes", "dislikes", "reposts", "replies", "views", "influence", "persuaded", "backfired", "source", "is_misinfo", "note"])
        for p in Post.objects.filter(run=run).select_related("author").order_by("seq").iterator():
            writer.writerow([p.seq, p.round, p.sim_time, p.platform, p.kind, p.author.handle, p.author.kind, p.author.archetype_label, p.author.region, p.text, round(p.stance, 4), p.sentiment, p.emotion, p.likes, p.dislikes, p.reposts, p.replies, p.views, round(p.influence, 5), p.persuaded, p.backfired, p.source, p.is_misinfo, "SIMULATED"])
    elif what == "agents":
        writer.writerow(["idx", "handle", "kind", "segment", "age", "gender", "region", "region_group", "urban", "leaning", "openness", "conscientiousness", "extraversion", "agreeableness", "neuroticism", "initial_stance", "final_stance", "change", "final_emotion", "arousal", "posts", "reactions", "followers"])
        for s in AgentState.objects.filter(run=run).select_related("agent").order_by("agent__idx").iterator():
            a = s.agent
            b = a.big5 or {}
            writer.writerow([a.idx, a.handle, a.kind, a.archetype_label, a.age, a.gender, a.region, a.region_group, a.urban, a.leaning, b.get("o"), b.get("c"), b.get("e"), b.get("a"), b.get("n"), a.initial_stance, s.stance, round(s.stance - a.initial_stance, 4), s.emotion, s.arousal, s.posts, s.reactions, s.followers])
    elif what == "rounds":
        keys = ["mean", "median", "std", "support", "oppose", "neutral", "extreme", "net", "esteban_ray", "bimodality", "divergence", "arousal", "sentiment", "salience", "active", "posts", "exposures", "cross_cutting", "exposure_distance", "mobilisation_index", "mobilising_posts", "misinfo_posts"]
        writer.writerow(["round", "sim_time", *keys, "events"])
        for snap in run.snapshots.order_by("round"):
            writer.writerow([snap.round, snap.sim_time, *[snap.metrics.get(k) for k in keys], " | ".join(snap.metrics.get("events", []))])
    else:
        raise Http404
    return response


# ---------------------------------------------------------------------------
# Experiments (Monte Carlo ensembles and sweeps)
# ---------------------------------------------------------------------------


@require_POST
def experiment_create(request, pk):
    project = get_object_or_404(Project, pk=pk)
    if not project.agents.exists():
        messages.error(request, "Create a population first.")
        return redirect("swarm:runs", pk=pk)
    kind = request.POST.get("kind") if request.POST.get("kind") in Experiment.Kind.values else Experiment.Kind.ENSEMBLE
    run_id = request.POST.get("run_id") or ""
    config = {
        "runs": int(_f(request.POST.get("runs"), 30, 3, 500)),
        "rounds": int(_f(request.POST.get("rounds"), 0, 0, 500)) or None,
        "run_id": run_id or None,
        "include_interventions": request.POST.get("include_interventions", "on") == "on",
        "compare_baseline": True,
    }
    name = "Monte Carlo ensemble"
    if kind == Experiment.Kind.SWEEP:
        param = request.POST.get("parameter") if request.POST.get("parameter") in SWEEPABLE else "echo_chamber"
        config["parameter"] = param
        config["runs"] = int(_f(request.POST.get("runs"), 50, 6, 1000))
        name = f"Sweep: {SWEEPABLE[param][0]}"
    exp = Experiment.objects.create(project=project, kind=kind, name=name, config=config)
    enqueue("apps.swarm.ensemble.run_experiment", kind="experiment", label=f"{name}: {project.title}", project=project, payload={"experiment_id": str(exp.pk)})
    messages.success(request, f"{name} started.")
    return redirect("swarm:experiment", exp_id=exp.pk)


def experiment_detail(request, exp_id):
    exp = get_object_or_404(Experiment.objects.select_related("project"), pk=exp_id)
    return render(request, "swarm/experiment.html", {"exp": exp, "project": exp.project, "active_step": "simulate", "results": exp.results})


@require_POST
def experiment_delete(request, exp_id):
    exp = get_object_or_404(Experiment, pk=exp_id)
    pk = exp.project_id
    exp.delete()
    messages.success(request, "Experiment deleted.")
    return redirect("swarm:runs", pk=pk)
