from django.urls import path

from . import views

app_name = "research"

urlpatterns = [
    path("sources/", views.catalog, name="catalog"),
    path("p/<uuid:pk>/research/", views.index, name="index"),
    path("p/<uuid:pk>/research/run/", views.run, name="run"),
    path("p/<uuid:pk>/research/add/", views.manual, name="manual"),
    path("p/<uuid:pk>/research/bulk/", views.bulk, name="bulk"),
    path("p/<uuid:pk>/research/<int:source_id>/", views.detail, name="detail"),
    path("p/<uuid:pk>/research/<int:source_id>/toggle/", views.toggle, name="toggle"),
]
