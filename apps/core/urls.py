from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("settings/", views.settings_view, name="settings"),
    path("settings/test-llm/", views.test_llm, name="test_llm"),
    path("status/pill/", views.status_pill, name="status_pill"),
    path("jobs/", views.jobs, name="jobs"),
    path("jobs/<uuid:pk>/", views.job_status, name="job_status"),
    path("jobs/<uuid:pk>/cancel/", views.job_cancel, name="job_cancel"),
    path("about/", views.about, name="about"),
    path("health/", views.health, name="health"),
    path("robots.txt", views.robots, name="robots"),
]
