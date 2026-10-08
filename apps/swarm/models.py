"""Synthetic population and simulation runs."""

from __future__ import annotations

import uuid

from django.db import models
from django.urls import reverse


def stance_label(value: float | None) -> str:
    if value is None:
        return "Unknown"
    if value >= 0.6:
        return "Strongly supports"
    if value >= 0.2:
        return "Leans support"
    if value > -0.2:
        return "Undecided"
    if value > -0.6:
        return "Leans oppose"
    return "Strongly opposes"


class Agent(models.Model):
    class Kind(models.TextChoices):
        CITIZEN = "citizen", "Citizen"
        INFLUENCER = "influencer", "Influencer"
        INSTITUTION = "institution", "Institution"
        MEDIA = "media", "Media outlet"
        BOT = "bot", "Coordinated account"

    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="agents")
    idx = models.IntegerField()
    handle = models.CharField(max_length=40)
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.CITIZEN, db_index=True)
    archetype = models.CharField(max_length=40, db_index=True)
    archetype_label = models.CharField(max_length=80)
    age = models.IntegerField(null=True, blank=True)
    gender = models.CharField(max_length=10, blank=True)
    region = models.CharField(max_length=80, blank=True)
    region_group = models.CharField(max_length=60, blank=True)
    urban = models.BooleanField(default=True)
    occupation = models.CharField(max_length=120, blank=True)
    education = models.CharField(max_length=20, blank=True)
    income = models.CharField(max_length=20, blank=True)
    leaning = models.CharField(max_length=20, blank=True)
    platforms = models.JSONField(default=dict, blank=True)
    big5 = models.JSONField(default=dict, blank=True)
    initial_stance = models.FloatField(default=0.0)
    salience = models.FloatField(default=0.5)
    credibility = models.FloatField(default=0.35)
    activity = models.FloatField(default=0.15)
    susceptibility = models.FloatField(default=0.1)
    accept_latitude = models.FloatField(default=0.4)
    reject_latitude = models.FloatField(default=1.0)
    stubbornness = models.FloatField(default=0.05)
    followers_seed = models.IntegerField(default=50)
    following = models.JSONField(default=list, blank=True)
    groups = models.JSONField(default=list, blank=True)
    concerns = models.JSONField(default=list, blank=True)
    bio = models.TextField(blank=True)
    persona = models.TextField(blank=True)
    voice = models.CharField(max_length=200, blank=True)
    entity = models.ForeignKey("knowledge.Entity", null=True, blank=True, on_delete=models.SET_NULL)
    bot_target = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ["idx"]
        constraints = [
            models.UniqueConstraint(fields=["project", "idx"], name="uniq_agent_idx"),
            models.UniqueConstraint(fields=["project", "handle"], name="uniq_agent_handle"),
        ]

    def __str__(self) -> str:
        return f"@{self.handle}"

    @property
    def stance_label(self) -> str:
        return stance_label(self.initial_stance)

    @property
    def age_band(self) -> str:
        if self.age is None:
            return "n/a"
        for lo, hi, label in ((18, 24, "18-24"), (25, 34, "25-34"), (35, 44, "35-44"), (45, 54, "45-54"), (55, 64, "55-64")):
            if lo <= self.age <= hi:
                return label
        return "65+" if self.age >= 65 else "under 18"

    @property
    def is_synthetic_person(self) -> bool:
        return self.kind in (self.Kind.CITIZEN, self.Kind.INFLUENCER, self.Kind.BOT)


class ChatGroup(models.Model):
    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="chat_groups")
    idx = models.IntegerField()
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=20)
    members = models.JSONField(default=list)

    class Meta:
        ordering = ["idx"]
        constraints = [models.UniqueConstraint(fields=["project", "idx"], name="uniq_group_idx")]

    def __str__(self) -> str:
        return self.name


class SimulationRun(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        STOPPED = "stopped", "Stopped"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="runs")
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT, db_index=True)
    config = models.JSONField(default=dict)
    current_round = models.IntegerField(default=0)
    total_rounds = models.IntegerField(default=24)
    baseline = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="variants")
    summary = models.JSONField(default=dict, blank=True)
    job = models.ForeignKey("core.Job", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("swarm:run_detail", args=[self.pk])

    @property
    def progress(self) -> float:
        if not self.total_rounds:
            return 0.0
        return round(100.0 * self.current_round / self.total_rounds, 1)

    @property
    def is_active(self) -> bool:
        return self.status in (self.Status.QUEUED, self.Status.RUNNING)


class Intervention(models.Model):
    class Kind(models.TextChoices):
        BREAKING_NEWS = "breaking_news", "Breaking news"
        OFFICIAL_STATEMENT = "official_statement", "Official statement / message test"
        POLICY_CHANGE = "policy_change", "Policy change (amendment, concession)"
        INFLUENCER_CAMPAIGN = "influencer_campaign", "Influencer campaign"
        MISINFORMATION = "misinformation", "Rumour / misinformation"
        FACT_CHECK = "fact_check", "Fact-check"
        WEB_REFRESH = "web_refresh", "Live web refresh (latest real news)"

    run = models.ForeignKey(SimulationRun, on_delete=models.CASCADE, related_name="interventions")
    round = models.IntegerField()
    kind = models.CharField(max_length=24, choices=Kind.choices)
    title = models.CharField(max_length=200, blank=True)
    content = models.TextField(blank=True)
    actor = models.CharField(max_length=200, blank=True)
    stance = models.FloatField(default=0.0)
    strength = models.FloatField(default=0.7)
    target_segments = models.JSONField(default=list, blank=True)
    duration = models.IntegerField(default=1)
    applied = models.BooleanField(default=False)
    result = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["round", "created_at"]

    def __str__(self) -> str:
        return f"R{self.round} {self.get_kind_display()}: {self.title}"


class AgentState(models.Model):
    run = models.ForeignKey(SimulationRun, on_delete=models.CASCADE, related_name="states")
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="states")
    stance = models.FloatField()
    arousal = models.FloatField(default=0.2)
    emotion = models.CharField(max_length=20, default="neutral")
    followers = models.IntegerField(default=0)
    following = models.JSONField(default=list, blank=True)
    muted = models.JSONField(default=list, blank=True)
    memory = models.JSONField(default=list, blank=True)
    history = models.JSONField(default=list, blank=True)
    posts = models.IntegerField(default=0)
    reactions = models.IntegerField(default=0)
    last_active_round = models.IntegerField(default=-1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["run", "agent"], name="uniq_state")]

    @property
    def change(self) -> float:
        return round(self.stance - self.agent.initial_stance, 3)


class Post(models.Model):
    class Platform(models.TextChoices):
        MICROBLOG = "microblog", "Microblog (X-style)"
        FORUM = "forum", "Forum (Facebook/Reddit-style)"
        CHAT = "chat", "Group chats (WhatsApp-style)"

    run = models.ForeignKey(SimulationRun, on_delete=models.CASCADE, related_name="posts")
    seq = models.IntegerField()
    round = models.IntegerField(db_index=True)
    platform = models.CharField(max_length=10, choices=Platform.choices)
    kind = models.CharField(max_length=12, default="post")
    author = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="posts")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="replies_set")
    root = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="thread_set")
    group_idx = models.IntegerField(null=True, blank=True)
    title = models.CharField(max_length=200, blank=True)
    text = models.TextField()
    hashtags = models.JSONField(default=list, blank=True)
    stance = models.FloatField(default=0.0)
    strength = models.FloatField(default=0.5)
    arousal = models.FloatField(default=0.3)
    emotion = models.CharField(max_length=20, default="neutral")
    sentiment = models.FloatField(default=0.0)
    argument = models.ForeignKey("knowledge.Argument", null=True, blank=True, on_delete=models.SET_NULL)
    source = models.CharField(max_length=12, default="template")
    is_misinfo = models.BooleanField(default=False)
    intervention = models.ForeignKey(Intervention, null=True, blank=True, on_delete=models.SET_NULL)
    likes = models.IntegerField(default=0)
    dislikes = models.IntegerField(default=0)
    reposts = models.IntegerField(default=0)
    replies = models.IntegerField(default=0)
    views = models.IntegerField(default=0)
    influence = models.FloatField(default=0.0)
    persuaded = models.IntegerField(default=0)
    backfired = models.IntegerField(default=0)
    sim_time = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-seq"]
        constraints = [models.UniqueConstraint(fields=["run", "seq"], name="uniq_post_seq")]
        indexes = [models.Index(fields=["run", "platform", "seq"])]

    def __str__(self) -> str:
        return f"{self.author} r{self.round}: {self.text[:60]}"

    @property
    def stance_label(self) -> str:
        return stance_label(self.stance)


class Action(models.Model):
    run = models.ForeignKey(SimulationRun, on_delete=models.CASCADE, related_name="actions")
    round = models.IntegerField()
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="actions")
    kind = models.CharField(max_length=12, db_index=True)
    post = models.ForeignKey(Post, null=True, blank=True, on_delete=models.CASCADE, related_name="actions")
    target = models.ForeignKey(Agent, null=True, blank=True, on_delete=models.CASCADE, related_name="targeted_actions")

    class Meta:
        indexes = [models.Index(fields=["run", "kind"])]


class RoundSnapshot(models.Model):
    run = models.ForeignKey(SimulationRun, on_delete=models.CASCADE, related_name="snapshots")
    round = models.IntegerField()
    sim_time = models.DateTimeField(null=True, blank=True)
    metrics = models.JSONField(default=dict)

    class Meta:
        ordering = ["round"]
        constraints = [models.UniqueConstraint(fields=["run", "round"], name="uniq_snapshot")]


class Experiment(models.Model):
    """Monte Carlo ensembles and parameter sweeps (dynamics only, no text)."""

    class Kind(models.TextChoices):
        ENSEMBLE = "ensemble", "Monte Carlo ensemble"
        SWEEP = "sweep", "Parameter sweep"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="experiments")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    name = models.CharField(max_length=120)
    config = models.JSONField(default=dict)
    results = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=12, default="queued")
    job = models.ForeignKey("core.Job", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.name
