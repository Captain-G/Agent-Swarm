"""A Project is one policy/action/reform being rehearsed, from prompt to report."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.urls import reverse

from apps.core.geo import get_country


class Project(models.Model):
    class Depth(models.TextChoices):
        QUICK = "quick", "Quick: about 80 people, light research"
        STANDARD = "standard", "Standard: about 160 people"
        DEEP = "deep", "Deep: about 300 people, thorough research"

    class LanguageStyle(models.TextChoices):
        ENGLISH = "english", "Plain English"
        LOCAL = "local", "English with local expressions"
        MIXED = "mixed", "Mixed English and local language (realistic)"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=200)
    prompt = models.TextField(help_text="What the user originally asked for.")
    country = models.CharField(max_length=2, default="KE")
    focus_regions = models.JSONField(default=list, blank=True)
    depth = models.CharField(max_length=12, choices=Depth.choices, default=Depth.STANDARD)
    language_style = models.CharField(max_length=12, choices=LanguageStyle.choices, default=LanguageStyle.LOCAL)
    brief = models.JSONField(default=dict, blank=True)
    brief_approved = models.BooleanField(default=False)
    template_key = models.CharField(max_length=64, blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return self.title

    def get_absolute_url(self) -> str:
        return reverse("projects:overview", args=[self.pk])

    @property
    def country_profile(self):
        return get_country(self.country)

    @property
    def depth_label(self) -> str:
        return {"quick": "Quick", "standard": "Standard", "deep": "Deep"}.get(self.depth, self.depth)

    @property
    def topic(self) -> str:
        return (self.brief or {}).get("title") or self.title

    @property
    def question(self) -> str:
        return (self.brief or {}).get("refined_question") or self.prompt

    # ------------------------------------------------------------------
    # Pipeline status
    # ------------------------------------------------------------------
    def step_status(self) -> list[dict]:
        """Status of each pipeline step, used by the stepper navigation."""
        has_brief = bool(self.brief)
        n_sources = self.sources.filter(included=True).count() if hasattr(self, "sources") else 0
        has_dossier = hasattr(self, "dossier") and self._has_dossier()
        n_agents = self.agents.count() if hasattr(self, "agents") else 0
        runs = self.runs.all() if hasattr(self, "runs") else []
        finished_runs = [r for r in runs if r.status == "completed"]
        has_report = any(getattr(r, "reports", None) and r.reports.exists() for r in finished_runs)
        steps = [
            ("brief", "Brief", "projects:brief", has_brief, "Restructured question"),
            ("research", "Research", "research:index", n_sources > 0, f"{n_sources} sources" if n_sources else "Gather evidence"),
            ("knowledge", "Knowledge", "knowledge:index", has_dossier, "Dossier & graph"),
            ("population", "Population", "swarm:population", n_agents > 0, f"{n_agents} agents" if n_agents else "Synthetic citizens"),
            ("simulate", "Simulate", "swarm:runs", bool(finished_runs), f"{len(finished_runs)} runs" if finished_runs else "Run the swarm"),
            ("insights", "Insights", "insights:project_insights", has_report, "Analytics & report"),
        ]
        out = []
        previous_done = True
        for key, label, url_name, done, hint in steps:
            out.append(
                {
                    "key": key,
                    "label": label,
                    "url": reverse(url_name, args=[self.pk]),
                    "done": done,
                    "available": previous_done or done,
                    "hint": hint,
                }
            )
            previous_done = done
        return out

    def _has_dossier(self) -> bool:
        try:
            return bool(self.dossier and self.dossier.summary)
        except Exception:
            return False

    def next_step(self) -> dict | None:
        for step in self.step_status():
            if not step["done"]:
                return step
        return None
