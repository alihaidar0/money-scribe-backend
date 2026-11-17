#!/usr/bin/env bash
# =============================================================
#  scripts/check-staging.sh — ask staging whether the web service, the database and Redis answer
#
#  Called by .github/workflows/staging-check.yml (docs/staging.md, ADR 0025). One call to GET /readyz wakes the
#  sleeping Render web service, wakes the Neon database (it suspends after 5 minutes without a query) and writes and
#  reads the Render Key Value instance, so none of the three goes weeks without a request.
#
#  The first call after a pause takes about a minute, so a refused or unanswered attempt is repeated.
#
#  Environment (all required unless marked):
#    STAGING_URL      https://<service>.onrender.com
#    READINESS_TOKEN  the token of /readyz, a SECRET (never printed, never in a process argument)
#    ATTEMPTS         optional, how many tries (6)
#    PAUSE_SECONDS    optional, pause between tries (20)
#
#  Exit codes: 0 all three answered; 1 anything else, with the reason.
# =============================================================

set -euo pipefail

: "${STAGING_URL:?STAGING_URL is required (the variable of the staging environment)}"
: "${READINESS_TOKEN:?READINESS_TOKEN is required (the secret of the staging environment)}"
ATTEMPTS="${ATTEMPTS:-6}"
PAUSE_SECONDS="${PAUSE_SECONDS:-20}"
summary="${GITHUB_STEP_SUMMARY:-/dev/null}"

fail() {
    echo "::error::$1"
    echo "❌ $1" >> "$summary"
    exit 1
}

staging_url="${STAGING_URL%/}"
[[ "$staging_url" == https://* ]] || fail "STAGING_URL must start with https://"

body="$(mktemp)"
trap 'rm -f "$body"' EXIT

echo "▶ Calling ${staging_url}/readyz (up to ${ATTEMPTS} tries)"
last="no answer"
for ((attempt = 1; attempt <= ATTEMPTS; attempt++)); do
    # The header goes in through a curl config on stdin: printf is a shell builtin, so the token is in no process list.
    status="$(printf 'header = "Authorization: Bearer %s"\n' "$READINESS_TOKEN" |
        curl -sS --config - --max-time 120 -o "$body" -w '%{http_code}' "${staging_url}/readyz" 2> /dev/null)" || status="000"
    case "$status" in
        200)
            echo "✅ the web service, the database and the cache answered (try ${attempt})"
            {
                echo "## ✅ Staging answers"
                echo ""
                echo "The web service, the database and the cache answered on try ${attempt} of ${ATTEMPTS}."
            } >> "$summary"
            exit 0
            ;;
        401) fail "The token is refused (401): the secret READINESS_TOKEN of the environment staging must equal the variable READINESS_TOKEN of the Render service." ;;
        404) fail "The check does not exist (404): set READINESS_TOKEN on the Render service (an empty value switches /readyz off) and check STAGING_URL." ;;
        405) fail "The check answered 405: the deployed version does not match this script." ;;
    esac
    reason="$(head -c 40 "$body" 2> /dev/null | tr -d '\r\n' || true)"
    if [[ "$status" == 503 && "$reason" =~ ^(database|cache)(,\ (database|cache))?$ ]]; then
        last="HTTP 503, not answering: ${reason}"
    else
        last="HTTP ${status}"
    fi
    echo "  try ${attempt}: ${last}"
    if ((attempt < ATTEMPTS)); then
        sleep "$PAUSE_SECONDS"
    fi
done

fail "Staging did not answer after ${ATTEMPTS} tries (last: ${last}). Read the service's logs on Render and the status of Neon and the Key Value instance."
