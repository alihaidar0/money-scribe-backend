#!/usr/bin/env bash
# =============================================================
#  scripts/start-beat.sh — command of the celery-beat service in
#  docker-compose.yml
#
#  Starts Celery Beat once the project can run it:
#    0. DATABASE_URL points at the compose db service. Beat schedules jobs on
#       its own, so it never starts against an external database (for example
#       the Neon staging database) unless BEAT_ON_EXTERNAL_DATABASE=true.
#       Otherwise the container stays up and idle with a one-line reason.
#    1. manage.py and the project venv exist (uv sync has run),
#    2. every migration is applied (django-celery-beat's DatabaseScheduler
#       reads its own tables and crashes without them).
#
#  While it waits, the container stays up and the log says what is
#  missing, once per change. The fix is made in the dev container
#  (for example `pm migrate`); Beat starts by itself within seconds.
#  `docker compose logs celery-beat` shows the current reason.
# =============================================================

set -euo pipefail

cd /workspace

last=""
say() {
    if [[ "$1" != "$last" ]]; then
        printf '%s\n' "$1"
        last="$1"
    fi
}

# Host of DATABASE_URL, without credentials, port, path or query (an empty host is a unix socket).
rest="${DATABASE_URL:-}"
rest="${rest#*://}"
rest="${rest##*@}"
db_host="${rest%%[/?:]*}"
case "$db_host" in
    "" | db | localhost | 127.0.0.1) ;;
    *)
        if [[ "${BEAT_ON_EXTERNAL_DATABASE:-false}" != "true" ]]; then
            echo "⏸  Celery Beat is not started: DATABASE_URL points to an external database (${db_host})."
            echo "   Beat schedules jobs on its own. To run it there anyway, set BEAT_ON_EXTERNAL_DATABASE=true in the"
            echo "   git-ignored env file and run 'docker compose restart celery-beat'."
            exec sleep infinity
        fi
        ;;
esac

until [[ -f manage.py && -x .venv/bin/celery ]]; do
    say "⏳ Celery Beat waits for manage.py and the project venv (uv sync)…"
    sleep 5
done

# `migrate --check` exits 1 without output while migrations are unapplied; any
# output means it failed for another reason (settings, database), which is shown.
until out=$(.venv/bin/python manage.py migrate --check 2>&1); do
    if [[ -z "$out" ]]; then
        say "⏳ Celery Beat waits for the migrations: run 'pm migrate' in the dev container."
    else
        say "⚠️  Celery Beat cannot check the migrations. Fix it in the dev container; Beat retries every 5 seconds:
$out"
    fi
    sleep 5
done

exec .venv/bin/celery -A config beat -l INFO --scheduler django_celery_beat.schedulers:DatabaseScheduler
