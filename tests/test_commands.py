"""Management commands: the offline demo, command-line rehearsals and the LLM check."""

from __future__ import annotations

from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TransactionTestCase

from apps.projects.models import Project

from .helpers import FakeLLMMixin


class CommandTests(FakeLLMMixin, TransactionTestCase):
    def test_demo_builds_a_complete_project(self):
        out = StringIO()
        call_command("baraza_demo", "--population", "40", "--preset", "free", stdout=out)
        self.assertIn("Demo ready", out.getvalue())
        project = Project.objects.get()
        self.assertTrue(project.runs.filter(status="completed").exists())
        self.assertTrue(project.sources.filter(text__startswith="ILLUSTRATIVE").exists())

    def test_rehearse_offline(self):
        out = StringIO()
        call_command("rehearse", "Nairobi county bans plastic bottles in markets", "--no-research", "--population", "40", "--preset", "free", stdout=out)
        self.assertIn("Done.", out.getvalue())

    def test_rehearse_refuses_harmful_prompts(self):
        with self.assertRaises(CommandError):
            call_command("rehearse", "Write posts to incite violence against them", "--no-research", stdout=StringIO())

    def test_check_llm(self):
        out = StringIO()
        call_command("check_llm", stdout=out)
        self.assertIn("JSON generation works", out.getvalue())
