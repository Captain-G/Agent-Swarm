"""
Django settings for Baraza.

Everything is configured through environment variables (optionally loaded
from a `.env` file in the project root). Sensible, zero-cost defaults are
used for local use: SQLite, a local Ollama server running `llama3`, and
free data sources only.
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in env(name, default).split(",") if item.strip()]


# ---------------------------------------------------------------------------
# Storage locations
# ---------------------------------------------------------------------------
DATA_DIR = Path(env("BARAZA_DATA_DIR", str(BASE_DIR / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
(DATA_DIR / "logs").mkdir(exist_ok=True)


def _load_or_create_secret_key() -> str:
    """Use SECRET_KEY from the environment, or persist a generated one locally."""
    from_env = env("SECRET_KEY")
    if from_env:
        return from_env
    key_file = DATA_DIR / "secret_key.txt"
    if key_file.exists():
        return key_file.read_text().strip()
    key = secrets.token_urlsafe(50)
    key_file.write_text(key)
    return key


SECRET_KEY = _load_or_create_secret_key()
DEBUG = env_bool("DEBUG", False)
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1,0.0.0.0,[::1]")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS", "")

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "apps.core",
    "apps.projects",
    "apps.research",
    "apps.knowledge",
    "apps.swarm",
    "apps.insights",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.OptionalLoginRequiredMiddleware",
    "apps.core.middleware.ProjectTimezoneMiddleware",
]

ROOT_URLCONF = "baraza.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.baraza",
            ],
        },
    },
]

WSGI_APPLICATION = "baraza.wsgi.application"
ASGI_APPLICATION = "baraza.asgi.application"

# ---------------------------------------------------------------------------
# Database: SQLite by default (zero setup). Set DATABASE_URL for PostgreSQL.
# ---------------------------------------------------------------------------


def _database_from_url(url: str) -> dict:
    parsed = urlparse(url)
    scheme = parsed.scheme.split("+")[0]
    if scheme in {"postgres", "postgresql", "pgsql"}:
        options = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote(parsed.path.lstrip("/")),
            "USER": unquote(parsed.username or ""),
            "PASSWORD": unquote(parsed.password or ""),
            "HOST": parsed.hostname or "",
            "PORT": str(parsed.port or ""),
            "CONN_MAX_AGE": 60,
            "OPTIONS": options,
        }
    if scheme == "sqlite":
        path = unquote(parsed.path)
        return _sqlite(Path(path) if path else DATA_DIR / "baraza.sqlite3")
    raise ValueError(f"Unsupported DATABASE_URL scheme: {parsed.scheme}")


def _sqlite(path: Path) -> dict:
    return {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": path,
        "OPTIONS": {
            # WAL lets the UI read while background simulations write.
            "init_command": (
                "PRAGMA journal_mode=WAL;"
                "PRAGMA synchronous=NORMAL;"
                "PRAGMA busy_timeout=30000;"
                "PRAGMA foreign_keys=ON;"
            ),
            "transaction_mode": "IMMEDIATE",
            "timeout": 30,
        },
        # A file (not in-memory) test database so background threads share it.
        "TEST": {"NAME": str(path.parent / "test_baraza.sqlite3")},
    }


DATABASES = {
    "default": _database_from_url(env("DATABASE_URL"))
    if env("DATABASE_URL")
    else _sqlite(DATA_DIR / "baraza.sqlite3")
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "core:home"
LOGOUT_REDIRECT_URL = "login"

# ---------------------------------------------------------------------------
# Internationalisation
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en"
TIME_ZONE = env("TIME_ZONE", "Africa/Nairobi")
USE_I18N = False
USE_TZ = True

# ---------------------------------------------------------------------------
# Static & media files (served by WhiteNoise, no separate web server needed)
# ---------------------------------------------------------------------------
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = DATA_DIR / "staticfiles"
WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = DEBUG
MEDIA_URL = "media/"
MEDIA_ROOT = DATA_DIR / "uploads"
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 25 * 1024 * 1024

# ---------------------------------------------------------------------------
# Security hardening that is safe for local use and production alike
# ---------------------------------------------------------------------------
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_HTTPONLY = True
SECURE_REFERRER_POLICY = "same-origin"
if env_bool("SECURE_COOKIES", False):
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
TESTING = len(sys.argv) > 1 and sys.argv[1] == "test"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "%(asctime)s %(levelname)s [%(name)s] %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(DATA_DIR / "logs" / "baraza.log"),
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 3,
            "formatter": "simple",
            "encoding": "utf-8",
        },
    },
    "loggers": {
        "baraza": {"handlers": ["console"] if TESTING else ["console", "file"], "level": "CRITICAL" if TESTING else env("LOG_LEVEL", "INFO")},
        "django": {"handlers": ["console"], "level": "WARNING"},
        "httpx": {"handlers": ["console"], "level": "WARNING"},
        "trafilatura": {"handlers": ["console"], "level": "ERROR"},
    },
}

# ---------------------------------------------------------------------------
# Baraza configuration (defaults; most can be changed in the Settings page)
# ---------------------------------------------------------------------------
BARAZA = {
    # Language model. "ollama" (free, local) | "openai" (any OpenAI-compatible
    # API such as Groq, OpenRouter, Together, LM Studio, vLLM, OpenAI)
    # | "fake" (deterministic offline stub for demos/tests) | "none".
    "LLM_PROVIDER": env("LLM_PROVIDER", "ollama"),
    "OLLAMA_BASE_URL": env("OLLAMA_BASE_URL", "http://localhost:11434"),
    "OLLAMA_MODEL": env("OLLAMA_MODEL", "llama3"),
    "OLLAMA_FAST_MODEL": env("OLLAMA_FAST_MODEL", ""),
    "OLLAMA_EMBED_MODEL": env("OLLAMA_EMBED_MODEL", "nomic-embed-text"),
    "OLLAMA_NUM_CTX": env_int("OLLAMA_NUM_CTX", 8192),
    "OPENAI_BASE_URL": env("OPENAI_BASE_URL", "https://api.groq.com/openai/v1"),
    "OPENAI_API_KEY": env("OPENAI_API_KEY", ""),
    "OPENAI_MODEL": env("OPENAI_MODEL", "llama-3.3-70b-versatile"),
    "OPENAI_FAST_MODEL": env("OPENAI_FAST_MODEL", ""),
    "OPENAI_EMBED_MODEL": env("OPENAI_EMBED_MODEL", ""),
    # Route only the "smart" tier (brief, dossier, report) to the OpenAI-compatible
    # provider while agents keep using local Ollama. Cheapest high-quality mix.
    "HYBRID_SMART_TIER": env_bool("HYBRID_SMART_TIER", False),
    "LLM_CONCURRENCY": env_int("LLM_CONCURRENCY", 2),
    "LLM_TIMEOUT": env_int("LLM_TIMEOUT", 240),
    "LLM_CACHE": env_bool("LLM_CACHE", True),
    "LLM_COST_PER_1M_INPUT_USD": env_float("LLM_COST_PER_1M_INPUT_USD", 0.0),
    "LLM_COST_PER_1M_OUTPUT_USD": env_float("LLM_COST_PER_1M_OUTPUT_USD", 0.0),
    # Background jobs: "thread" (default, zero setup) or "worker"
    # (run `python manage.py run_worker` in a second terminal).
    "JOB_RUNNER": env("JOB_RUNNER", "thread"),
    "MAX_CONCURRENT_JOBS": env_int("MAX_CONCURRENT_JOBS", 2),
    "DEFAULT_COUNTRY": env("DEFAULT_COUNTRY", "KE"),
    "REQUIRE_LOGIN": env_bool("REQUIRE_LOGIN", False),
    # Research / scraping
    "HTTP_USER_AGENT": env(
        "HTTP_USER_AGENT",
        "BarazaResearchBot/1.0 (+https://github.com/captain-g/agent-swarm; public-interest research)",
    ),
    "RESPECT_ROBOTS_TXT": env_bool("RESPECT_ROBOTS_TXT", True),
    "HTTP_TIMEOUT": env_int("HTTP_TIMEOUT", 20),
    "ANONYMIZE_SOCIAL_AUTHORS": env_bool("ANONYMIZE_SOCIAL_AUTHORS", True),
    # Optional data-source credentials (all optional; free tiers noted in docs)
    "YOUTUBE_API_KEY": env("YOUTUBE_API_KEY"),
    "REDDIT_CLIENT_ID": env("REDDIT_CLIENT_ID"),
    "REDDIT_CLIENT_SECRET": env("REDDIT_CLIENT_SECRET"),
    "SEARXNG_URL": env("SEARXNG_URL"),
    "TELEGRAM_CHANNELS": env("TELEGRAM_CHANNELS"),
    "CUSTOM_RSS_FEEDS": env("CUSTOM_RSS_FEEDS"),
    "MASTODON_INSTANCE": env("MASTODON_INSTANCE", "https://mastodon.social"),
    "BLUESKY_HANDLE": env("BLUESKY_HANDLE"),
    "BLUESKY_APP_PASSWORD": env("BLUESKY_APP_PASSWORD"),
    "JINA_API_KEY": env("JINA_API_KEY"),
    "TAVILY_API_KEY": env("TAVILY_API_KEY"),
    "BRAVE_API_KEY": env("BRAVE_API_KEY"),
    "SERPER_API_KEY": env("SERPER_API_KEY"),
    "APIFY_TOKEN": env("APIFY_TOKEN"),
    "TWITTERAPI_IO_KEY": env("TWITTERAPI_IO_KEY"),
    "X_BEARER_TOKEN": env("X_BEARER_TOKEN"),
}
