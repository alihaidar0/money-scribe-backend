#!/usr/bin/env bash
# =============================================================
#  check-image-contract.sh — does this template still match the dev image?
#
#  Compares the template with the published sources of django-devcontainer
#  (no image pull needed). Checked:
#    * the PostgreSQL major: the image's client must equal the compose server
#      and the CI service container (pg_dump refuses a newer server),
#    * the Python minor: image, production Dockerfile and CI must agree,
#    * the dev image the compose file pulls (and that its python-X.Y tag matches),
#    * every `pm*` shell alias the template mentions exists in the image,
#    * the uv version of the image vs the production Dockerfile (warning only:
#      Dependabot bumps the two separately).
#
#  Run from the repository root. IMAGE_REPO_RAW overrides the source URL.
# =============================================================
set -euo pipefail

base="${IMAGE_REPO_RAW:-https://raw.githubusercontent.com/alihaidar0/django-devcontainer/main}"
status=0
fail() { echo "::error::$*"; status=1; }
warn() { echo "::warning::$*"; }

dockerfile="$(curl -fsSL --retry 3 "${base}/docker/Dockerfile.dev")"
aliases_script="$(curl -fsSL --retry 3 "${base}/scripts/shell_setup.sh")"

# ── PostgreSQL ───────────────────────────────────────────────
image_pg="$(grep -oE '^ARG PG_MAJOR=[0-9]+' <<< "${dockerfile}" | head -1 | cut -d= -f2 || true)"
compose_pg="$(grep -oE '^[[:space:]]*image:[[:space:]]*postgres:[0-9]+' docker-compose.yml | head -1 | grep -oE '[0-9]+$' || true)"
ci_pg="$(grep -oE '^[[:space:]]*image:[[:space:]]*postgres:[0-9]+' .github/workflows/ci.yml | head -1 | grep -oE '[0-9]+$' || true)"
[ -n "${image_pg}" ] || fail "Could not read PG_MAJOR from the image's Dockerfile.dev."
[ "${compose_pg}" = "${image_pg}" ] \
  || fail "docker-compose.yml runs PostgreSQL ${compose_pg:-<none>} but the image's client is ${image_pg}."
[ "${ci_pg}" = "${image_pg}" ] \
  || fail "ci.yml runs PostgreSQL ${ci_pg:-<none>} but the image's client is ${image_pg}."

# ── Python ───────────────────────────────────────────────────
image_py="$(grep -oE '^FROM python:[0-9]+\.[0-9]+' <<< "${dockerfile}" | head -1 | cut -d: -f2 || true)"
prod_py="$(grep -oE '^FROM python:[0-9]+\.[0-9]+' docker/Dockerfile.prod | head -1 | cut -d: -f2 || true)"
ci_py="$(grep -oE 'PYTHON_VERSION:[[:space:]]*"[0-9]+\.[0-9]+' .github/workflows/ci.yml | head -1 | grep -oE '[0-9]+\.[0-9]+$' || true)"
[ -n "${image_py}" ] || fail "Could not read the Python version from the image's Dockerfile.dev."
[ "${prod_py}" = "${image_py}" ] \
  || fail "docker/Dockerfile.prod uses Python ${prod_py:-<none>} but the dev image runs ${image_py}."
[ "${ci_py}" = "${image_py}" ] \
  || fail "ci.yml PYTHON_VERSION is ${ci_py:-<none>} but the dev image runs ${image_py}."

# ── uv (informational) ───────────────────────────────────────
image_uv="$(grep -oE '^FROM ghcr.io/astral-sh/uv:[0-9]+\.[0-9]+\.[0-9]+' <<< "${dockerfile}" | head -1 | grep -oE '[0-9.]+$' || true)"
prod_uv="$(grep -oE '^FROM ghcr.io/astral-sh/uv:[0-9]+\.[0-9]+\.[0-9]+' docker/Dockerfile.prod | head -1 | grep -oE '[0-9.]+$' || true)"
if [ -n "${image_uv}" ] && [ -n "${prod_uv}" ] && [ "${image_uv}" != "${prod_uv}" ]; then
  warn "uv differs: dev image ${image_uv}, docker/Dockerfile.prod ${prod_uv} (a Dependabot pull request is probably pending)."
fi

# ── Image reference ──────────────────────────────────────────
grep -E '^[[:space:]]*image:' docker-compose.yml | grep -q 'alihaidar199527/django-devcontainer' \
  || fail "docker-compose.yml does not pull alihaidar199527/django-devcontainer."
# A python-X.Y[.Z.R] tag must name the Python line the image is on.
compose_line="$(grep -oE 'alihaidar199527/django-devcontainer:python-[0-9]+\.[0-9]+' docker-compose.yml | head -1 | grep -oE '[0-9]+\.[0-9]+$' || true)"
if [ -n "${compose_line}" ] && [ -n "${image_py}" ] && [ "${compose_line}" != "${image_py}" ]; then
  fail "docker-compose.yml pulls the python-${compose_line} line but the image is on Python ${image_py}."
fi

# ── Aliases the template mentions must exist in the image ────
image_aliases="$(grep -oE '^alias [A-Za-z0-9_]+' <<< "${aliases_script}" | cut -d' ' -f2 | sort -u)"
[ -n "${image_aliases}" ] || fail "No aliases found in the image's shell_setup.sh."
used_aliases="$(grep -ohE '\bpm[a-z]{0,2}\b' README.md scripts/welcome.sh | sort -u || true)"
while IFS= read -r name; do
  [ -z "${name}" ] && continue
  grep -qxF -- "${name}" <<< "${image_aliases}" \
    || fail "Alias '${name}' is used in the README or welcome.sh but not defined by the image."
done <<< "${used_aliases}"

if [ "${status}" -eq 0 ]; then
  echo "Template matches the django-devcontainer image contract (PostgreSQL ${image_pg}, Python ${image_py})."
fi
exit "${status}"
