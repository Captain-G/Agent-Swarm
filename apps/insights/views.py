"""Analytics dashboard, Observer report, interviews, polls, validation and comparisons."""

from __future__ import annotations

from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.jobs import active_job, enqueue
from apps.projects.models import Project
from apps.swarm.models import Agent, SimulationRun

from .analytics import compare_runs, full_analytics, movers, network_json
from .conversations import ask_observer, interview_agent
from .models import Interview, Poll, Report, ValidationDataset
from .report import report_html
from .validation import dataset_from_upload, validate


def project_insights(request, pk):
    project = get_object_or_404(Project, pk=pk)
    runs = project.runs.filter(status=SimulationRun.Status.COMPLETED).order_by("-created_at")
    latest = runs.first()
    if latest and not request.GET.get("all"):
        return redirect("insights:run", run_id=latest.pk)
    return render(request, "insights/project.html", {"project": project, "runs": runs, "active_step": "insights"})


def run_insights(request, run_id):
    run = get_object_or_404(SimulationRun.objects.select_related("project"), pk=run_id)
    project = run.project
    if run.current_round == 0:
        messages.info(request, "This run has no results yet.")
        return redirect("swarm:run_detail", run_id=run.pk)
    analytics = full_analytics(run)
    traj = analytics["trajectory"]
    chart_data = {
        "labels": traj["labels"],
        "series": traj["series"],
        "emotions": traj["emotions"],
        "segments": {k: {"labels": [r["name"] for r in v["rows"]], "initial": [r["initial"] for r in v["rows"]], "final": [r["final"] for r in v["rows"]]} for k, v in analytics["segments"].items()},
        "hist_initial": analytics["distributions"]["initial"].get("histogram", []),
        "hist_final": analytics["distributions"]["final"].get("histogram", []),
        "arguments": [{"claim": a["claim"][:70], "side": a["side"], "influence": a["influence"]} for a in analytics["arguments"][:10]],
    }
    return render(
        request,
        "insights/run.html",
        {
            "project": project,
            "run": run,
            "a": analytics,
            "movers": movers(run),
            "chart_data": chart_data,
            "other_runs": project.runs.filter(status=SimulationRun.Status.COMPLETED).exclude(pk=run.pk),
            "report": run.reports.filter(status="completed").first(),
            "validation": run.validations.first(),
            "active_step": "insights",
            "tab": "overview",
        },
    )


def network_data(request, run_id):
    run = get_object_or_404(SimulationRun, pk=run_id)
    return JsonResponse(network_json(run))


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def report(request, run_id):
    run = get_object_or_404(SimulationRun.objects.select_related("project"), pk=run_id)
    rep = run.reports.filter(status="completed").first()
    job = active_job(run.project, ["report"])
    return render(
        request,
        "insights/report.html",
        {"project": run.project, "run": run, "report": rep, "sections": report_html(rep) if rep else [], "job": job, "active_step": "insights", "tab": "report", "print": request.GET.get("print") == "1"},
    )


@require_POST
def report_generate(request, run_id):
    run = get_object_or_404(SimulationRun, pk=run_id)
    if active_job(run.project, ["report"]):
        messages.info(request, "A report is already being written.")
    else:
        enqueue("apps.insights.report.generate_report", kind="report", label=f"Report: {run.name}", project=run.project, payload={"run_id": str(run.pk)})
        messages.success(request, "The Observer is writing the report…")
    return redirect("insights:report", run_id=run.pk)


def report_markdown(request, run_id):
    run = get_object_or_404(SimulationRun, pk=run_id)
    rep = get_object_or_404(Report, run=run, pk=request.GET.get("id")) if request.GET.get("id") else run.reports.filter(status="completed").first()
    if rep is None:
        return redirect("insights:report", run_id=run.pk)
    response = HttpResponse(rep.markdown, content_type="text/markdown; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="baraza-report-{run.pk}.md"'
    return response


# ---------------------------------------------------------------------------
# Talk to the simulation
# ---------------------------------------------------------------------------


def talk(request, run_id):
    run = get_object_or_404(SimulationRun.objects.select_related("project"), pk=run_id)
    agents = Agent.objects.filter(project=run.project).order_by("kind", "archetype_label", "idx")
    agent = None
    interview = None
    wanted = request.GET.get("agent", "")
    if wanted.isdigit():  # a stale or foreign agent id falls back to the default view
        agent = Agent.objects.filter(pk=int(wanted), project=run.project).first()
        if agent is not None:
            interview = Interview.objects.filter(run=run, agent=agent).first()
    observer = Interview.objects.filter(run=run, agent__isnull=True).first()
    return render(
        request,
        "insights/talk.html",
        {
            "project": run.project,
            "run": run,
            "agents": agents,
            "agent": agent,
            "interview": interview,
            "observer": observer,
            "polls": run.polls.all()[:10],
            "active_step": "insights",
            "tab": "talk",
        },
    )


@require_POST
def talk_send(request, run_id):
    run = get_object_or_404(SimulationRun, pk=run_id)
    question = (request.POST.get("message") or "").strip()[:1000]
    target = request.POST.get("target", "observer")
    if not question:
        return HttpResponse(status=204)
    if target == "observer":
        interview = ask_observer(run, question)
    else:
        agent = get_object_or_404(Agent, pk=target, project=run.project)
        interview = interview_agent(run, agent, question)
    return render(request, "insights/partials/conversation.html", {"interview": interview, "agent": interview.agent})


@require_POST
def poll_create(request, run_id):
    run = get_object_or_404(SimulationRun, pk=run_id)
    question = (request.POST.get("question") or "").strip()
    if len(question) < 8:
        messages.error(request, "Write a full question.")
        return redirect("insights:talk", run_id=run.pk)
    options = [o.strip() for o in (request.POST.get("options") or "").split(",") if o.strip()][:6]
    size = max(6, min(200, int(request.POST.get("sample_size") or 24)))
    poll = Poll.objects.create(run=run, question=question[:1000], options=options, sample_size=size)
    enqueue("apps.insights.conversations.run_poll", kind="poll", label=f"Poll: {question[:60]}", project=run.project, payload={"poll_id": poll.pk})
    messages.success(request, "Asking the virtual focus group…")
    return redirect("insights:poll", run_id=run.pk, poll_id=poll.pk)


def poll_detail(request, run_id, poll_id):
    run = get_object_or_404(SimulationRun.objects.select_related("project"), pk=run_id)
    poll = get_object_or_404(Poll, pk=poll_id, run=run)
    return render(request, "insights/poll.html", {"project": run.project, "run": run, "poll": poll, "active_step": "insights", "tab": "talk", "job": active_job(run.project, ["poll"])})


# ---------------------------------------------------------------------------
# Validation and comparison
# ---------------------------------------------------------------------------


def validation(request, run_id):
    run = get_object_or_404(SimulationRun.objects.select_related("project"), pk=run_id)
    if request.method == "POST":
        dataset = None
        if request.FILES.get("file"):
            dataset = dataset_from_upload(run.project, request.POST.get("name", ""), request.FILES["file"])
            if not dataset.texts:
                messages.error(request, "No text column found. The CSV needs a column named text, content, body, message, post or tweet.")
                return redirect("insights:validation", run_id=run.pk)
        elif request.POST.get("dataset"):
            dataset = get_object_or_404(ValidationDataset, pk=request.POST["dataset"], project=run.project)
        result = validate(run, dataset)
        if result.metrics.get("error"):
            messages.error(request, result.metrics["error"])
        else:
            messages.success(request, f"Fit score {result.metrics['fit_score']:.2f} (1 = identical distributions).")
        return redirect("insights:validation", run_id=run.pk)
    return render(
        request,
        "insights/validation.html",
        {"project": run.project, "run": run, "results": run.validations.all()[:10], "datasets": run.project.validation_sets.all(), "active_step": "insights", "tab": "validation"},
    )


def compare(request, pk):
    project = get_object_or_404(Project, pk=pk)
    completed = project.runs.filter(status=SimulationRun.Status.COMPLETED).order_by("-created_at")
    a = completed.filter(pk=request.GET.get("a")).first() or (completed[1] if completed.count() > 1 else None)
    b = completed.filter(pk=request.GET.get("b")).first() or completed.first()
    data = compare_runs(a, b) if a and b and a.pk != b.pk else None
    return render(request, "insights/compare.html", {"project": project, "runs": completed, "a": a, "b": b, "data": data, "active_step": "insights"})
