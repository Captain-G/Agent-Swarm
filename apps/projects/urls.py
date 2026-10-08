from django.urls import path

from . import views

app_name = "projects"

urlpatterns = [
    path("projects/", views.project_list, name="list"),
    path("projects/new/", views.create, name="create"),
    path("projects/regions/", views.country_regions, name="regions"),
    path("p/<uuid:pk>/", views.overview, name="overview"),
    path("p/<uuid:pk>/brief/", views.brief, name="brief"),
    path("p/<uuid:pk>/brief/regenerate/", views.regenerate_brief, name="regenerate_brief"),
    path("p/<uuid:pk>/autopilot/", views.autopilot, name="autopilot"),
    path("p/<uuid:pk>/settings/", views.update_settings, name="settings"),
    path("p/<uuid:pk>/delete/", views.delete, name="delete"),
    path("p/<uuid:pk>/export.json", views.export_json, name="export"),
]
