"""Reports, interviews, virtual polls and validation against real data."""

from __future__ import annotations

from django.db import models


class Report(models.Model):
    run = models.ForeignKey("swarm.SimulationRun", on_delete=models.CASCADE, related_name="reports")
    status = models.CharField(max_length=12, default="queued")
    title = models.CharField(max_length=200, blank=True)
    sections = models.JSONField(default=list, blank=True)
    markdown = models.TextField(blank=True)
    data = models.JSONField(default=dict, blank=True)
    generated_by = models.CharField(max_length=20, default="template")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.title or f"Report {self.pk}"


class Interview(models.Model):
    """A conversation with one simulated agent, or with the Observer (agent=None)."""

    run = models.ForeignKey("swarm.SimulationRun", on_delete=models.CASCADE, related_name="interviews")
    agent = models.ForeignKey("swarm.Agent", null=True, blank=True, on_delete=models.CASCADE, related_name="interviews")
    messages = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]


class Poll(models.Model):
    run = models.ForeignKey("swarm.SimulationRun", on_delete=models.CASCADE, related_name="polls")
    question = models.TextField()
    options = models.JSONField(default=list, blank=True)
    sample_size = models.IntegerField(default=24)
    responses = models.JSONField(default=list, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=12, default="queued")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class ValidationDataset(models.Model):
    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="validation_sets")
    name = models.CharField(max_length=200)
    file = models.FileField(upload_to="validation/%Y/%m", blank=True)
    texts = models.JSONField(default=list, blank=True)
    stances = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name


class ValidationResult(models.Model):
    run = models.ForeignKey("swarm.SimulationRun", on_delete=models.CASCADE, related_name="validations")
    dataset = models.ForeignKey(ValidationDataset, null=True, blank=True, on_delete=models.CASCADE)
    source = models.CharField(max_length=40, default="research")
    metrics = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
