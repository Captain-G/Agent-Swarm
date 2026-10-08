"""Project creation, overview, brief editing and Autopilot."""

from __future__ import annotations

import json

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.geo import get_country
from apps.core.geo.countries import COUNTRIES, country_choices
from apps.core.jobs import active_job, enqueue
from apps.core.llm import as_float, as_str

from .brief import PROPOSERS, screen_request
from .models import Project
from .scenarios import SCENARIOS_BY_KEY


def project_list(request):
    projects = Project.objects.filter(archived=False).order_by("-updated_at")
    return render(request, "projects/list.html", {"projects": projects})


@require_POST
def create(request):
    prompt = (request.POST.get("prompt") or "").strip()
    template_key = request.POST.get("template", "")
    if not prompt and template_key in SCENARIOS_BY_KEY:
        prompt = SCENARIOS_BY_KEY[template_key]["prompt"]
    if len(prompt) < 15:
        messages.error(request, "Please describe the policy, action or reform in at least one sentence.")
        return redirect("core:home")
    screen = screen_request(prompt)
    if not screen["allowed"]:
        messages.error(request, f"Baraza cannot simulate this request ({', '.join(screen['concerns'])}). It is built for rehearsing public sentiment, not for causing harm.")
        return redirect("core:home")
    country = request.POST.get("country") or "KE"
    if country not in COUNTRIES:
        country = "KE"
    depth = request.POST.get("depth") if request.POST.get("depth") in Project.Depth.values else Project.Depth.STANDARD
    style = request.POST.get("language_style") if request.POST.get("language_style") in Project.LanguageStyle.values else Project.LanguageStyle.LOCAL
    regions = [r.strip() for r in (request.POST.get("focus_regions") or "").split(",") if r.strip()]
    project = Project.objects.create(
        title=(SCENARIOS_BY_KEY.get(template_key, {}).get("title") or prompt[:80]),
        prompt=prompt,
        country=country,
        depth=depth,
        language_style=style,
        focus_regions=regions,
        template_key=template_key if template_key in SCENARIOS_BY_KEY else "",
        owner=request.user if request.user.is_authenticated else None,
    )
    if screen["concerns"]:
        messages.warning(request, f"Note: {', '.join(screen['concerns'])}. Results are for rehearsal and preparedness only.")
    if request.POST.get("mode") == "autopilot":
        enqueue("apps.projects.tasks.autopilot", kind="autopilot", label=f"Autopilot: {project.title}", project=project, payload={"project_id": str(project.pk)})
        messages.success(request, "Autopilot started. You can watch each step below or explore the results as they arrive.")
        return redirect("projects:overview", pk=project.pk)
    enqueue("apps.projects.tasks.generate_brief", kind="brief", label=f"Brief: {project.title}", project=project, payload={"project_id": str(project.pk)})
    return redirect("projects:brief", pk=project.pk)


def overview(request, pk):
    project = get_object_or_404(Project, pk=pk)
    job = active_job(project)
    last_jobs = project.jobs.order_by("-created_at")[:6]
    runs = project.runs.order_by("-created_at")[:5]
    try:
        dossier = project.dossier
    except Exception:
        dossier = None
    usage = project.llm_usage.all()
    totals = {
        "calls": sum(u.calls for u in usage),
        "tokens": sum(u.prompt_tokens + u.completion_tokens for u in usage),
        "cost": sum(u.cost_usd for u in usage),
        "seconds": sum(u.seconds for u in usage),
    }
    latest_report = None
    for run in runs:
        report = run.reports.filter(status="completed").first()
        if report:
            latest_report = report
            break
    return render(
        request,
        "projects/overview.html",
        {
            "project": project,
            "job": job,
            "last_jobs": last_jobs,
            "runs": runs,
            "dossier": dossier,
            "totals": totals,
            "latest_report": latest_report,
            "active_step": "overview",
            "country": get_country(project.country),
        },
    )


def brief(request, pk):
    project = get_object_or_404(Project, pk=pk)
    job = active_job(project, ["brief", "autopilot"])
    if request.method == "POST":
        data = dict(project.brief or {})
        data["title"] = as_str(request.POST.get("title"), data.get("title", project.title), max_len=90)
        data["refined_question"] = as_str(request.POST.get("refined_question"), max_len=400)
        data["summary"] = as_str(request.POST.get("summary"), max_len=1200)
        data["proposer"] = request.POST.get("proposer") if request.POST.get("proposer") in PROPOSERS else data.get("proposer", "government")
        data["proposer_name"] = as_str(request.POST.get("proposer_name"), max_len=120)
        data["time_horizon"] = as_str(request.POST.get("time_horizon"), max_len=120)
        for key in ("policy_details", "key_issues", "search_queries", "local_queries", "social_keywords", "hypotheses", "assumptions", "risks_to_watch"):
            data[key] = [line.strip() for line in (request.POST.get(key) or "").splitlines() if line.strip()][:15]
        hashtags = data.get("hashtags") or {}
        for side in ("support", "oppose"):
            tags = [t.strip() for t in (request.POST.get(f"hashtags_{side}") or "").replace(",", " ").split() if t.strip()]
            hashtags[side] = [t if t.startswith("#") else f"#{t}" for t in tags][:6]
        data["hashtags"] = hashtags
        for key in ("stakeholders", "affected_groups"):
            try:
                rows = json.loads(request.POST.get(key) or "[]")
            except json.JSONDecodeError:
                rows = data.get(key, [])
            cleaned = []
            for row in rows if isinstance(rows, list) else []:
                if key == "stakeholders" and as_str(row.get("name")):
                    cleaned.append({"name": as_str(row.get("name"), max_len=90), "type": as_str(row.get("type"), "organization", max_len=40), "likely_position": round(as_float(row.get("likely_position"), 0, -1, 1), 2), "influence": round(as_float(row.get("influence"), 0.5, 0, 1), 2)})
                if key == "affected_groups" and as_str(row.get("group")):
                    cleaned.append({"group": as_str(row.get("group"), max_len=80), "impact": as_str(row.get("impact"), max_len=240), "direction": round(as_float(row.get("direction"), 0, -1, 1), 2), "intensity": round(as_float(row.get("intensity"), 0.5, 0, 1), 2)})
            data[key] = cleaned
        data["edited"] = True
        project.brief = data
        project.title = data["title"][:200]
        project.brief_approved = request.POST.get("approve") == "1" or project.brief_approved
        project.save()
        messages.success(request, "Brief saved.")
        if request.POST.get("approve") == "1":
            return redirect("research:index", pk=project.pk)
        return redirect("projects:brief", pk=project.pk)
    return render(
        request,
        "projects/brief.html",
        {"project": project, "job": job, "proposers": PROPOSERS, "active_step": "brief", "brief_json": project.brief or {}},
    )


@require_POST
def regenerate_brief(request, pk):
    project = get_object_or_404(Project, pk=pk)
    if active_job(project, ["brief", "autopilot"]):
        messages.info(request, "A brief is already being generated.")
    else:
        project.brief = {}
        project.save(update_fields=["brief", "updated_at"])
        enqueue("apps.projects.tasks.generate_brief", kind="brief", label=f"Brief: {project.title}", project=project, payload={"project_id": str(project.pk)})
    return redirect("projects:brief", pk=project.pk)


@require_POST
def autopilot(request, pk):
    project = get_object_or_404(Project, pk=pk)
    if active_job(project):
        messages.info(request, "Another task is still running for this project. Wait for it to finish or stop it first.")
    else:
        enqueue("apps.projects.tasks.autopilot", kind="autopilot", label=f"Autopilot: {project.title}", project=project, payload={"project_id": str(project.pk)})
        messages.success(request, "Autopilot started.")
    return redirect("projects:overview", pk=project.pk)


@require_POST
def update_settings(request, pk):
    project = get_object_or_404(Project, pk=pk)
    title = (request.POST.get("title") or "").strip()
    if title:
        project.title = title[:200]
    if request.POST.get("country") in COUNTRIES:
        project.country = request.POST["country"]
    if request.POST.get("depth") in Project.Depth.values:
        project.depth = request.POST["depth"]
    if request.POST.get("language_style") in Project.LanguageStyle.values:
        project.language_style = request.POST["language_style"]
    if "focus_regions" in request.POST:
        project.focus_regions = [r.strip() for r in request.POST["focus_regions"].split(",") if r.strip()]
    project.save()
    messages.success(request, "Project settings updated.")
    return redirect("projects:overview", pk=project.pk)


@require_POST
def delete(request, pk):
    project = get_object_or_404(Project, pk=pk)
    title = project.title
    project.delete()
    messages.success(request, f"Deleted '{title}'.")
    return redirect("core:home")


def export_json(request, pk):
    """Everything about a project as one JSON file (for archiving or analysis)."""
    project = get_object_or_404(Project, pk=pk)
    data = {
        "project": {"id": str(project.pk), "title": project.title, "prompt": project.prompt, "country": project.country, "brief": project.brief},
        "sources": list(project.sources.values("platform", "kind", "url", "title", "published_at", "snippet", "sentiment", "stance", "relevance", "included")),
        "arguments": list(project.arguments.values("side", "claim", "short", "strength", "frequency", "emotion")),
        "agents": list(project.agents.values("idx", "handle", "kind", "archetype_label", "age", "gender", "region", "urban", "leaning", "initial_stance", "big5")),
        "runs": [
            {"id": str(r.pk), "name": r.name, "config": r.config, "summary": r.summary, "snapshots": list(r.snapshots.values("round", "metrics"))}
            for r in project.runs.all()
        ],
    }
    try:
        d = project.dossier
        data["dossier"] = {"summary": d.summary, "key_facts": d.key_facts, "stakeholders": d.stakeholders, "impact_matrix": d.impact_matrix, "timeline": d.timeline}
    except Exception:
        pass
    response = HttpResponse(json.dumps(data, default=str, indent=2), content_type="application/json")
    response["Content-Disposition"] = f'attachment; filename="baraza-{project.pk}.json"'
    return response


def country_regions(request):
    """Region suggestions for a country (used by the new-project form)."""
    country = get_country(request.GET.get("country"))
    return render(request, "projects/partials/region_options.html", {"country": country, "countries": country_choices()})
