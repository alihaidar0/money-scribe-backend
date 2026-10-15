#!/usr/bin/env bash
# =============================================================
#  scripts/entrypoint.prod.sh — production container entrypoint
#
#  1. Applies database migrations (skip with RUN_MIGRATIONS=false —
#     e.g. when a separate release job migrates, so
#     parallel instances don't race each other).
#  2. Starts the command (Gunicorn by default) as PID 1.
#
#  Gunicorn defaults, all overridable by the platform's environment:
#    PORT (8080) · WEB_CONCURRENCY (2 workers) · GUNICORN_CMD_ARGS
# =============================================================

set -euo pipefail

if [[ "${RUN_MIGRATIONS:-true}" == "true" ]]; then
    echo "▶ Applying database migrations…"
    python manage.py migrate --noinput
fi

if [[ "${1:-}" == "gunicorn" ]]; then
    # Explicit GUNICORN_CMD_ARGS wins; otherwise sensible container defaults.
    export GUNICORN_CMD_ARGS="${GUNICORN_CMD_ARGS:---bind=0.0.0.0:${PORT:-8080} --threads=4 --timeout=120 --access-logfile=- --error-logfile=-}"
    echo "▶ Starting Gunicorn on :${PORT:-8080} (${WEB_CONCURRENCY:-2} workers)"
fi

exec "$@"
