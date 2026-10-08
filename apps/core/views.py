"""Dashboard, settings, background jobs, health and static pages."""

from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from baraza import __version__

from .config import BOOL_KEYS, SECRET_KEYS, get_config, mask, save_overrides
from .geo.countries import country_choices
from .jobs import cancel, dispatch_pending
from .llm import get_llm, reset_providers
from .models import Job, LLMUsage

SETTINGS_GROUPS = [
    (
        "Language model",
        "Baraza runs free on your own computer with Ollama. You can optionally use a hosted model for the most important writing.",
        [
            ("LLM_PROVIDER", "Provider", "select", [("ollama", "Ollama (free, local)"), ("openai", "OpenAI-compatible API (Groq, OpenRouter, ...)"), ("fake", "Demo mode (no AI, placeholder text)"), ("none", "None (heuristics only)")]),
            ("OLLAMA_BASE_URL", "Ollama address", "text", None),
            ("OLLAMA_MODEL", "Main model", "text", None),
            ("OLLAMA_FAST_MODEL", "Fast model for agent posts (optional)", "text", None),
            ("OLLAMA_EMBED_MODEL", "Embedding model (optional)", "text", None),
            ("OLLAMA_NUM_CTX", "Context window (tokens)", "number", None),
            ("OPENAI_BASE_URL", "OpenAI-compatible base URL", "text", None),
            ("OPENAI_API_KEY", "API key", "password", None),
            ("OPENAI_MODEL", "Model name", "text", None),
            ("OPENAI_FAST_MODEL", "Fast model (optional)", "text", None),
            ("HYBRID_SMART_TIER", "Use the hosted model only for briefs, dossiers and reports (agents stay local)", "checkbox", None),
            ("LLM_CONCURRENCY", "Parallel requests", "number", None),
            ("LLM_TIMEOUT", "Timeout per request (seconds)", "number", None),
            ("LLM_CACHE", "Cache identical requests (saves time and money)", "checkbox", None),
        ],
    ),
    (
        "Data sources",
        "All optional. Free sources work without any keys. See the Data sources page for what each one gives you and how to get keys.",
        [
            ("YOUTUBE_API_KEY", "YouTube Data API key (free)", "password", None),
            ("REDDIT_CLIENT_ID", "Reddit client id (free)", "text", None),
            ("REDDIT_CLIENT_SECRET", "Reddit client secret", "password", None),
            ("TELEGRAM_CHANNELS", "Telegram public channels (comma-separated usernames)", "text", None),
            ("CUSTOM_RSS_FEEDS", "Extra RSS feeds (comma-separated URLs)", "text", None),
            ("SEARXNG_URL", "SearXNG address (self-hosted)", "text", None),
            ("BLUESKY_HANDLE", "Bluesky handle (optional)", "text", None),
            ("BLUESKY_APP_PASSWORD", "Bluesky app password", "password", None),
            ("JINA_API_KEY", "Jina Reader key (optional)", "password", None),
            ("TAVILY_API_KEY", "Tavily key (free tier)", "password", None),
            ("BRAVE_API_KEY", "Brave Search key", "password", None),
            ("SERPER_API_KEY", "Serper key", "password", None),
            ("APIFY_TOKEN", "Apify token", "password", None),
            ("TWITTERAPI_IO_KEY", "twitterapi.io key", "password", None),
            ("X_BEARER_TOKEN", "X API bearer token", "password", None),
        ],
    ),
    (
        "Privacy and behaviour",
        "",
        [
            ("DEFAULT_COUNTRY", "Default country", "select", None),
            ("RESPECT_ROBOTS_TXT", "Respect robots.txt when reading web pages", "checkbox", None),
            ("ANONYMIZE_SOCIAL_AUTHORS", "Anonymise authors of collected social posts", "checkbox", None),
            ("REQUIRE_LOGIN", "Require login (for shared servers)", "checkbox", None),
        ],
    ),
]


def home(request):
    from apps.projects.models import Project
    from apps.projects.scenarios import SCENARIOS

    dispatch_pending()
    projects = Project.objects.filter(archived=False).order_by("-updated_at")[:9]
    return render(
        request,
        "core/home.html",
        {
            "projects": projects,
            "scenarios": SCENARIOS,
            "countries": country_choices(),
            "default_country": get_config().get("DEFAULT_COUNTRY", "KE"),
        },
    )


def status_pill(request):
    """Lazy-loaded header pill showing whether the language model is reachable."""
    llm = get_llm()
    info = llm.health("smart")
    return render(request, "core/partials/status_pill.html", {"info": info, "describe": llm.describe()})


def settings_view(request):
    conf = get_config()
    if request.method == "POST":
        values = {}
        for _group, _help, fields in SETTINGS_GROUPS:
            for key, _label, kind, _choices in fields:
                if kind == "checkbox":
                    values[key] = "true" if request.POST.get(key) == "on" else "false"
                elif kind == "password":
                    submitted = request.POST.get(key, "")
                    if submitted == "__clear__":
                        values[key] = ""
                    elif submitted and "•" not in submitted:
                        values[key] = submitted.strip()
                else:
                    if key in request.POST:
                        values[key] = request.POST.get(key, "").strip()
        save_overrides(values)
        reset_providers()
        messages.success(request, "Settings saved.")
        return redirect("core:settings")
    groups = []
    for title, help_text, fields in SETTINGS_GROUPS:
        rows = []
        for key, label, kind, choices in fields:
            value = conf.get(key)
            if key == "DEFAULT_COUNTRY":
                choices = country_choices()
            rows.append(
                {
                    "key": key,
                    "label": label,
                    "kind": kind,
                    "choices": choices,
                    "value": mask(value) if key in SECRET_KEYS else value,
                    "is_set": bool(value),
                    "checked": bool(value) if key in BOOL_KEYS else False,
                }
            )
        groups.append({"title": title, "help": help_text, "fields": rows})
    usage = LLMUsage.objects.order_by("-calls")[:20]
    return render(request, "core/settings.html", {"groups": groups, "usage": usage})


@require_POST
def test_llm(request):
    reset_providers()
    llm = get_llm()
    info = llm.health("smart", force=True)
    sample = None
    if info.get("ok"):
        try:
            sample = llm.complete(
                "You are a helpful assistant. Reply in one short sentence.",
                "Say hello to a Kenyan policy analyst in one sentence.",
                purpose="settings_test",
                tier="smart",
                max_tokens=60,
                temperature=0.3,
                use_cache=False,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            info = {**info, "ok": False, "detail": f"Connected, but generation failed: {exc}"}
    embed_ok = llm.embed(["test sentence"]) is not None
    return render(request, "core/partials/llm_test.html", {"info": info, "sample": sample, "embed_ok": embed_ok, "describe": llm.describe()})


def jobs(request):
    dispatch_pending()
    return render(request, "core/jobs.html", {"jobs": Job.objects.select_related("project")[:100]})


def job_status(request, pk):
    job = get_object_or_404(Job, pk=pk)
    template = "core/partials/job_status.html"
    response = render(request, template, {"job": job, "compact": request.GET.get("compact") == "1"})
    if job.is_done and request.headers.get("HX-Request"):
        # Tell the page a job finished so it can refresh dependent sections.
        response["HX-Trigger"] = "jobFinished"
    return response


@require_POST
def job_cancel(request, pk):
    job = get_object_or_404(Job, pk=pk)
    cancel(job)
    if request.headers.get("HX-Request"):
        job.refresh_from_db()
        return render(request, "core/partials/job_status.html", {"job": job})
    messages.info(request, "Stopping the job…")
    return redirect(request.POST.get("next") or "core:jobs")


def about(request):
    return render(request, "core/about.html")


def health(request):
    llm = get_llm()
    return JsonResponse({"ok": True, "version": __version__, "llm": llm.health("smart"), "debug": settings.DEBUG})


def error_404(request, exception=None):
    return render(request, "core/error.html", {"code": 404, "title": "Page not found", "message": "The page you are looking for does not exist or was deleted."}, status=404)


def error_500(request):
    return render(request, "core/error.html", {"code": 500, "title": "Something went wrong", "message": "An unexpected error occurred. Details were written to data/logs/baraza.log."}, status=500)


def robots(request):
    return HttpResponse("User-agent: *\nDisallow: /\n", content_type="text/plain")
