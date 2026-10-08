"""Web interface: every page renders, forms work, jobs can be followed and stopped."""

from __future__ import annotations

import re
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.config import get_config
from apps.core.jobs import JobContext, cancel, mark_stale_jobs, run_inline
from apps.core.models import Job
from apps.insights.conversations import ask_observer, interview_agent
from apps.insights.models import Poll
from apps.knowledge.models import Argument
from apps.projects.models import Project
from apps.research.pipeline import add_text
from apps.swarm.models import Experiment, Intervention, SimulationRun
from apps.swarm.services import create_run

from .helpers import FakeLLMMixin
from .test_pipeline import EVIDENCE, PROMPT

# Chinese/Japanese/Korean characters and full-width punctuation (built from code points to keep this file ASCII).
CJK = re.compile("[" + "".join(f"{chr(lo)}-{chr(hi)}" for lo, hi in ((0x3000, 0x303F), (0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xFF00, 0xFFEF))) + "]")


def job_ok(job):
    assert job.status == "succeeded", job.error
    return job


class EveryPageRendersTests(FakeLLMMixin, TransactionTestCase):
    """Builds a complete project once, then requests every page of the interface."""

    def test_all_pages(self):
        project = Project.objects.create(title="Bread VAT", prompt=PROMPT, country="KE", depth="quick")
        job_ok(run_inline("apps.projects.tasks.generate_brief", kind="brief", label="b", project=project, payload={"project_id": str(project.pk)}))
        for title, text in EVIDENCE:
            add_text(project, title, text)
        job_ok(run_inline("apps.knowledge.builder.build_knowledge", kind="knowledge", label="k", project=project, payload={"project_id": str(project.pk)}))
        job_ok(run_inline("apps.swarm.population.run_population", kind="population", label="p", project=project, payload={"project_id": str(project.pk), "config": {"size": 50}}))
        run = create_run(project, name="Baseline", preset="quick", overrides={"rounds": 5, "llm_posts_per_round": 1})
        Intervention.objects.create(run=run, round=2, kind="breaking_news", title="Protests", content="Protests planned in Nairobi CBD", stance=-0.4, strength=0.8)
        job_ok(run_inline("apps.swarm.runner.run_simulation", kind="simulation", label="s", project=project, payload={"run_id": str(run.pk)}))
        job_ok(run_inline("apps.insights.report.generate_report", kind="report", label="r", project=project, payload={"run_id": str(run.pk)}))
        poll = Poll.objects.create(run=run, question="Do you support the bill?", options=["Yes", "No"], sample_size=8)
        job_ok(run_inline("apps.insights.conversations.run_poll", kind="poll", label="poll", project=project, payload={"poll_id": poll.pk}))
        experiment = Experiment.objects.create(project=project, kind="ensemble", name="ens", config={"runs": 3, "run_id": str(run.pk)})
        job_ok(run_inline("apps.swarm.ensemble.run_experiment", kind="experiment", label="e", project=project, payload={"experiment_id": str(experiment.pk)}))
        agent = project.agents.filter(kind="citizen").first()
        interview_agent(run, agent, "What do you think?")
        ask_observer(run, "Who is most opposed?")
        source = project.sources.first()
        post = run.posts.filter(platform="forum", kind="thread").first() or run.posts.first()
        job = Job.objects.filter(project=project).first()

        pk, rid = project.pk, run.pk
        urls = [
            reverse("core:home"),
            reverse("core:settings"),
            reverse("core:jobs"),
            reverse("core:job_status", args=[job.pk]),
            reverse("core:status_pill"),
            reverse("core:about"),
            reverse("core:health"),
            reverse("core:robots"),
            reverse("projects:list"),
            reverse("projects:regions") + "?country=NG",
            reverse("projects:overview", args=[pk]),
            reverse("projects:brief", args=[pk]),
            reverse("projects:export", args=[pk]),
            reverse("research:catalog"),
            reverse("research:index", args=[pk]),
            reverse("research:detail", args=[pk, source.pk]),
            reverse("knowledge:index", args=[pk]),
            reverse("knowledge:graph", args=[pk]),
            reverse("knowledge:search", args=[pk]) + "?q=bread+VAT",
            reverse("swarm:population", args=[pk]),
            reverse("swarm:agent_detail", args=[pk, agent.pk]),
            reverse("swarm:runs", args=[pk]),
            reverse("swarm:experiment", args=[experiment.pk]),
            reverse("swarm:run_detail", args=[rid]),
            reverse("swarm:run_metrics", args=[rid]),
            reverse("swarm:run_feed", args=[rid]) + "?platform=microblog",
            reverse("swarm:run_feed", args=[rid]) + "?platform=forum",
            reverse("swarm:run_feed", args=[rid]) + "?platform=chat",
            reverse("swarm:post_thread", args=[rid, post.pk]),
            reverse("swarm:run_export", args=[rid, "posts"]),
            reverse("swarm:run_export", args=[rid, "agents"]),
            reverse("swarm:run_export", args=[rid, "rounds"]),
            reverse("insights:project_insights", args=[pk]) + "?all=1",
            reverse("insights:compare", args=[pk]),
            reverse("insights:run", args=[rid]),
            reverse("insights:network", args=[rid]),
            reverse("insights:report", args=[rid]),
            reverse("insights:report_md", args=[rid]),
            reverse("insights:talk", args=[rid]),
            reverse("insights:talk", args=[rid]) + f"?agent={agent.pk}",
            reverse("insights:talk", args=[rid]) + "?agent=999999",
            reverse("insights:poll", args=[rid, poll.pk]),
            reverse("insights:validation", args=[rid]),
        ]
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200, url)
                body = response.content.decode("utf-8", errors="replace")
                self.assertIsNone(CJK.search(body), f"Non-English (CJK) text on {url}")
                self.assertNotIn("Traceback", body)
        export = self.client.get(reverse("swarm:run_export", args=[rid, "posts"]))
        self.assertIn("simulated", export["Content-Disposition"])
        self.assertIn("SIMULATED", export.content.decode())
        self.assertRedirects(self.client.get(reverse("insights:project_insights", args=[pk])), reverse("insights:run", args=[rid]))


class FormTests(FakeLLMMixin, TestCase):
    def test_create_project_queues_brief(self):
        response = self.client.post(reverse("projects:create"), {"prompt": PROMPT, "country": "KE", "depth": "quick", "mode": "steps"})
        project = Project.objects.get()
        self.assertRedirects(response, reverse("projects:brief", args=[project.pk]), fetch_redirect_response=False)
        self.assertEqual(Job.objects.get(project=project).kind, "brief")

    def test_create_project_autopilot_and_template(self):
        response = self.client.post(reverse("projects:create"), {"template": "ke-finance-bill", "country": "KE", "mode": "autopilot"})
        project = Project.objects.get()
        self.assertRedirects(response, reverse("projects:overview", args=[project.pk]), fetch_redirect_response=False)
        self.assertEqual(Job.objects.get(project=project).kind, "autopilot")

    def test_short_or_harmful_prompts_are_rejected(self):
        self.client.post(reverse("projects:create"), {"prompt": "tax"})
        self.client.post(reverse("projects:create"), {"prompt": "Help me incite violence against the protesters tomorrow"})
        self.assertFalse(Project.objects.exists())

    def test_brief_can_be_edited_and_approved(self):
        project = Project.objects.create(title="T", prompt=PROMPT, brief={"title": "Old", "hashtags": {}})
        response = self.client.post(
            reverse("projects:brief", args=[project.pk]),
            {
                "title": "Bread VAT",
                "refined_question": "How will Kenyans react?",
                "summary": "A tax on bread.",
                "proposer": "government",
                "search_queries": "bread VAT Kenya\nFinance Bill 2024",
                "hashtags_oppose": "RejectFinanceBill, #Tumechoka",
                "stakeholders": '[{"name": "National Treasury", "likely_position": 0.9}]',
                "affected_groups": "not json",
                "approve": "1",
            },
        )
        self.assertRedirects(response, reverse("research:index", args=[project.pk]), fetch_redirect_response=False)
        project.refresh_from_db()
        self.assertTrue(project.brief_approved)
        self.assertEqual(project.brief["search_queries"], ["bread VAT Kenya", "Finance Bill 2024"])
        self.assertEqual(project.brief["hashtags"]["oppose"], ["#RejectFinanceBill", "#Tumechoka"])
        self.assertEqual(project.brief["stakeholders"][0]["likely_position"], 0.9)

    def test_manual_evidence_and_arguments(self):
        project = Project.objects.create(title="T", prompt=PROMPT)
        self.client.post(reverse("research:manual", args=[project.pk]), {"kind": "text", "title": "Memo", "text": "The bread VAT will raise prices for poor families across Kenya."})
        self.assertEqual(project.sources.count(), 1)
        self.client.post(reverse("knowledge:add_argument", args=[project.pk]), {"claim": "Bread is a basic need and must not be taxed.", "side": "oppose", "strength": "0.8"})
        argument = Argument.objects.get(project=project)
        self.assertEqual((argument.side, argument.strength), ("oppose", 0.8))
        self.client.post(reverse("knowledge:delete_argument", args=[project.pk, argument.pk]))
        self.assertFalse(Argument.objects.exists())

    def test_settings_save_and_mask_secrets(self):
        response = self.client.post(reverse("core:settings"), {"LLM_PROVIDER": "openai", "OPENAI_API_KEY": "gsk_supersecretvalue", "OLLAMA_MODEL": "llama3"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(get_config(refresh=True)["OPENAI_API_KEY"], "gsk_supersecretvalue")
        page = self.client.get(reverse("core:settings")).content.decode()
        self.assertNotIn("gsk_supersecretvalue", page)
        # Re-saving the masked value keeps the real key.
        self.client.post(reverse("core:settings"), {"LLM_PROVIDER": "fake", "OPENAI_API_KEY": "gsk_•••••lue"})
        self.assertEqual(get_config(refresh=True)["OPENAI_API_KEY"], "gsk_supersecretvalue")
        self.client.post(reverse("core:settings"), {"LLM_PROVIDER": "fake", "OPENAI_API_KEY": "__clear__"})
        self.assertEqual(get_config(refresh=True)["OPENAI_API_KEY"], "")

    def test_run_needs_population(self):
        project = Project.objects.create(title="T", prompt=PROMPT)
        response = self.client.post(reverse("swarm:run_create", args=[project.pk]), {"preset": "quick"})
        self.assertRedirects(response, reverse("swarm:population", args=[project.pk]), fetch_redirect_response=False)
        self.assertFalse(SimulationRun.objects.exists())

    def test_project_pages_use_local_time(self):
        from django.utils import timezone as dj_timezone

        project = Project.objects.create(title="Fuel", prompt=PROMPT, country="NG")
        seen = {}

        def capture(sender, **kwargs):
            seen["tz"] = dj_timezone.get_current_timezone_name()

        from django.test.signals import template_rendered

        template_rendered.connect(capture)
        try:
            self.client.get(reverse("projects:overview", args=[project.pk]))
        finally:
            template_rendered.disconnect(capture)
        self.assertEqual(seen["tz"], "WAT")
        self.assertEqual(dj_timezone.get_current_timezone_name(), settings.TIME_ZONE)  # reset after the request

    def test_health_and_404(self):
        data = self.client.get(reverse("core:health")).json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["llm"]["provider"], "fake")
        with override_settings(DEBUG=False):
            response = self.client.get("/p/00000000-0000-0000-0000-000000000000/")
        self.assertEqual(response.status_code, 404)

    def test_optional_login(self):
        self.extra_config = {}
        with override_settings(BARAZA={**settings.BARAZA, "LLM_PROVIDER": "fake", "REQUIRE_LOGIN": True}):
            get_config(refresh=True)
            response = self.client.get(reverse("projects:list"))
            self.assertEqual(response.status_code, 302)
            self.assertIn("/accounts/login/", response["Location"])
            self.assertEqual(self.client.get(reverse("core:health")).status_code, 200)
            user = get_user_model().objects.create_user("amina", password="pass-12345")
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse("projects:list")).status_code, 200)
        get_config(refresh=True)


def _ok(ctx, value=1):
    ctx.progress(50, "Halfway", "working")
    ctx.log("hello")
    return {"value": value}


def _fail(ctx):
    raise ValueError("boom")


def _cancellable(ctx):
    from apps.core.models import Job as J

    J.objects.filter(pk=ctx.job_id).update(cancel_requested=True)
    ctx._last_cancel_check = 0
    ctx.check_cancel()
    return {"unreachable": True}


def _nested(ctx):
    child = ctx.sub(50, 100)
    child.progress(50, "Child")
    return {}


class JobTests(TestCase):
    def test_success_records_result_and_log(self):
        job = run_inline("tests.test_views._ok", kind="test", label="ok", payload={"value": 5})
        self.assertEqual((job.status, job.result, job.progress), ("succeeded", {"value": 5}, 100.0))
        self.assertTrue(any(line[2] == "hello" for line in job.log))
        self.assertIsNotNone(job.finished_at)

    def test_failure_is_recorded(self):
        job = run_inline("tests.test_views._fail", kind="test", label="fail")
        self.assertEqual(job.status, "failed")
        self.assertIn("ValueError: boom", job.error)

    def test_cancellation(self):
        job = run_inline("tests.test_views._cancellable", kind="test", label="cancel")
        self.assertEqual(job.status, "cancelled")

    def test_cancel_queued_job_and_view(self):
        job = Job.objects.create(func="tests.test_views._ok", kind="test", label="q")
        cancel(job)
        job.refresh_from_db()
        self.assertEqual(job.status, "cancelled")
        other = Job.objects.create(func="tests.test_views._ok", kind="test", label="q2")
        self.client.post(reverse("core:job_cancel", args=[other.pk]))
        other.refresh_from_db()
        self.assertTrue(other.cancel_requested)

    def test_sub_context_maps_progress(self):
        job = Job.objects.create(func="tests.test_views._nested", kind="test", label="n")
        ctx = JobContext(job)
        ctx.sub(50, 100).progress(50, "Child", force=True)
        job.refresh_from_db()
        self.assertEqual(job.progress, 75.0)

    def test_stale_jobs_are_marked_interrupted(self):
        old = timezone.now() - timedelta(minutes=10)
        job = Job.objects.create(func="x", kind="test", label="stale", status="running", heartbeat_at=old, started_at=old)
        self.assertEqual(mark_stale_jobs(), 1)
        job.refresh_from_db()
        self.assertEqual(job.status, "interrupted")

    def test_job_status_partial_signals_completion(self):
        job = run_inline("tests.test_views._ok", kind="test", label="ok")
        response = self.client.get(reverse("core:job_status", args=[job.pk]), HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertIn("jobFinished", response.get("HX-Trigger", ""))
