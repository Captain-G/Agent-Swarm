"""GraphRAG knowledge base: chunks, entities, relations, arguments, communities and the dossier."""

from __future__ import annotations

from django.db import models


class Chunk(models.Model):
    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="chunks")
    source = models.ForeignKey("research.Source", null=True, blank=True, on_delete=models.CASCADE, related_name="chunks")
    idx = models.IntegerField(default=0)
    kind = models.CharField(max_length=16, default="text")  # text | social_digest
    text = models.TextField()
    embedding = models.BinaryField(null=True, blank=True)
    score = models.FloatField(default=0.0)
    extracted = models.BooleanField(default=False)

    class Meta:
        ordering = ["-score", "idx"]

    def __str__(self) -> str:
        return f"Chunk {self.idx} of {self.source_id}"


class Entity(models.Model):
    class Type(models.TextChoices):
        PERSON = "person", "Person"
        GOVERNMENT = "government", "Government body"
        LEGISLATURE = "legislature", "Legislature"
        POLITICAL_PARTY = "political_party", "Political party"
        ORGANIZATION = "organization", "Organisation"
        BUSINESS = "business", "Business"
        UNION = "union", "Union / association"
        CIVIL_SOCIETY = "civil_society", "Civil society"
        RELIGIOUS = "religious", "Religious body"
        MEDIA = "media", "Media"
        INTERNATIONAL = "international", "International body"
        GROUP = "group", "Population group"
        POLICY = "policy", "Policy / law"
        LOCATION = "location", "Place"
        EVENT = "event", "Event"
        OTHER = "other", "Other"

    SPEAKER_TYPES = {"government", "legislature", "political_party", "organization", "business", "union", "civil_society", "religious", "media", "international"}

    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="entities")
    name = models.CharField(max_length=200)
    key = models.CharField(max_length=200, db_index=True)
    type = models.CharField(max_length=20, choices=Type.choices, default=Type.OTHER)
    description = models.TextField(blank=True)
    aliases = models.JSONField(default=list, blank=True)
    mentions = models.IntegerField(default=1)
    salience = models.FloatField(default=0.0)
    stance = models.FloatField(null=True, blank=True)
    community = models.IntegerField(null=True, blank=True)
    is_stakeholder = models.BooleanField(default=False)

    class Meta:
        ordering = ["-salience", "-mentions"]
        constraints = [models.UniqueConstraint(fields=["project", "key"], name="uniq_entity_key")]

    def __str__(self) -> str:
        return self.name


class Relation(models.Model):
    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="relations")
    source = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="out_relations")
    target = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="in_relations")
    type = models.CharField(max_length=40, default="RELATED_TO")
    description = models.TextField(blank=True)
    weight = models.FloatField(default=1.0)
    evidence = models.ForeignKey(Chunk, null=True, blank=True, on_delete=models.SET_NULL)

    def __str__(self) -> str:
        return f"{self.source} {self.type} {self.target}"


class Argument(models.Model):
    class Side(models.TextChoices):
        SUPPORT = "support", "Supports"
        OPPOSE = "oppose", "Opposes"
        NEUTRAL = "neutral", "Neutral / conditional"

    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="arguments")
    side = models.CharField(max_length=10, choices=Side.choices)
    claim = models.TextField()
    short = models.CharField(max_length=280, blank=True)
    rationale = models.TextField(blank=True)
    strength = models.FloatField(default=0.5)
    frequency = models.IntegerField(default=1)
    emotion = models.CharField(max_length=20, default="neutral")
    groups = models.JSONField(default=list, blank=True)
    evidence = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["side", "-frequency", "-strength"]

    def __str__(self) -> str:
        return f"[{self.side}] {self.claim[:80]}"

    @property
    def stance_value(self) -> float:
        return {"support": 0.75, "oppose": -0.75}.get(self.side, 0.0) * (0.6 + 0.4 * self.strength)


class Community(models.Model):
    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="communities")
    index = models.IntegerField()
    title = models.CharField(max_length=200, blank=True)
    summary = models.TextField(blank=True)
    entity_keys = models.JSONField(default=list, blank=True)
    size = models.IntegerField(default=0)

    class Meta:
        ordering = ["index"]


class Dossier(models.Model):
    project = models.OneToOneField("projects.Project", on_delete=models.CASCADE, related_name="dossier")
    summary = models.TextField(blank=True)
    background = models.TextField(blank=True)
    timeline = models.JSONField(default=list, blank=True)
    key_facts = models.JSONField(default=list, blank=True)
    stakeholders = models.JSONField(default=list, blank=True)
    impact_matrix = models.JSONField(default=list, blank=True)
    sentiment_signals = models.JSONField(default=dict, blank=True)
    narratives = models.JSONField(default=list, blank=True)
    rumors = models.JSONField(default=list, blank=True)
    uncertainties = models.JSONField(default=list, blank=True)
    generated_by = models.CharField(max_length=20, default="heuristic")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Dossier for {self.project}"
