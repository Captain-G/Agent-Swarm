"""Background jobs for projects: the brief, and Autopilot (the whole pipeline)."""

from __future__ import annotations

from .brief import build_brief, screen_request
from .models import Project

POPULATION_SIZE = {"quick": 80, "standard": 160, "deep": 300}
SIM_PRESET = {"quick": "quick", "standard": "standard", "deep": "deep"}


def generate_brief(ctx, project_id):
    project = Project.objects.get(pk=project_id)
    screen = screen_request(project.prompt)
    if not screen["allowed"]:
        raise RuntimeError(f"This request cannot be simulated: {', '.join(screen['concerns'])}.")
    if screen["concerns"]:
        ctx.warn(f"Please note: {', '.join(screen['concerns'])}.")
    brief = build_brief(project, ctx=ctx)
    brief["safety"] = screen
    project.brief = brief
    project.title = brief["title"][:200]
    project.save(update_fields=["brief", "title", "updated_at"])
    ctx.log(f"Brief ready: {brief['refined_question']}")
    return {"title": brief["title"], "generated_by": brief["generated_by"]}


def autopilot(ctx, project_id, options: dict | None = None):
    """Prompt -> brief -> research -> knowledge -> population -> simulation -> report."""
    from apps.insights.report import generate_report
    from apps.knowledge.builder import build_knowledge
    from apps.research.pipeline import run_research
    from apps.swarm.population import PopulationConfig, generate_population
    from apps.swarm.runner import run_simulation
    from apps.swarm.services import create_run

    options = options or {}
    project = Project.objects.get(pk=project_id)
    depth = project.depth
    result = {}

    if not project.brief:
        ctx.progress(1, "Step 1 of 6: understanding your question")
        result["brief"] = generate_brief(ctx.sub(0, 5), project_id)
        project.refresh_from_db()
    if options.get("research", True):
        ctx.progress(5, "Step 2 of 6: researching the web and social media")
        try:
            result["research"] = run_research(ctx.sub(5, 35), project_id, connectors=options.get("connectors"))
        except RuntimeError as exc:
            ctx.warn(f"Research skipped: {exc}")
    ctx.progress(35, "Step 3 of 6: building the knowledge graph and dossier")
    result["knowledge"] = build_knowledge(ctx.sub(35, 55), project_id)
    ctx.progress(55, "Step 4 of 6: creating the synthetic population")
    project.refresh_from_db()
    population = generate_population(project, PopulationConfig(size=int(options.get("population", POPULATION_SIZE.get(depth, 160))), focus_regions=project.focus_regions), ctx.sub(55, 62))
    result["population"] = {"agents": population["agents"], "chat_groups": population["chat_groups"]}
    ctx.log(f"Population: {population['agents']} agents in {population['chat_groups']} chat groups.")
    ctx.progress(62, "Step 5 of 6: running the simulation")
    run = create_run(project, name="Autopilot baseline", preset=options.get("preset", SIM_PRESET.get(depth, "standard")), overrides=options.get("sim", {}))
    result["simulation"] = run_simulation(ctx.sub(62, 94), run.pk)
    ctx.progress(94, "Step 6 of 6: writing the Observer report")
    result["report"] = generate_report(ctx.sub(94, 100), run.pk)
    result["run_id"] = str(run.pk)
    return result
