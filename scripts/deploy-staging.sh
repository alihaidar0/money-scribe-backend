#!/usr/bin/env bash
# =============================================================
#  scripts/deploy-staging.sh — deploy one image to staging and wait for it
#
#  Called by .github/workflows/deploy-staging.yml (docs/staging.md, ADR 0011):
#
#    1. takes the digest of the image built for the commit (IMAGE_DIGEST, passed by
#       docker.yml), or finds it by the tag sha-<short> for a rollback by hand;
#    2. calls the Render deploy hook, pinned to that digest (imgURL);
#    3. waits until /healthz answers with the same commit in its X-Release
#       header, which proves the NEW build is the one that is live.
#
#  Environment (all required unless marked):
#    IMAGE_REPOSITORY        owner/repo, as GitHub names it (lower-cased here)
#    COMMIT                  the commit to deploy: 7 to 40 hex characters
#    IMAGE_DIGEST            optional, sha256:... of that build; empty = look it up
#    RENDER_DEPLOY_HOOK_URL  the service's deploy hook, a SECRET (never printed)
#    STAGING_URL             https://<service>.onrender.com
#    WAIT_SECONDS            optional, how long to wait for the release (900)
#    POLL_SECONDS            optional, pause between probes (15)
#
#  Exit codes: 0 deployed and confirmed; 1 anything else, with the reason.
# =============================================================

set -euo pipefail

: "${IMAGE_REPOSITORY:?IMAGE_REPOSITORY is required (owner/repo)}"
: "${COMMIT:?COMMIT is required}"
: "${RENDER_DEPLOY_HOOK_URL:?RENDER_DEPLOY_HOOK_URL is required (the secret of the staging environment)}"
: "${STAGING_URL:?STAGING_URL is required (the variable of the staging environment)}"
WAIT_SECONDS="${WAIT_SECONDS:-900}"
POLL_SECONDS="${POLL_SECONDS:-15}"
summary="${GITHUB_STEP_SUMMARY:-/dev/null}"

fail() {
    echo "::error::$1"
    echo "❌ $1" >> "$summary"
    exit 1
}

commit="${COMMIT,,}"
[[ "$commit" =~ ^[0-9a-f]{7,40}$ ]] || fail "COMMIT must be 7 to 40 hex characters."
staging_url="${STAGING_URL%/}"
[[ "$staging_url" == https://* ]] || fail "STAGING_URL must start with https://"

image="ghcr.io/${IMAGE_REPOSITORY,,}"
tag="sha-${commit:0:7}"

# ── 1. the digest of the image that was built for this commit ─────────────────
digest="${IMAGE_DIGEST:-}"
if [[ -z "$digest" ]]; then
    echo "▶ Resolving ${image}:${tag}"
    digest="$(docker buildx imagetools inspect "${image}:${tag}" --format '{{.Manifest.Digest}}' 2>/dev/null)" ||
        fail "No image ${image}:${tag}. Was it published? (repository variable PUBLISH_IMAGE=ghcr, a green Docker workflow for this commit, a package that Actions may read.)"
fi
[[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || fail "Unexpected digest '${digest}' for ${image}."
reference="${image}@${digest}"
echo "  digest ${digest}"

# ── 2. start the deploy, pinned to that digest ────────────────────────────────
encoded="$(jq -rn --arg value "$reference" '$value | @uri')"
separator='?'
[[ "$RENDER_DEPLOY_HOOK_URL" == *'?'* ]] && separator='&'
echo "▶ Calling the deploy hook (the URL is a secret and is not printed)"
body="$(mktemp)"
trap 'rm -f "$body"' EXIT
status="$(curl -sS --max-time 60 -X POST -o "$body" -w '%{http_code}' \
    "${RENDER_DEPLOY_HOOK_URL}${separator}imgURL=${encoded}")" || fail "The deploy hook could not be reached."
case "$status" in
    200) echo "  deploy started" ;;
    202) echo "  another deploy is running; this one starts after it" ;;
    400) fail "The hook refused the image (400): imgURL must match the service's image (${image}), in lower case." ;;
    401) fail "The hook key is wrong or old (401): copy the current Deploy Hook URL into the secret RENDER_DEPLOY_HOOK_URL." ;;
    404) fail "The hook does not find the service (404)." ;;
    409) fail "The service is suspended or deploys are blocked (409)." ;;
    *) fail "The deploy hook answered HTTP ${status}." ;;
esac

# ── 3. wait for the new build to answer ───────────────────────────────────────
echo "▶ Waiting up to ${WAIT_SECONDS}s for ${staging_url}/healthz to report ${commit:0:7}"
started="$SECONDS"
seen="(nothing yet)"
while ((SECONDS - started < WAIT_SECONDS)); do
    headers="$(curl -sS --max-time 30 -o /dev/null -D - "${staging_url}/healthz" 2>/dev/null || true)"
    release="$(tr -d '\r' <<<"$headers" | awk -F': *' 'tolower($1) == "x-release" { print tolower($2); exit }')"
    if [[ -n "$release" ]]; then
        seen="$release"
        if [[ "$release" == "$commit"* || "$commit" == "$release"* ]]; then
            elapsed=$((SECONDS - started))
            echo "✅ ${staging_url} runs ${release:0:7} (after ${elapsed}s)"
            {
                echo "## 🚀 Deployed to staging"
                echo ""
                echo "| Field | Value |"
                echo "|-------|-------|"
                echo "| **Address** | ${staging_url} |"
                echo "| **Commit** | \`${release}\` |"
                echo "| **Image** | \`${image}\` |"
                echo "| **Digest** | \`${digest}\` |"
                echo "| **Live after** | ${elapsed}s |"
            } >> "$summary"
            exit 0
        fi
    fi
    sleep "$POLL_SECONDS"
done

fail "Staging did not report ${commit:0:7} within ${WAIT_SECONDS}s (last X-Release: ${seen}). Read the service's logs on Render: a missing variable, a bad DATABASE_URL or users.E001 stops the new container."
