from django.urls import path

from . import views

app_name = "swarm"

urlpatterns = [
    path("p/<uuid:pk>/population/", views.population, name="population"),
    path("p/<uuid:pk>/population/generate/", views.generate_population, name="generate_population"),
    path("p/<uuid:pk>/agents/<int:agent_id>/", views.agent_detail, name="agent_detail"),
    path("p/<uuid:pk>/simulate/", views.runs, name="runs"),
    path("p/<uuid:pk>/simulate/new/", views.run_create, name="run_create"),
    path("p/<uuid:pk>/experiments/new/", views.experiment_create, name="experiment_create"),
    path("experiments/<uuid:exp_id>/", views.experiment_detail, name="experiment"),
    path("experiments/<uuid:exp_id>/delete/", views.experiment_delete, name="experiment_delete"),
    path("r/<uuid:run_id>/", views.run_detail, name="run_detail"),
    path("r/<uuid:run_id>/metrics.json", views.run_metrics, name="run_metrics"),
    path("r/<uuid:run_id>/feed/", views.run_feed, name="run_feed"),
    path("r/<uuid:run_id>/posts/<int:post_id>/", views.post_thread, name="post_thread"),
    path("r/<uuid:run_id>/start/", views.run_start, name="run_start"),
    path("r/<uuid:run_id>/stop/", views.run_stop, name="run_stop"),
    path("r/<uuid:run_id>/resume/", views.run_resume, name="run_resume"),
    path("r/<uuid:run_id>/inject/", views.run_inject, name="run_inject"),
    path("r/<uuid:run_id>/clone/", views.run_clone, name="run_clone"),
    path("r/<uuid:run_id>/delete/", views.run_delete, name="run_delete"),
    path("r/<uuid:run_id>/interventions/<int:iv_id>/delete/", views.intervention_delete, name="intervention_delete"),
    path("r/<uuid:run_id>/export/<str:what>.csv", views.run_export, name="run_export"),
]
