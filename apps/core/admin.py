from django.contrib import admin

from .models import Job, LLMCacheEntry, LLMUsage, SiteConfig


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ("label", "kind", "status", "progress", "project", "created_at")
    list_filter = ("status", "kind")
    search_fields = ("label", "message", "error")
    readonly_fields = ("created_at", "started_at", "finished_at", "heartbeat_at")


@admin.register(LLMUsage)
class LLMUsageAdmin(admin.ModelAdmin):
    list_display = ("purpose", "provider", "model", "calls", "cached_calls", "errors", "prompt_tokens", "completion_tokens", "seconds", "cost_usd", "project")
    list_filter = ("provider", "purpose")


@admin.register(LLMCacheEntry)
class LLMCacheEntryAdmin(admin.ModelAdmin):
    list_display = ("key", "model", "hits", "created_at")
    search_fields = ("key", "model")


admin.site.register(SiteConfig)
admin.site.site_header = "Baraza administration"
admin.site.site_title = "Baraza admin"
