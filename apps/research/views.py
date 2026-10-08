"""Deep research: choose sources, run, review and curate evidence."""

from __future__ import annotations

from collections import Counter

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Avg, Count
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.config import get_config
from apps.core.jobs import active_job, enqueue
from apps.projects.models import Project

from .connectors import CONNECTORS_BY_KEY, CostTier, available_connectors, default_selection
from .models import Source
from .pipeline import add_text, add_upload, add_url, observed_stance_summary

ALLOWED_UPLOADS = {".pdf", ".txt", ".md", ".docx", ".html", ".htm", ".csv"}


def index(request, pk):
    project = get_object_or_404(Project, pk=pk)
    config = get_config()
    qs = project.sources.all()
    platform = request.GET.get("platform", "")
    show = request.GET.get("show", "included")
    if platform:
        qs = qs.filter(platform=platform)
    if show == "included":
        qs = qs.filter(included=True)
    elif show == "excluded":
        qs = qs.filter(included=False)
    query = (request.GET.get("q") or "").strip()
    if query:
        qs = qs.filter(title__icontains=query) | qs.filter(snippet__icontains=query)
    page = Paginator(qs.order_by("-relevance", "-created_at"), 25).get_page(request.GET.get("page"))
    platform_counts = dict(project.sources.values_list("platform").annotate(n=Count("id")).values_list("platform", "n"))
    sentiment_by_platform = {
        row["platform"]: row["avg"] for row in project.sources.filter(included=True).values("platform").annotate(avg=Avg("sentiment"))
    }
    observed = observed_stance_summary(project)
    observed.pop("values", None)
    connectors = available_connectors(config)
    selected = set(default_selection(config))
    tiers = []
    for tier in CostTier.ORDER:
        items = [c for c in connectors if c["cost"] == tier]
        if items:
            tiers.append({"key": tier, "label": CostTier.LABELS[tier], "items": items})
    return render(
        request,
        "research/index.html",
        {
            "project": project,
            "page": page,
            "platform": platform,
            "show": show,
            "q": query,
            "platform_counts": platform_counts,
            "platform_labels": dict(Source.Platform.choices),
            "sentiment_by_platform": sentiment_by_platform,
            "observed": observed,
            "tiers": tiers,
            "selected": selected,
            "job": active_job(project, ["research", "autopilot"]),
            "total": project.sources.count(),
            "included": project.sources.filter(included=True).count(),
            "active_step": "research",
        },
    )


@require_POST
def run(request, pk):
    project = get_object_or_404(Project, pk=pk)
    if active_job(project, ["research", "autopilot", "knowledge"]):
        messages.info(request, "Research is already running for this project.")
        return redirect("research:index", pk=pk)
    connectors = [k for k in request.POST.getlist("connectors") if k in CONNECTORS_BY_KEY]
    if not connectors:
        messages.error(request, "Choose at least one source.")
        return redirect("research:index", pk=pk)
    depth = request.POST.get("depth") if request.POST.get("depth") in Project.Depth.values else project.depth
    timelimit = request.POST.get("timelimit") if request.POST.get("timelimit") in ("d", "w", "m", "y", "") else "m"
    extra = [q.strip() for q in (request.POST.get("extra_queries") or "").splitlines() if q.strip()][:6]
    enqueue(
        "apps.research.pipeline.run_research",
        kind="research",
        label=f"Research: {project.title}",
        project=project,
        payload={"project_id": str(project.pk), "connectors": connectors, "depth": depth, "timelimit": timelimit or None, "extra_queries": extra},
    )
    messages.success(request, "Research started. New sources will appear as they are found.")
    return redirect("research:index", pk=pk)


@require_POST
def toggle(request, pk, source_id):
    source = get_object_or_404(Source, pk=source_id, project_id=pk)
    source.included = not source.included
    source.save(update_fields=["included"])
    return render(request, "research/partials/source_row.html", {"s": source, "project": source.project})


def detail(request, pk, source_id):
    source = get_object_or_404(Source, pk=source_id, project_id=pk)
    return render(request, "research/partials/source_detail.html", {"s": source})


@require_POST
def manual(request, pk):
    project = get_object_or_404(Project, pk=pk)
    kind = request.POST.get("kind")
    if kind == "url":
        url = (request.POST.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            messages.error(request, "Please enter a full web address starting with http:// or https://")
        elif add_url(project, url):
            messages.success(request, "Article added.")
        else:
            messages.error(request, "Could not read that page (it may block automated readers). Paste the text instead.")
    elif kind == "text":
        text = (request.POST.get("text") or "").strip()
        if len(text) < 40:
            messages.error(request, "Please paste at least a few sentences.")
        else:
            add_text(project, request.POST.get("title") or "Pasted text", text)
            messages.success(request, "Text added.")
    elif kind == "file":
        files = request.FILES.getlist("files")
        added = 0
        for f in files[:10]:
            suffix = "." + f.name.rsplit(".", 1)[-1].lower() if "." in f.name else ""
            if suffix not in ALLOWED_UPLOADS:
                messages.error(request, f"{f.name}: unsupported file type.")
                continue
            if f.size > 20 * 1024 * 1024:
                messages.error(request, f"{f.name}: larger than 20 MB.")
                continue
            created = add_upload(project, f)
            added += len(created)
            if not created:
                messages.error(request, f"{f.name}: no readable text found.")
        if added:
            messages.success(request, f"Added {added} item(s) from your files.")
    return redirect("research:index", pk=pk)


@require_POST
def bulk(request, pk):
    project = get_object_or_404(Project, pk=pk)
    action = request.POST.get("action")
    if action == "exclude_low":
        n = project.sources.filter(relevance__lt=0.1).exclude(platform__in=Source.SOCIAL_PLATFORMS).update(included=False)
        messages.success(request, f"Excluded {n} low-relevance sources.")
    elif action == "include_all":
        n = project.sources.update(included=True)
        messages.success(request, f"Included all {n} sources.")
    elif action == "delete_excluded":
        n, _ = project.sources.filter(included=False).delete()
        messages.success(request, f"Deleted {n} excluded sources.")
    return redirect("research:index", pk=pk)


def catalog(request):
    config = get_config()
    connectors = available_connectors(config)
    counts = Counter(c["cost"] for c in connectors)
    tiers = [{"key": t, "label": CostTier.LABELS[t], "items": [c for c in connectors if c["cost"] == t]} for t in CostTier.ORDER if counts.get(t)]
    return render(request, "research/catalog.html", {"tiers": tiers})
