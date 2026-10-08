from django.urls import path

from . import views

app_name = "knowledge"

urlpatterns = [
    path("p/<uuid:pk>/knowledge/", views.index, name="index"),
    path("p/<uuid:pk>/knowledge/build/", views.build, name="build"),
    path("p/<uuid:pk>/knowledge/graph.json", views.graph_data, name="graph"),
    path("p/<uuid:pk>/knowledge/search/", views.ask, name="search"),
    path("p/<uuid:pk>/knowledge/arguments/add/", views.add_argument, name="add_argument"),
    path("p/<uuid:pk>/knowledge/arguments/<int:arg_id>/delete/", views.delete_argument, name="delete_argument"),
]
