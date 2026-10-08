"""Shared test helpers: fake LLM configuration, mock HTTP transports, fixtures."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import httpx
from django.conf import settings
from django.test import override_settings

from apps.core.config import get_config
from apps.core.llm import reset_providers

FIXTURES = Path(__file__).parent / "fixtures"
_MEDIA = tempfile.mkdtemp(prefix="baraza-test-media-")


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeLLMMixin:
    """Run with the deterministic offline language model and a temp media dir."""

    provider = "fake"
    extra_config: dict = {}

    def setUp(self):
        super().setUp()
        conf = {**settings.BARAZA, "LLM_PROVIDER": self.provider, "LLM_CONCURRENCY": 2, **self.extra_config}
        self._override = override_settings(BARAZA=conf, MEDIA_ROOT=_MEDIA)
        self._override.enable()
        reset_providers()
        get_config(refresh=True)

    def tearDown(self):
        self._override.disable()
        reset_providers()
        get_config(refresh=True)
        super().tearDown()


def mock_client(routes: dict, default_status: int = 404) -> httpx.Client:
    """httpx client whose responses come from {substring-of-url: response} routes.

    A route value may be a dict/list (JSON), a string (text), an httpx.Response or
    a callable(request) -> httpx.Response.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for key, value in routes.items():
            if key in url:
                if callable(value):
                    return value(request)
                if isinstance(value, httpx.Response):
                    return value
                if isinstance(value, (dict, list)):
                    return httpx.Response(200, json=value)
                return httpx.Response(200, text=value, headers={"content-type": "text/html; charset=utf-8" if value.lstrip().startswith("<") else "text/plain"})
        return httpx.Response(default_status, text="not found")

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def cleanup_media():
    shutil.rmtree(_MEDIA, ignore_errors=True)


def json_dump(data) -> str:
    return json.dumps(data)
