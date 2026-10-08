"""Knowledge base: dossier, knowledge graph and the argument bank."""

from __future__ import annotations

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.jobs import active_job, enqueue
from apps.core.text.textutils import truncate
from apps.projects.models import Project

from .graph import graph_json
from .models import Argument, Community, Dossier, Entity, Relation
from .retrieval import KnowledgeBase


def index(request, pk):
    project = get_object_or_404(Project, pk=pk)
    try:
        dossier = project.dossier
    except Dossier.DoesNotExist:
        dossier = None
    arguments = project.arguments.all()
    sources = {s.pk: s for s in project.sources.filter(pk__in=[sid for f in (dossier.key_facts if dossier else []) for sid in f.get("sources", [])])}
    return render(
        request,
        "knowledge/index.html",
        {
            "project": project,
            "dossier": dossier,
            "facts": [{"fact": f["fact"], "sources": [sources[s] for s in f.get("sources", []) if s in sources]} for f in (dossier.key_facts if dossier else [])],
            "support_args": [a for a in arguments if a.side == "support"],
            "oppose_args": [a for a in arguments if a.side == "oppose"],
            "neutral_args": [a for a in arguments if a.side == "neutral"],
            "entities": Entity.objects.filter(project=project).order_by("-salience")[:40],
            "communities": Community.objects.filter(project=project)[:8],
            "n_entities": Entity.objects.filter(project=project).count(),
            "n_relations": Relation.objects.filter(project=project).count(),
            "n_chunks": project.chunks.count(),
            "n_arguments": len(arguments),
            "job": active_job(project, ["knowledge", "autopilot"]),
            "active_step": "knowledge",
        },
    )


@require_POST
def build(request, pk):
    project = get_object_or_404(Project, pk=pk)
    if active_job(project, ["knowledge", "autopilot", "research"]):
        messages.info(request, "Please wait for the running task to finish.")
    else:
        enqueue("apps.knowledge.builder.build_knowledge", kind="knowledge", label=f"Knowledge: {project.title}", project=project, payload={"project_id": str(project.pk)})
        messages.success(request, "Building the knowledge base. This reads your evidence and may take a few minutes with a local model.")
    return redirect("knowledge:index", pk=pk)


def graph_data(request, pk):
    project = get_object_or_404(Project, pk=pk)
    return JsonResponse(graph_json(project))


@require_POST
def add_argument(request, pk):
    project = get_object_or_404(Project, pk=pk)
    claim = (request.POST.get("claim") or "").strip()
    side = request.POST.get("side") if request.POST.get("side") in Argument.Side.values else "support"
    if len(claim) < 10:
        messages.error(request, "Write the argument in at least one full sentence.")
    else:
        Argument.objects.create(
            project=project,
            side=side,
            claim=claim[:600],
            short=truncate(request.POST.get("short") or claim, 280),
            strength=max(0.0, min(1.0, float(request.POST.get("strength") or 0.6))),
            frequency=2,
            emotion=request.POST.get("emotion") or "neutral",
            rationale="Added by you (message to test).",
        )
        messages.success(request, "Argument added. Agents on that side will now use it in future simulation runs.")
    return redirect("knowledge:index", pk=pk)


@require_POST
def delete_argument(request, pk, arg_id):
    argument = get_object_or_404(Argument, pk=arg_id, project_id=pk)
    argument.delete()
    messages.success(request, "Argument removed.")
    return redirect("knowledge:index", pk=pk)


def ask(request, pk):
    """Quick GraphRAG search over the evidence (htmx)."""
    project = get_object_or_404(Project, pk=pk)
    query = (request.GET.get("q") or "").strip()
    results = []
    graph = []
    if query:
        kb = KnowledgeBase(project)
        results = kb.search(query, k=6)
        graph = kb.graph_context(kb.entities_in(query))
    return render(request, "knowledge/partials/search_results.html", {"results": results, "graph": graph, "q": query})
