"""Create a fully worked demo project that works offline."""

from django.core.management.base import BaseCommand

from apps.core.jobs import run_inline
from apps.projects.models import Project
from apps.research.pipeline import add_text

DEMO_PROMPT = (
    "The government proposes a 2% levy on mobile money transfers above KES 1,000 to fund county hospitals. "
    "How will Kenyans react online over the first week, which groups will oppose it most, and which messages "
    "could reduce opposition?"
)

# Clearly illustrative texts so the demo works without internet access.
DEMO_EVIDENCE = [
    (
        "Illustrative: proposal summary",
        "ILLUSTRATIVE DEMO TEXT - NOT REAL DATA. The Ministry of Finance proposes a Health Facilities Levy of 2% on "
        "mobile money transfers above KES 1,000. The Treasury estimates the levy would raise about KES 40 billion a "
        "year, ring-fenced for county hospitals: medicines, equipment and 5,000 additional nurses. Transfers below "
        "KES 1,000 would be exempt to protect low-income users. The Kenya Revenue Authority would collect the levy "
        "through mobile network operators from 1 July.",
    ),
    (
        "Illustrative: supporters",
        "ILLUSTRATIVE DEMO TEXT - NOT REAL DATA. The Kenya Medical Practitioners union and several county health "
        "executives welcomed the proposal, arguing that county hospitals are chronically short of drugs and staff. "
        "Supporters say the exemption for small transfers protects the poor and that a ring-fenced fund is more "
        "transparent than general taxation. Some economists said earmarked levies can work if spending is published.",
    ),
    (
        "Illustrative: critics",
        "ILLUSTRATIVE DEMO TEXT - NOT REAL DATA. Small traders' associations and the consumers federation warned that "
        "the levy will hurt market vendors and boda boda riders who move money daily, and that people will split "
        "payments to avoid it. Opposition MPs said Kenyans are already overtaxed and doubted the money would reach "
        "hospitals given past corruption scandals. Fintech firms warned of a shift back to cash.",
    ),
    (
        "Illustrative: online reactions digest",
        "ILLUSTRATIVE DEMO TEXT - NOT REAL DATA. Typical posts: 'Another tax on M-Pesa? Tumechoka!' 'If it really "
        "pays for nurses in our county I can support it, but show us the receipts.' 'Mama mboga will pay this, not "
        "the big people.' 'Hospitals have no drugs, something must be done.' 'Where did the last levy go?' "
        "'#RejectMpesaLevy' trended briefly, while health workers shared photos of empty pharmacy shelves.",
    ),
]


class Command(BaseCommand):
    help = "Create and run a demo rehearsal (works offline; uses whatever language model is configured, or heuristics)."

    def add_arguments(self, parser):
        parser.add_argument("--population", type=int, default=120)
        parser.add_argument("--preset", default="quick", help="quick, standard, deep or free")

    def handle(self, *args, **opts):
        project = Project.objects.create(title="Demo: mobile money levy for county hospitals", prompt=DEMO_PROMPT, country="KE", depth="quick")
        for title, text in DEMO_EVIDENCE:
            add_text(project, title, text)
        self.stdout.write("Running the demo (brief, knowledge, population, simulation, report)…")
        job = run_inline(
            "apps.projects.tasks.autopilot",
            kind="autopilot",
            label="Autopilot: demo",
            project=project,
            payload={"project_id": str(project.pk), "options": {"research": False, "population": opts["population"], "preset": opts["preset"]}},
        )
        if job.status != "succeeded":
            self.stdout.write(self.style.ERROR(f"Demo failed: {job.error[:1500]}"))
            return
        self.stdout.write(self.style.SUCCESS(f"Demo ready: http://127.0.0.1:8000/p/{project.pk}/"))
