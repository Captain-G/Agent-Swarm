"""Optional login requirement, and showing simulated times in the project's local time zone."""

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.utils import timezone

from .config import cfg

OPEN_PREFIXES = ("/accounts/", "/static/", "/admin/", "/health/")


class OptionalLoginRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if cfg("REQUIRE_LOGIN", False) and not request.user.is_authenticated:
            if not request.path.startswith(OPEN_PREFIXES):
                return redirect_to_login(request.get_full_path(), settings.LOGIN_URL)
        return self.get_response(request)


class ProjectTimezoneMiddleware:
    """Show times on project pages in the local time of the simulated country.

    Simulated posts are timestamped in UTC; a Nigerian project should read in
    West Africa Time and a Kenyan one in East Africa Time.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            return self.get_response(request)
        finally:
            timezone.deactivate()

    def process_view(self, request, view_func, view_args, view_kwargs):
        country = None
        try:
            if "run_id" in view_kwargs:
                from apps.swarm.models import SimulationRun

                country = SimulationRun.objects.filter(pk=view_kwargs["run_id"]).values_list("project__country", flat=True).first()
            elif "exp_id" in view_kwargs:
                from apps.swarm.models import Experiment

                country = Experiment.objects.filter(pk=view_kwargs["exp_id"]).values_list("project__country", flat=True).first()
            elif "pk" in view_kwargs and request.resolver_match and request.resolver_match.app_name in ("projects", "research", "knowledge", "swarm", "insights"):
                from apps.projects.models import Project

                country = Project.objects.filter(pk=view_kwargs["pk"]).values_list("country", flat=True).first()
        except (ValueError, TypeError):
            country = None
        if country:
            from apps.swarm.engine.config import country_timezone

            timezone.activate(country_timezone(country))
        return None
