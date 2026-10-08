"""Helpers shared by views, Autopilot and management commands."""

from __future__ import annotations

from django.utils import timezone

from apps.core.jobs import enqueue

from .engine import PRESETS, TZ_OFFSETS, SimConfig
from .engine.config import default_start
from .models import Intervention, SimulationRun


def create_run(project, *, name: str, preset: str = "standard", overrides: dict | None = None, description: str = "", interventions: list[dict] | None = None, baseline=None) -> SimulationRun:
    config = dict(PRESETS.get(preset, PRESETS["standard"])["config"])
    config.update({k: v for k, v in (overrides or {}).items() if v not in (None, "")})
    config.setdefault("language_style", project.language_style)
    config.setdefault("seed", 7)
    config.setdefault("tz_offset_hours", TZ_OFFSETS.get(project.country, 3))
    cfg = SimConfig.from_dict(config)
    if not cfg.start_time:
        cfg.start_time = default_start(cfg.tz_offset_hours).isoformat()
    run = SimulationRun.objects.create(
        project=project,
        name=name[:120] or f"Run {timezone.localtime().strftime('%d %b %H:%M')}",
        description=description,
        config=cfg.to_dict(),
        total_rounds=cfg.rounds,
        status=SimulationRun.Status.DRAFT,
        baseline=baseline,
    )
    for iv in interventions or []:
        Intervention.objects.create(run=run, **iv)
    return run


def start_run(run: SimulationRun):
    job = enqueue("apps.swarm.runner.run_simulation", kind="simulation", label=f"Simulation: {run.name}", project=run.project, payload={"run_id": str(run.pk)})
    SimulationRun.objects.filter(pk=run.pk).update(status=SimulationRun.Status.QUEUED, job=job)
    run.refresh_from_db()
    return job


def clone_run(run: SimulationRun, *, name: str, include_interventions: bool = False) -> SimulationRun:
    clone = SimulationRun.objects.create(
        project=run.project,
        name=name[:120],
        description=run.description,
        config=dict(run.config),
        total_rounds=run.total_rounds,
        baseline=run,
    )
    if include_interventions:
        for iv in run.interventions.all():
            Intervention.objects.create(
                run=clone, round=iv.round, kind=iv.kind, title=iv.title, content=iv.content, actor=iv.actor,
                stance=iv.stance, strength=iv.strength, target_segments=iv.target_segments, duration=iv.duration,
            )
    return clone
