#!/usr/bin/env bash
# =============================================================
#  scripts/init-django.sh — scaffold the Django project layout
#
#  Run ONCE, after `uv init` and the `uv add` steps (see the welcome
#  banner / README). Creates the `config` project wired the way the rest
#  of this template expects:
#
#    config/settings/base.py         startproject settings + env overrides
#    config/settings/development.py  DEBUG on        (docker-compose default)
#    config/settings/production.py   DEBUG off, secure cookies (Dockerfile.prod)
#    config/settings/testing.py      fast hashing    (pytest / CI)
#    config/celery.py                Celery app      (`celery -A config`)
#    config/health.py                GET /healthz for platform probes
#    config/api_errors.py            the shared 400 error schema of the API contract
#    users/                         custom user model (AUTH_USER_MODEL), from the first migration
#    pyproject.toml                  ruff · mypy (django-stubs) · pytest · coverage
#
#  Settings read the environment that docker-compose.yml / .env provide:
#  SECRET_KEY, DEBUG, ALLOWED_HOSTS, DATABASE_URL, CELERY_BROKER_URL, …
#
#  CI runs this same script to build and smoke-test the production image
#  from a fresh template, so what the docs describe is what gets tested.
# =============================================================

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -f manage.py ]]; then
    echo "❌ manage.py already exists — the Django project is initialised."
    exit 1
fi
if [[ ! -f pyproject.toml ]]; then
    echo "❌ No pyproject.toml — run 'uv init --bare' and the 'uv add' steps first."
    exit 1
fi
for pkg in django dj-database-url celery whitenoise; do
    if ! uv pip show --quiet "$pkg" >/dev/null 2>&1; then
        echo "❌ '$pkg' is not installed in the project — run the 'uv add' steps first."
        exit 1
    fi
done
# The generated settings use Django 6's Content Security Policy middleware. Django
# 6.1 supports Python 3.12–3.14, so it fits the dev image's Python line.
django_major="$(uv pip show django 2>/dev/null | sed -nE 's/^Version: ([0-9]+)\..*/\1/p')"
if [[ -z "$django_major" || "$django_major" -lt 6 ]]; then
    echo "❌ Django 6.0 or newer is required (Content Security Policy) — found ${django_major:-none}. Run: uv add 'django>=6.1'"
    exit 1
fi

echo "▶ django-admin startproject config ."
uv run django-admin startproject config .

echo "▶ Splitting settings into config/settings/"
mkdir -p config/settings
mv config/settings.py config/settings/base.py
: > config/settings/__init__.py
# base.py moved one level deeper — keep BASE_DIR pointing at the repo root.
sed -i 's/Path(__file__).resolve().parent.parent$/Path(__file__).resolve().parent.parent.parent/' config/settings/base.py
# Imports for the env-driven block below belong at the top of the module.
sed -i 's/^from pathlib import Path$/import os\nfrom pathlib import Path\n\nimport dj_database_url/' config/settings/base.py

cat >> config/settings/base.py << 'PY'


# ── Environment-driven settings (scripts/init-django.sh) ─────────────────
# Values come from docker-compose.yml / .env in development and from the
# platform's environment in production.
SECRET_KEY = os.environ.get("SECRET_KEY", SECRET_KEY)
DEBUG = os.environ.get("DEBUG", "False").lower() in {"1", "true", "yes", "on"}
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]
# Browser origins allowed to call the API (django-cors-headers). Empty by default: a project
# lists the origins of its own web client; native mobile apps do not need CORS.
CORS_ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()]

DATABASES = {
    "default": dict(
        dj_database_url.config(
            default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
            conn_max_age=600,
            conn_health_checks=True,
        )
    ),
}

INSTALLED_APPS += [
    "corsheaders",
    "rest_framework",
    "drf_spectacular",
    "drf_standardized_errors",
    "django_celery_beat",
    "users",
]

# A custom user model, in place from the first migration (users/models.py).
# Django's own docs call swapping AUTH_USER_MODEL in later "a substantial
# task" — a from-scratch migration history plus a data migration for every
# existing user — so this exists even though it changes nothing yet.
AUTH_USER_MODEL = "users.User"

# ── API contract (drf-spectacular) ───────────────────────────────────────
# openapi/schema.yaml is generated from the code (bash scripts/schema.sh generate)
# and committed; it is the contract with every API client. The schema is NOT
# served by the app: no SpectacularAPIView is wired into the URLconf, so
# nothing is exposed in production unless the project adds it on purpose.
#
# One error body for every API error (drf-standardized-errors): {"type", "errors": [{"code",
# "detail", "attr"}]}. Its AutoSchema documents the error responses of each operation, so
# generated clients get a typed error model. Only errors raised inside DRF views are covered;
# Django's own pages (a disallowed host, a 404 outside the API) keep their normal responses.
REST_FRAMEWORK: dict[str, object] = {
    "DEFAULT_SCHEMA_CLASS": "drf_standardized_errors.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "drf_standardized_errors.handler.exception_handler",
    # Without this every operation would document a 404 for the ?format= query parameter.
    "URL_FORMAT_OVERRIDE": None,
}
DRF_STANDARDIZED_ERRORS = {
    # Document only the errors a client acts on; a project that adds a code here also adds
    # its enum to ENUM_NAME_OVERRIDES below (ErrorCode<status>Enum, e.g. 500 → ServerErrorEnum).
    "ALLOWED_ERROR_STATUS_CODES": ["400", "401", "403", "404", "429"],
    # One shared 400 schema. The package's default documents every field's error codes per
    # operation, which makes generated clients grow by dozens of classes per endpoint.
    "ERROR_SCHEMAS": {"400": "config.api_errors.Error400ResponseSerializer"},
}
_ERROR_ENUMS = "drf_standardized_errors.openapi_serializers"
SPECTACULAR_SETTINGS = {
    "TITLE": "API",  # the project's own name
    "DESCRIPTION": "The HTTP API of this project, generated from its code.",
    "VERSION": "0.1.0",  # the API contract version, not the app's release
    "SERVE_INCLUDE_SCHEMA": False,
    # Separate request and response components: what client generators expect.
    "COMPONENT_SPLIT_REQUEST": True,
    # Keep /api/v1/ out of operation ids and tags (path versioning).
    "SCHEMA_PATH_PREFIX": r"/api/v[0-9]",
    # Name the error-code enums once instead of once per operation (keeps the schema free of
    # drf-spectacular warnings, which fail scripts/schema.sh).
    "ENUM_NAME_OVERRIDES": {
        "ClientErrorEnum": f"{_ERROR_ENUMS}.ClientErrorEnum.choices",
        "ErrorCode401Enum": f"{_ERROR_ENUMS}.ErrorCode401Enum.choices",
        "ErrorCode403Enum": f"{_ERROR_ENUMS}.ErrorCode403Enum.choices",
        "ErrorCode404Enum": f"{_ERROR_ENUMS}.ErrorCode404Enum.choices",
        "ErrorCode429Enum": f"{_ERROR_ENUMS}.ErrorCode429Enum.choices",
    },
    # Replaces drf-spectacular's own enum hook (the package's hook calls the same logic).
    "POSTPROCESSING_HOOKS": ["drf_standardized_errors.openapi_hooks.postprocess_schema_enums"],
}

STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_ROOT = BASE_DIR / "media"

# WhiteNoise serves the collected static files straight from Gunicorn —
# no separate web server or bucket needed for static assets.
MIDDLEWARE.insert(
    MIDDLEWARE.index("django.middleware.security.SecurityMiddleware") + 1,
    "whitenoise.middleware.WhiteNoiseMiddleware",
)
# CORS headers must be added before any middleware that can build a response (security redirects,
# WhiteNoise, CommonMiddleware), so CorsMiddleware goes in front of SecurityMiddleware.
MIDDLEWARE.insert(
    MIDDLEWARE.index("django.middleware.security.SecurityMiddleware"),
    "corsheaders.middleware.CorsMiddleware",
)
# /healthz answers before host validation and HTTPS redirects (config/health.py).
MIDDLEWARE.insert(0, "config.health.HealthCheckMiddleware")
# Django 6 Content Security Policy — the policy itself is set per environment
# (report-only in production.py until it has been tuned for the project).
MIDDLEWARE.append("django.middleware.csp.ContentSecurityPolicyMiddleware")
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
MEDIA_URL = "media/"

EMAIL_HOST = os.environ.get("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "25"))

# Celery (config/celery.py reads every CELERY_* setting)
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/1")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/2")
CELERY_TIMEZONE = TIME_ZONE

# Logs go to stdout — the container platform collects them.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {"format": "{asctime} {levelname} {name}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "plain"},
    },
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
    "loggers": {
        "django": {"level": os.environ.get("DJANGO_LOG_LEVEL", "INFO"), "propagate": True},
    },
}
PY

cat > config/settings/development.py << 'PY'
from .base import *

DEBUG = True
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"  # Mailpit
PY

cat > config/settings/production.py << 'PY'
import os

from django.utils.csp import CSP

from .base import *

DEBUG = False
SECRET_KEY = os.environ["SECRET_KEY"]  # required — fail fast if missing

# Behind a TLS-terminating proxy / load balancer
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = os.environ.get("SECURE_SSL_REDIRECT", "True").lower() in {"1", "true", "yes", "on"}
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "0"))  # raise once HTTPS is confirmed
SECURE_CONTENT_TYPE_NOSNIFF = True

# Content Security Policy — starts in report-only mode (violations are reported
# to the browser console, nothing is blocked). Tune it for the project's assets,
# then switch the setting name to SECURE_CSP to enforce it.
SECURE_CSP_REPORT_ONLY = {
    "default-src": [CSP.SELF],
    "img-src": [CSP.SELF, "data:"],
    "object-src": [CSP.NONE],
    "base-uri": [CSP.SELF],
    "frame-ancestors": [CSP.NONE],
}
PY

cat > config/settings/testing.py << 'PY'
from .base import *

DEBUG = False
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # fast tests only
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
CELERY_TASK_ALWAYS_EAGER = True
# No collectstatic in tests: plain storage instead of the hashed manifest.
STORAGES = {
    **STORAGES,
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
PY

echo "▶ Pointing manage.py / wsgi.py / asgi.py at the split settings"
# startproject quotes the module with ' (Django ≥ 6) or " — match either.
sed -i -E "s/(['\"])config\.settings(['\"])/\1config.settings.development\2/" manage.py
sed -i -E "s/(['\"])config\.settings(['\"])/\1config.settings.production\2/" config/wsgi.py config/asgi.py
sed -i 's/^def main():$/def main() -> None:/' manage.py
if ! grep -q "config.settings.development" manage.py; then
    echo "❌ Could not point manage.py at config.settings.development — check its DJANGO_SETTINGS_MODULE line."
    exit 1
fi

echo "▶ Scaffolding a custom user model (users/) — see AUTH_USER_MODEL in settings"
mkdir -p users/migrations
: > users/__init__.py
: > users/migrations/__init__.py

cat > users/apps.py << 'PY'
from django.apps import AppConfig


class UsersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "users"
PY

cat > users/models.py << 'PY'
from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """The project's user model.

    Swapping ``AUTH_USER_MODEL`` after the first migration means recreating
    every migration that touches it and writing a data migration for existing
    users, so this exists from the start even though it adds nothing yet —
    add project-specific fields here.
    """
PY

cat > users/admin.py << 'PY'
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User

admin.site.register(User, UserAdmin)
PY

echo "▶ Generating the initial migration for users"
uv run python manage.py makemigrations users

echo "▶ Wiring Celery (config/celery.py)"
cat > config/celery.py << 'PY'
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

app = Celery("config")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
PY

cat > config/__init__.py << 'PY'
from .celery import app as celery_app

__all__ = ("celery_app",)
PY

echo "▶ Adding the /healthz endpoint (config/health.py)"
cat > config/health.py << 'PY'
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse


class HealthCheckMiddleware:
    """Answer ``GET /healthz`` with 200 before any other middleware runs.

    Platforms (orchestrators, load balancers) probe the container by IP
    or an internal hostname over plain HTTP; running first means those probes are
    not rejected by ALLOWED_HOSTS or redirected to HTTPS. It checks that the
    process serves requests (liveness) — add a readiness view for dependencies.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.path == "/healthz":
            return HttpResponse("ok", content_type="text/plain")
        return self.get_response(request)
PY

echo "▶ Adding the shared API error schema (config/api_errors.py)"
cat > config/api_errors.py << 'PY'
"""The one error body of the API, as documented in the OpenAPI contract.

drf-standardized-errors produces the bodies at runtime; DRF_STANDARDIZED_ERRORS["ERROR_SCHEMAS"]
points here so every operation shares ONE 400 schema instead of a generated one per endpoint and
field. ``attr`` is the field the error belongs to (dotted for nested data) or null.
"""

from drf_standardized_errors.openapi_serializers import ClientErrorEnum, ValidationErrorEnum
from rest_framework import serializers


class Error400Serializer(serializers.Serializer):  # type: ignore[misc]
    code = serializers.CharField()
    detail = serializers.CharField()
    attr = serializers.CharField(allow_null=True)


class Error400ResponseSerializer(serializers.Serializer):  # type: ignore[misc]
    """``validation_error``: invalid input. ``client_error``: an unreadable request body."""

    type = serializers.ChoiceField(choices=[*ValidationErrorEnum.choices, *ClientErrorEnum.choices])
    errors = Error400Serializer(many=True)
PY

echo "▶ Configuring ruff, mypy (django-stubs), pytest-django and coverage in pyproject.toml"
if ! grep -q '^\[tool.ruff\]' pyproject.toml; then
    cat >> pyproject.toml << 'TOML'

[tool.ruff]
target-version = "py314"
extend-exclude = ["**/migrations/**"]

[tool.ruff.lint]
# pycodestyle, pyflakes, isort, bugbear, pyupgrade, comprehensions, simplify,
# flake8-django, bandit, pathlib, ruff-specific.
select = ["E", "W", "F", "I", "B", "UP", "C4", "SIM", "DJ", "S", "PTH", "RUF"]
ignore = ["E501"]  # line length is the formatter's job

[tool.ruff.lint.per-file-ignores]
"config/settings/*.py" = ["F403", "F405", "S105"]  # star imports; startproject's dev-only key
"**/tests/**" = ["S101", "S106"]                  # assert, test passwords
"**/test_*.py" = ["S101", "S106"]

[tool.mypy]
# No python_version: mypy checks against the interpreter it runs in (the project's own),
# which follows the newest Python minor Django supports; nothing to keep in sync.
strict = true
plugins = ["mypy_django_plugin.main"]
exclude = ['^\.venv/', '/migrations/']

# django-stubs resolves the whole app registry for AUTH_USER_MODEL (users.User);
# django-celery-beat and Django REST framework ship no type stubs / py.typed marker, so
# they're untyped either way.
[[tool.mypy.overrides]]
module = ["django_celery_beat.*", "rest_framework.*"]
ignore_missing_imports = true

[tool.django-stubs]
django_settings_module = "config.settings.development"

[tool.pytest.ini_options]
DJANGO_SETTINGS_MODULE = "config.settings.testing"
addopts = "--strict-markers"

[tool.coverage.run]
branch = true
source = ["."]
omit = [".venv/*", "*/migrations/*", "*/tests/*", "manage.py", "config/asgi.py", "config/wsgi.py"]

[tool.coverage.report]
show_missing = true
skip_covered = true
TOML
fi

echo "▶ Formatting the generated code (ruff, same style as the pre-commit hook)"
# startproject writes single quotes; format now so the first commit is clean.
# Prefer the project's pinned ruff (uv.lock); fall back to a one-off uvx run.
if uv run --quiet ruff --version >/dev/null 2>&1; then ruff_cmd=(uv run --quiet ruff); else ruff_cmd=(uvx ruff); fi
"${ruff_cmd[@]}" format --quiet manage.py config users
"${ruff_cmd[@]}" check --quiet --fix manage.py config users

# Python dependencies now exist, so Dependabot's `uv` ecosystem can run: the block
# is commented in the template (without pyproject.toml the job would fail weekly).
# Uncomment it from its marker line to the end of the file.
if [[ -f .github/dependabot.yml ]] && grep -q '^  # - package-ecosystem: "uv"' .github/dependabot.yml; then
    echo "▶ Enabling Dependabot for Python dependencies (.github/dependabot.yml)"
    sed -i '/^  # - package-ecosystem: "uv"/,$ s/^  # /  /' .github/dependabot.yml
fi

echo ""
echo "✅ Django project initialised."
echo "   Next: pre-commit install · python manage.py migrate · python manage.py runserver 0.0.0.0:8000"
echo "   Health check: GET /healthz · settings: config/settings/ · tool config: pyproject.toml"
echo "   Custom user model: users/models.py (AUTH_USER_MODEL = \"users.User\")"
echo "   Then freeze the toolchain (bash scripts/pin-image.sh) and write your README (bash scripts/init-readme.sh)."
