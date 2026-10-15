#!/usr/bin/env bash
# =============================================================
#  scripts/schema.sh — the project's API contract file
#
#  openapi/schema.yaml is generated from the code by drf-spectacular and
#  committed; nobody edits it by hand. This script is the one way to produce it
#  and to prove it is current.
#
#  Usage: scripts/schema.sh generate | check
#    generate  write openapi/schema.yaml from the code (creates openapi/)
#    check     regenerate to a temporary file and fail, with a clear message,
#              if it differs from the committed openapi/schema.yaml
#
#  Both modes validate the schema against the OpenAPI specification and fail on
#  any generation warning, so an endpoint drf-spectacular cannot describe
#  (an untyped serializer method, a missing @extend_schema) is noticed here,
#  not by the frontend. Needs no database. Run it through the project's
#  environment (it calls `uv run`); CI runs `check` on every pull request once
#  openapi/schema.yaml exists.
# =============================================================
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

schema="openapi/schema.yaml"
mode="${1:-}"

# Schema generation only imports the URLconf: settings that do not touch the
# network or a database are enough, so the same file comes out on every machine.
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings.testing}"
export SECRET_KEY="${SECRET_KEY:-schema-generation-only-not-a-real-secret-key-0123456789}"
export DATABASE_URL="${DATABASE_URL:-sqlite:////tmp/schema-generation.db}"

generate_to() { # output file
    uv run python manage.py spectacular --file "$1" --validate --fail-on-warn
}

case "$mode" in
    generate)
        mkdir -p "$(dirname "$schema")"
        generate_to "$schema"
        echo "Wrote ${schema}. Review the diff and commit it with the code that changed it."
        ;;
    check)
        if [[ ! -f "$schema" ]]; then
            echo "::error::${schema} does not exist. Run 'bash scripts/schema.sh generate' and commit it." >&2
            exit 1
        fi
        tmp="$(mktemp)"
        trap 'rm -f "$tmp"' EXIT
        generate_to "$tmp"
        if ! diff -u "$schema" "$tmp"; then
            echo "" >&2
            echo "::error file=${schema}::${schema} is out of date: it differs from what the code generates (diff above)." >&2
            echo "Run 'bash scripts/schema.sh generate' and commit the result." >&2
            exit 1
        fi
        echo "${schema} is current."
        ;;
    *)
        echo "Usage: scripts/schema.sh generate | check" >&2
        exit 2
        ;;
esac
