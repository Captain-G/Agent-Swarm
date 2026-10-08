"""Root URL configuration."""

from django.conf import settings
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from django.views.static import serve

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/login/", auth_views.LoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("apps.core.urls")),
    path("", include("apps.projects.urls")),
    path("", include("apps.research.urls")),
    path("", include("apps.knowledge.urls")),
    path("", include("apps.swarm.urls")),
    path("", include("apps.insights.urls")),
    path(f"{settings.MEDIA_URL.strip('/')}/<path:path>", serve, {"document_root": settings.MEDIA_ROOT}),
]

handler404 = "apps.core.views.error_404"
handler500 = "apps.core.views.error_500"
