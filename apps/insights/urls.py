from django.urls import path

from . import views

app_name = "insights"

urlpatterns = [
    path("p/<uuid:pk>/insights/", views.project_insights, name="project_insights"),
    path("p/<uuid:pk>/compare/", views.compare, name="compare"),
    path("r/<uuid:run_id>/insights/", views.run_insights, name="run"),
    path("r/<uuid:run_id>/network.json", views.network_data, name="network"),
    path("r/<uuid:run_id>/report/", views.report, name="report"),
    path("r/<uuid:run_id>/report/generate/", views.report_generate, name="report_generate"),
    path("r/<uuid:run_id>/report.md", views.report_markdown, name="report_md"),
    path("r/<uuid:run_id>/talk/", views.talk, name="talk"),
    path("r/<uuid:run_id>/talk/send/", views.talk_send, name="talk_send"),
    path("r/<uuid:run_id>/polls/new/", views.poll_create, name="poll_create"),
    path("r/<uuid:run_id>/polls/<int:poll_id>/", views.poll_detail, name="poll"),
    path("r/<uuid:run_id>/validation/", views.validation, name="validation"),
]
