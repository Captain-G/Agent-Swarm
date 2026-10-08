"""Core models: runtime configuration, background jobs and LLM bookkeeping."""

from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone


class SiteConfig(models.Model):
    """Singleton holding settings edited in the UI (overrides environment defaults)."""

    values = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "site configuration"

    def __str__(self) -> str:
        return "Site configuration"

    @classmethod
    def load(cls) -> "SiteConfig":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Job(models.Model):
    """A long-running background task (research, simulation, report...)."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"
        INTERRUPTED = "interrupted", "Interrupted"

    ACTIVE_STATUSES = (Status.QUEUED, Status.RUNNING)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=64, db_index=True)
    label = models.CharField(max_length=200)
    func = models.CharField(max_length=200)
    payload = models.JSONField(default=dict, blank=True)
    project = models.ForeignKey(
        "projects.Project", null=True, blank=True, on_delete=models.CASCADE, related_name="jobs"
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED, db_index=True)
    progress = models.FloatField(default=0.0)
    stage = models.CharField(max_length=160, blank=True)
    message = models.TextField(blank=True)
    log = models.JSONField(default=list, blank=True)
    result = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)
    cancel_requested = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.label} [{self.status}]"

    @property
    def is_active(self) -> bool:
        return self.status in self.ACTIVE_STATUSES

    @property
    def is_done(self) -> bool:
        return not self.is_active

    @property
    def duration_seconds(self) -> float | None:
        if not self.started_at:
            return None
        end = self.finished_at or timezone.now()
        return (end - self.started_at).total_seconds()


class LLMCacheEntry(models.Model):
    """Cache of LLM responses keyed by a hash of the full request (saves money and time)."""

    key = models.CharField(max_length=64, unique=True)
    model = models.CharField(max_length=200)
    response = models.TextField()
    prompt_tokens = models.IntegerField(default=0)
    completion_tokens = models.IntegerField(default=0)
    hits = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.model} {self.key[:10]}"


class LLMUsage(models.Model):
    """Aggregated LLM usage per project / purpose / model (for cost transparency)."""

    project = models.ForeignKey(
        "projects.Project", null=True, blank=True, on_delete=models.CASCADE, related_name="llm_usage"
    )
    purpose = models.CharField(max_length=64)
    provider = models.CharField(max_length=32)
    model = models.CharField(max_length=200)
    calls = models.IntegerField(default=0)
    cached_calls = models.IntegerField(default=0)
    errors = models.IntegerField(default=0)
    prompt_tokens = models.BigIntegerField(default=0)
    completion_tokens = models.BigIntegerField(default=0)
    seconds = models.FloatField(default=0.0)
    cost_usd = models.FloatField(default=0.0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["project", "purpose", "provider", "model"], name="uniq_llm_usage_bucket")
        ]

    def __str__(self) -> str:
        return f"{self.purpose} via {self.model}: {self.calls} calls"
