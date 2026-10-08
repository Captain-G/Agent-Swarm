"""Template context available on every page."""

from baraza import __version__

from .config import cfg


def baraza(request):
    return {
        "APP_NAME": "Baraza",
        "APP_TAGLINE": "Rehearse public sentiment before it happens",
        "APP_VERSION": __version__,
        "LLM_PROVIDER": cfg("LLM_PROVIDER", "ollama"),
        "REQUIRE_LOGIN": cfg("REQUIRE_LOGIN", False),
    }
