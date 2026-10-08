"""Run a full rehearsal from the command line (handy for batch experiments)."""

from django.core.management.base import BaseCommand, CommandError

from apps.core.geo.countries import COUNTRIES
from apps.core.jobs import run_inline
from apps.projects.brief import screen_request
from apps.projects.models import Project


class Command(BaseCommand):
    help = 'Prompt -> brief -> research -> knowledge -> population -> simulation -> report. Example: python manage.py rehearse "A 2% levy on mobile money" --country KE --depth quick'

    def add_arguments(self, parser):
        parser.add_argument("prompt")
        parser.add_argument("--country", default="KE", choices=sorted(COUNTRIES))
        parser.add_argument("--depth", default="quick", choices=["quick", "standard", "deep"])
        parser.add_argument("--population", type=int, default=0, help="Number of agents (default depends on depth).")
        parser.add_argument("--preset", default="", help="Simulation preset: quick, standard, deep or free.")
        parser.add_argument("--no-research", action="store_true", help="Skip web research (offline).")
        parser.add_argument("--evidence", nargs="*", default=[], help="Text or Markdown files to add as evidence.")

    def handle(self, *args, **opts):
        prompt = opts["prompt"].strip()
        screen = screen_request(prompt)
        if not screen["allowed"]:
            raise CommandError(f"Refused: {', '.join(screen['concerns'])}")
        project = Project.objects.create(title=prompt[:80], prompt=prompt, country=opts["country"], depth=opts["depth"])
        if opts["evidence"]:
            from pathlib import Path

            from apps.research.pipeline import add_text

            for path in opts["evidence"]:
                p = Path(path)
                add_text(project, p.stem, p.read_text(encoding="utf-8", errors="replace"))
        options = {"research": not opts["no_research"]}
        if opts["population"]:
            options["population"] = opts["population"]
        if opts["preset"]:
            options["preset"] = opts["preset"]
        self.stdout.write(f"Project {project.pk}: running Autopilot…")
        job = run_inline("apps.projects.tasks.autopilot", kind="autopilot", label=f"Autopilot: {project.title}", project=project, payload={"project_id": str(project.pk), "options": options})
        for stamp, level, message in job.log[-25:]:
            self.stdout.write(f"  {stamp} {message}")
        if job.status != "succeeded":
            raise CommandError(f"Autopilot {job.status}: {job.error[:1000]}")
        summary = job.result.get("simulation", {}).get("summary", {})
        self.stdout.write(self.style.SUCCESS(
            f"Done. Mean opinion {summary.get('start_mean', 0):+.2f} -> {summary.get('end_mean', 0):+.2f}; "
            f"oppose {summary.get('end_oppose', 0):.0%}, support {summary.get('end_support', 0):.0%}."
        ))
        self.stdout.write(f"Open http://127.0.0.1:8000/p/{project.pk}/ to explore the results.")
