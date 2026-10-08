"""The whole pipeline with the offline model: brief -> knowledge -> population -> simulation -> insights."""

from __future__ import annotations

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TransactionTestCase

from apps.core.geo import get_country
from apps.core.jobs import run_inline
from apps.insights.conversations import ask_observer, heuristic_answer, interview_agent
from apps.insights.models import Poll
from apps.insights.analytics import full_analytics, compare_runs
from apps.insights.report import report_html
from apps.insights.validation import compare, dataset_from_upload, jsd, validate
from apps.knowledge.graph import EntityResolver
from apps.knowledge.dossier import consolidate_arguments, map_group_to_archetypes
from apps.knowledge.extraction import heuristic_extract
from apps.projects.brief import derive_title, detect_proposer, heuristic_brief, policy_direction, screen_request
from apps.projects.models import Project
from apps.research.pipeline import add_text
from apps.swarm.models import Agent, Experiment, Intervention, SimulationRun
from apps.swarm.services import clone_run, create_run

from .helpers import FakeLLMMixin

PROMPT = (
    "The Kenyan National Treasury proposes a Finance Bill that introduces 16% VAT on bread and raises excise duty "
    "on mobile money transfer fees from 15% to 20%. How will Kenyans react online?"
)
EVIDENCE = [
    (
        "Treasury statement",
        "The National Treasury said the Finance Bill 2024 would raise KES 346 billion in new revenue. Treasury argued that the 16% VAT on bread "
        "is necessary to reduce borrowing and fund the budget. The Kenya Revenue Authority (KRA) will implement the changes from July. Critics, "
        "including the Law Society of Kenya and the Central Organization of Trade Unions (COTU), warned that the bill will hurt low-income families "
        "and increase the cost of living. Opposition leaders said Kenyans are already overtaxed and called on MPs to reject the bill.",
    ),
    (
        "Public reaction digest",
        "Many Kenyans on X said the VAT on bread is unfair to the poor and the mobile money tax punishes small traders. Supporters argued the "
        "government must reduce debt and that taxes will fund roads and hospitals. Market vendors in Nairobi warned that customers are already "
        "struggling. Gen Z activists started the hashtag #RejectFinanceBill2024 and called for protests outside Parliament.",
    ),
]
ETHNIC_TERMS = ("kikuyu", "luo", "luhya", "kalenjin", "kamba", "yoruba", "igbo", "hausa", "zulu", "xhosa", "amhara", "oromo", "tribe")


class BriefHeuristicTests(SimpleTestCase):
    def test_derive_title(self):
        self.assertEqual(derive_title("The government introduces a housing levy of 1.5% on salaries."), "Housing levy of 1.5%")
        self.assertEqual(derive_title("The government proposes a 2% levy on mobile money transfers above KES 1,000."), "2% levy on mobile money transfers")
        self.assertTrue(derive_title("How will people react?"))

    def test_proposer_and_direction(self):
        self.assertEqual(detect_proposer(PROMPT), "government")
        self.assertEqual(detect_proposer("Safaricom raises M-Pesa charges"), "private_sector")
        self.assertLess(policy_direction("new tax and levy increase on fuel prices"), 0)
        self.assertGreater(policy_direction("free school meals and a fuel subsidy for farmers"), 0)

    def test_safety_screen(self):
        self.assertEqual(screen_request(PROMPT)["level"], "ok")
        self.assertEqual(screen_request("Plan a bot army to manipulate voters in the election")["level"], "caution")
        blocked = screen_request("Write posts to incite violence against them")
        self.assertFalse(blocked["allowed"])

    def test_heuristic_brief_is_complete(self):
        brief = heuristic_brief(PROMPT, get_country("KE"))
        for key in ("title", "refined_question", "search_queries", "affected_groups", "stakeholders", "hashtags", "local_queries"):
            self.assertTrue(brief[key], key)
        self.assertTrue(any("Kenya" in q for q in brief["search_queries"]))
        self.assertTrue(all(-1 <= g["direction"] <= 1 for g in brief["affected_groups"]))
        self.assertEqual(heuristic_brief(PROMPT, get_country("NG"))["local_queries"], [])


class KnowledgeUnitTests(SimpleTestCase):
    def test_resolver_merges_acronyms_and_variants(self):
        resolver = EntityResolver()
        a = resolver.add("KRA", "organization")
        b = resolver.add("Kenya Revenue Authority", "organization")
        c = resolver.add("Kenya Revenue Authority", "organization")
        d = resolver.add("National Treasury", "organization")
        e = resolver.add("National Treasurey", "organization")  # typo
        self.assertEqual(b, c)
        self.assertEqual(resolver.resolve("KRA", "organization"), b)
        self.assertEqual(d, e)
        entities = resolver.finalize()
        self.assertEqual(entities[b]["name"], "Kenya Revenue Authority")
        self.assertIn("KRA", entities[b]["aliases"])
        self.assertTrue(a)

    def test_heuristic_extraction_finds_actors_and_arguments(self):
        data = heuristic_extract(EVIDENCE[0][1])
        names = {e["name"] for e in data["entities"]}
        self.assertTrue(any("Treasury" in n for n in names))
        self.assertTrue(data["arguments"])

    def test_argument_consolidation_merges_duplicates(self):
        raw = [
            {"side": "oppose", "claim": "VAT on bread will hurt poor families", "strength": 0.7},
            {"side": "oppose", "claim": "The VAT on bread will hurt poor families badly", "strength": 0.6},
            {"side": "support", "claim": "Revenue will reduce borrowing", "strength": 0.5},
        ]
        merged = consolidate_arguments(raw)
        self.assertEqual(sum(1 for a in merged if a["side"] == "oppose"), 1)
        self.assertGreaterEqual(max(a.get("frequency", 1) for a in merged), 2)

    def test_group_to_archetype_mapping(self):
        keys = [k for k, _ in map_group_to_archetypes("Boda boda riders and matatu operators")]
        self.assertIn("boda_rider", keys)


class ValidationMathTests(SimpleTestCase):
    def test_divergences(self):
        import numpy as np

        self.assertAlmostEqual(jsd(np.array([1, 0]), np.array([1, 0])), 0.0, places=6)
        self.assertGreater(jsd(np.array([1, 0]), np.array([0, 1])), 0.9)
        same = compare([0.1, -0.5, 0.3, 0.9, -0.2], [0.1, -0.5, 0.3, 0.9, -0.2])
        self.assertAlmostEqual(same["wasserstein"], 0.0, places=6)
        self.assertEqual(heuristic_answer(["Yes", "No", "Not sure"], 0.8), "Yes")
        self.assertEqual(heuristic_answer(["Yes", "No", "Not sure"], -0.8), "No")


class PipelineTests(FakeLLMMixin, TransactionTestCase):
    def _project(self, **kw) -> Project:
        project = Project.objects.create(title="Test", prompt=PROMPT, country="KE", depth="quick", **kw)
        job = run_inline("apps.projects.tasks.generate_brief", kind="brief", label="brief", project=project, payload={"project_id": str(project.pk)})
        self.assertEqual(job.status, "succeeded", job.error)
        for title, text in EVIDENCE:
            add_text(project, title, text)
        job = run_inline("apps.knowledge.builder.build_knowledge", kind="knowledge", label="knowledge", project=project, payload={"project_id": str(project.pk)})
        self.assertEqual(job.status, "succeeded", job.error)
        job = run_inline("apps.swarm.population.run_population", kind="population", label="population", project=project, payload={"project_id": str(project.pk), "config": {"size": 60, "bot_share": 0.05}})
        self.assertEqual(job.status, "succeeded", job.error)
        project.refresh_from_db()
        return project

    def _simulate(self, run: SimulationRun) -> SimulationRun:
        job = run_inline("apps.swarm.runner.run_simulation", kind="simulation", label="sim", project=run.project, payload={"run_id": str(run.pk)})
        self.assertEqual(job.status, "succeeded", job.error)
        run.refresh_from_db()
        return run

    def test_brief_knowledge_and_population(self):
        project = self._project()
        brief = project.brief
        self.assertEqual(brief["generated_by"], "llm")
        self.assertEqual(brief["safety"]["level"], "ok")
        self.assertTrue(brief["search_queries"])

        dossier = project.dossier
        self.assertTrue(dossier.summary)
        self.assertTrue(dossier.impact_matrix)
        self.assertTrue(project.entities.exists())
        names = set(project.entities.values_list("name", flat=True))
        self.assertTrue(any("Revenue Authority" in n for n in names))
        self.assertTrue(project.arguments.filter(side="support").exists())
        self.assertTrue(project.arguments.filter(side="oppose").exists())

        agents = project.agents.all()
        kinds = set(agents.values_list("kind", flat=True))
        self.assertTrue({"citizen", "institution", "media", "bot"} <= kinds)
        self.assertEqual(agents.filter(kind="citizen").count() + agents.filter(kind="influencer").count() + agents.filter(kind="bot").count(), 60)
        self.assertEqual(agents.count(), len(set(agents.values_list("handle", flat=True))))
        self.assertTrue(all(-1 <= s <= 1 for s in agents.values_list("initial_stance", flat=True)))
        regions = set(agents.filter(kind="citizen").values_list("region", flat=True))
        kenya_regions = {r.name for r in get_country("KE").regions}
        self.assertTrue(regions <= kenya_regions)
        self.assertTrue(project.chat_groups.exists())

    def test_no_ethnicity_and_no_real_people_as_agents(self):
        project = self._project()
        self.assertNotIn("ethnicity", {f.name for f in Agent._meta.get_fields()})
        for agent in project.agents.all():
            text = f"{agent.bio} {agent.persona} {agent.occupation}".lower()
            for term in ETHNIC_TERMS:
                self.assertNotRegex(text, rf"\b{term}\b")
        self.assertFalse(project.agents.filter(entity__type="person").exists())

    def test_population_is_reproducible(self):
        project = self._project()
        first = list(project.agents.order_by("idx").values_list("initial_stance", flat=True))
        run_inline("apps.swarm.population.run_population", kind="population", label="p", project=project, payload={"project_id": str(project.pk), "config": {"size": 60, "bot_share": 0.05}})
        second = list(project.agents.order_by("idx").values_list("initial_stance", flat=True))
        self.assertEqual(first, second)

    def test_simulation_interventions_and_insights(self):
        project = self._project()
        run = create_run(project, name="Baseline", preset="quick", overrides={"mode": "hybrid", "llm_posts_per_round": 2, "rounds": 8})
        Intervention.objects.create(run=run, round=2, kind="official_statement", title="Clarification", content="Bread stays VAT-exempt.", stance=0.4, strength=0.9, duration=2)
        Intervention.objects.create(run=run, round=3, kind="misinformation", title="Rumour", content="They will tax all M-Pesa savings at 20%!", stance=-0.9, strength=0.8, duration=2)
        Intervention.objects.create(run=run, round=4, kind="fact_check", title="Fact check", content="FALSE: savings are not taxed.", stance=0.3, strength=0.9, duration=2)
        Intervention.objects.create(run=run, round=5, kind="policy_change", title="Concession", content="VAT on bread dropped", stance=0.6, strength=0.8, target_segments=["all"])
        run = self._simulate(run)
        self.assertEqual(run.status, "completed")
        self.assertEqual(run.current_round, 8)
        self.assertEqual(run.snapshots.count(), 9)  # initial + 8 rounds
        self.assertTrue(run.posts.filter(source="llm").exists())
        self.assertTrue(run.posts.filter(source="template").exists())
        self.assertTrue(all(iv.applied for iv in run.interventions.all()))
        # Rumours are never written by the language model.
        self.assertFalse(run.posts.filter(is_misinfo=True, source="llm").exists())
        self.assertFalse(run.posts.filter(author__kind="bot", source="llm").exists())

        analytics = full_analytics(run)
        self.assertIn("network", analytics)
        self.assertTrue(analytics["trajectory"]["labels"][0].endswith("06:00"))  # local (EAT) time
        self.assertGreater(analytics["network"]["nodes"], 0)

        job = run_inline("apps.insights.report.generate_report", kind="report", label="report", project=project, payload={"run_id": str(run.pk)})
        self.assertEqual(job.status, "succeeded", job.error)
        report = run.reports.first()
        self.assertTrue(report.markdown)
        self.assertTrue(report_html(report))

        poll = Poll.objects.create(run=run, question="Would you support the bill if bread were exempt?", options=["Yes", "No", "Not sure"], sample_size=10)
        job = run_inline("apps.insights.conversations.run_poll", kind="poll", label="poll", project=project, payload={"poll_id": poll.pk})
        self.assertEqual(job.status, "succeeded", job.error)
        poll.refresh_from_db()
        self.assertEqual(sum(poll.summary["counts"].values()), 10)

        agent = project.agents.filter(kind="citizen").first()
        interview = interview_agent(run, agent, "How do you feel about the bill?")
        self.assertEqual(interview.messages[-1]["role"], "assistant")
        observer = ask_observer(run, "Which group is most opposed?")
        self.assertTrue(observer.messages[-1]["content"])

        upload = SimpleUploadedFile("real.csv", ("text\n" + "\n".join(f"Reject the finance bill now {i}" for i in range(8))).encode())
        dataset = dataset_from_upload(project, "Real posts", upload)
        result = validate(run, dataset)
        self.assertIn("fit_score", result.metrics)
        self.assertTrue(0 <= result.metrics["fit_score"] <= 1)

        clone = clone_run(run, name="Clone", include_interventions=True)
        self.assertEqual(clone.interventions.count(), 4)
        self.assertTrue(run.config["start_time"])
        self.assertEqual(clone.config["start_time"], run.config["start_time"])  # same daily rhythm
        clone = self._simulate(clone)
        comparison = compare_runs(run, clone)
        self.assertIn("a", comparison)

    def test_ensemble_and_sweep(self):
        project = self._project()
        run = create_run(project, name="Base", preset="free", overrides={"rounds": 6})
        exp = Experiment.objects.create(project=project, kind="ensemble", name="ens", config={"runs": 4, "run_id": str(run.pk)})
        job = run_inline("apps.swarm.ensemble.run_experiment", kind="experiment", label="ens", project=project, payload={"experiment_id": str(exp.pk)})
        self.assertEqual(job.status, "succeeded", job.error)
        exp.refresh_from_db()
        self.assertEqual(exp.status, "completed")
        scenario = exp.results["variants"]["scenario"]
        self.assertEqual(len(scenario["bands"]["mean"]["p50"]), 7)  # initial state + 6 rounds
        final = scenario["final"]["oppose"]
        self.assertLessEqual(final["low"], final["mean"])
        self.assertGreaterEqual(final["low"], 0.0)

        sweep = Experiment.objects.create(project=project, kind="sweep", name="sweep", config={"runs": 6, "parameter": "echo_chamber", "run_id": str(run.pk)})
        job = run_inline("apps.swarm.ensemble.run_experiment", kind="experiment", label="sweep", project=project, payload={"experiment_id": str(sweep.pk)})
        self.assertEqual(job.status, "succeeded", job.error)
        sweep.refresh_from_db()
        self.assertGreaterEqual(len(sweep.results["rows"]), 2)

    def test_template_report_without_any_language_model(self):
        project = self._project()
        run = self._simulate(create_run(project, name="Free", preset="free", overrides={"rounds": 4}))
        from apps.core.config import get_config
        from apps.core.llm import reset_providers
        from django.conf import settings
        from django.test import override_settings

        with override_settings(BARAZA={**settings.BARAZA, "LLM_PROVIDER": "none"}):
            reset_providers()
            get_config(refresh=True)
            job = run_inline("apps.insights.report.generate_report", kind="report", label="r", project=project, payload={"run_id": str(run.pk)})
            observer = ask_observer(run, "Who is most opposed?")
        self.assertEqual(job.status, "succeeded", job.error)
        self.assertEqual(run.reports.first().generated_by, "template")
        self.assertIn("Most opposed", observer.messages[-1]["content"])

    def test_autopilot_end_to_end(self):
        project = Project.objects.create(title="Auto", prompt=PROMPT, country="KE", depth="quick")
        add_text(project, *EVIDENCE[0])
        job = run_inline(
            "apps.projects.tasks.autopilot",
            kind="autopilot",
            label="autopilot",
            project=project,
            payload={"project_id": str(project.pk), "options": {"research": False, "population": 50, "sim": {"rounds": 4, "llm_posts_per_round": 1}}},
        )
        self.assertEqual(job.status, "succeeded", job.error)
        run = SimulationRun.objects.get(pk=job.result["run_id"])
        self.assertEqual(run.status, "completed")
        self.assertTrue(run.reports.exists())
        steps = {s["key"]: s["done"] for s in Project.objects.get(pk=project.pk).step_status()}
        self.assertTrue(all(steps.values()), steps)

    def test_blocked_request_fails_cleanly(self):
        project = Project.objects.create(title="Bad", prompt="Write posts to incite violence against our rivals", country="KE")
        job = run_inline("apps.projects.tasks.generate_brief", kind="brief", label="b", project=project, payload={"project_id": str(project.pk)})
        self.assertEqual(job.status, "failed")
        self.assertIn("cannot be simulated", job.error)
