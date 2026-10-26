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
    # --no-control-socket: the control socket lives under $HOME, and the image's user has no home directory.
    # The access log line: time, method, path without the query string (a search parameter is the person's own text),
    # status, size, duration and the request id. No client address (ADR 0009 keeps only truncated ones), no user agent.
    access_format='%(t)s "%(m)s %(U)s" %(s)s %(b)s %(M)sms %({x-request-id}o)s'
    # Gunicorn splits GUNICORN_CMD_ARGS like a shell (shlex), so the format goes in quoted (${var@Q}).
    default_args="--bind=0.0.0.0:${PORT:-8080} --threads=4 --timeout=120 --access-logfile=-"
    default_args+=" --access-logformat=${access_format@Q} --error-logfile=- --no-control-socket"
    export GUNICORN_CMD_ARGS="${GUNICORN_CMD_ARGS:-${default_args}}"
    echo "▶ Starting Gunicorn on :${PORT:-8080} (${WEB_CONCURRENCY:-2} workers)"
fi

exec "$@"
