"""Evidence gathered during deep research: articles, social posts, documents."""

from __future__ import annotations

from django.db import models


class Source(models.Model):
    class Platform(models.TextChoices):
        WEB = "web", "Web"
        NEWS = "news", "News"
        REDDIT = "reddit", "Reddit"
        X = "x", "X (Twitter)"
        FACEBOOK = "facebook", "Facebook"
        LINKEDIN = "linkedin", "LinkedIn"
        TIKTOK = "tiktok", "TikTok"
        INSTAGRAM = "instagram", "Instagram"
        YOUTUBE = "youtube", "YouTube"
        BLUESKY = "bluesky", "Bluesky"
        MASTODON = "mastodon", "Mastodon"
        TELEGRAM = "telegram", "Telegram"
        WIKIPEDIA = "wikipedia", "Wikipedia"
        UPLOAD = "upload", "Uploaded file"
        MANUAL = "manual", "Pasted text"

    class Kind(models.TextChoices):
        ARTICLE = "article", "Article"
        SOCIAL_POST = "social_post", "Social post"
        COMMENT = "comment", "Comment"
        SNIPPET = "snippet", "Search snippet"
        DOCUMENT = "document", "Document"
        REFERENCE = "reference", "Reference"

    SOCIAL_PLATFORMS = {"reddit", "x", "facebook", "linkedin", "tiktok", "instagram", "youtube", "bluesky", "mastodon", "telegram"}

    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="sources")
    connector = models.CharField(max_length=40, db_index=True)
    platform = models.CharField(max_length=16, choices=Platform.choices, default=Platform.WEB, db_index=True)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.SNIPPET)
    url = models.URLField(max_length=1000, blank=True)
    url_hash = models.CharField(max_length=40, db_index=True)
    title = models.CharField(max_length=500, blank=True)
    author = models.CharField(max_length=200, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    query = models.CharField(max_length=300, blank=True)
    snippet = models.TextField(blank=True)
    text = models.TextField(blank=True)
    metrics = models.JSONField(default=dict, blank=True)
    meta = models.JSONField(default=dict, blank=True)
    sentiment = models.FloatField(null=True, blank=True)
    stance = models.FloatField(null=True, blank=True, help_text="-1 against the policy … +1 for it")
    stance_confidence = models.FloatField(null=True, blank=True)
    relevance = models.FloatField(default=0.0)
    included = models.BooleanField(default=True)
    full_text = models.BooleanField(default=False)
    file = models.FileField(upload_to="sources/%Y/%m", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-relevance", "-created_at"]
        constraints = [models.UniqueConstraint(fields=["project", "url_hash"], name="uniq_source_per_project")]

    def __str__(self) -> str:
        return self.title or self.url or f"Source {self.pk}"

    @property
    def body(self) -> str:
        return self.text or self.snippet

    @property
    def is_social(self) -> bool:
        return self.platform in self.SOCIAL_PLATFORMS

    @property
    def domain(self) -> str:
        from apps.core.text.textutils import domain_of

        return domain_of(self.url) if self.url else ""

    @property
    def engagement(self) -> int:
        m = self.metrics or {}
        return int(sum(int(m.get(k) or 0) for k in ("likes", "comments", "shares", "score")))
