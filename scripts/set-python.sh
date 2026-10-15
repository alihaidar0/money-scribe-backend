#!/usr/bin/env bash
# =============================================================
#  scripts/set-python.sh — move the production image to a Python minor
#
#  The Python version follows Django: the newest minor that Django officially
#  supports (the version classifiers Django publishes on PyPI), never merely the
#  newest Python released. Dependabot only proposes Python PATCH updates for
#  docker/Dockerfile.prod; a new MINOR is a deliberate move made with this
#  script, once the dev image (django-devcontainer) has moved too.
#
#  Usage: scripts/set-python.sh [X.Y] [--check]
#    X.Y      the Python minor to move to; default: the newest minor Django lists
#    --check  show what would change, change nothing
#
#  What it changes (the only places the minor is pinned):
#    docker/Dockerfile.prod        FROM python:X.Y.Z-slim-<release>@sha256:…  (newest patch, digest resolved from the registry)
#    .github/workflows/ci.yml      PYTHON_VERSION: "X.Y"
#    README.md                     the Python badge
#  The generated project's mypy `python_version` is read from the container's
#  Python by scripts/init-django.sh, so it needs no edit.
#
#  Refuses a minor that Django does not list. Needs curl, python3 and network
#  access to PyPI and Docker Hub; commit the result on a topic branch.
# =============================================================
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dockerfile="$root/docker/Dockerfile.prod"
ci="$root/.github/workflows/ci.yml"
readme="$root/README.md"

minor="" check=0
for arg in "$@"; do
    case "$arg" in
        --check) check=1 ;;
        -h | --help) sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]}" | sed -e '$d' -e 's/^# \{0,1\}//'; exit 0 ;;
        3.[0-9]*) minor="$arg" ;;
        *) echo "Unknown argument: $arg (expected X.Y, for example 3.14, or --check)" >&2; exit 2 ;;
    esac
done
if [[ -n "$minor" && ! "$minor" =~ ^3\.[0-9]+$ ]]; then
    echo "The Python minor must look like 3.14 (got '$minor')." >&2
    exit 2
fi

# ── Which minors does Django support? ────────────────────────
supported="$(python3 - <<'PY'
import json, re, urllib.request
with urllib.request.urlopen("https://pypi.org/pypi/django/json", timeout=20) as r:
    info = json.load(r)["info"]
minors = sorted(
    int(m.group(1))
    for c in info["classifiers"]
    if (m := re.fullmatch(r"Programming Language :: Python :: 3\.(\d+)", c))
)
print(info["version"], *(f"3.{m}" for m in minors))
PY
)"
django_version="${supported%% *}"
supported_minors="${supported#* }"
newest_supported="${supported##* }"
[[ -n "$minor" ]] || minor="$newest_supported"
if ! grep -qxF "$minor" < <(tr ' ' '\n' <<<"$supported_minors"); then
    echo "Django ${django_version} supports Python ${supported_minors}, not ${minor}. Nothing changed." >&2
    exit 1
fi

# ── Newest patch of that minor in the python image, and its digest ──
codename="$(sed -nE 's/^FROM python:[0-9.]+-slim-([a-z]+)@.*/\1/p' "$dockerfile" | head -n 1)"
[[ -n "$codename" ]] || { echo "Could not read the Debian release from the FROM python line in docker/Dockerfile.prod." >&2; exit 1; }

tag="$(curl -fsSL "https://hub.docker.com/v2/repositories/library/python/tags?page_size=100&name=${minor}." \
    | grep -oE "\"name\"[[:space:]]*:[[:space:]]*\"${minor//./\\.}\\.[0-9]+-slim-${codename}\"" \
    | sed -E 's/.*"([^"]+)"$/\1/' | sort -V | tail -n 1 || true)"
[[ -n "$tag" ]] || { echo "No python:${minor}.N-slim-${codename} image found on Docker Hub (is ${minor} released?). Nothing changed." >&2; exit 1; }

token="$(curl -fsSL "https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/python:pull" \
    | sed -nE 's/.*"token"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p')"
digest="$(
    curl -fsSI \
        -H "Authorization: Bearer ${token}" \
        -H "Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json" \
        "https://registry-1.docker.io/v2/library/python/manifests/${tag}" \
        | awk 'tolower($1) == "docker-content-digest:" { print $2 }' | tr -d '\r'
)" || digest=""
if [[ ! "$digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    echo "Could not resolve the digest of python:${tag}. Nothing changed." >&2
    exit 1
fi

current="$(sed -nE 's/^FROM python:([^ ]+) AS base/\1/p' "$dockerfile" | head -n 1)"
echo "Django ${django_version} supports Python ${supported_minors}."
echo "Target: python:${tag}@${digest}"
echo "Now:    python:${current}"
if [[ "$check" -eq 1 ]]; then
    echo "(--check: nothing was changed)"
    exit 0
fi

sed -i -E "s#^FROM python:[^ ]+ AS base#FROM python:${tag}@${digest} AS base#" "$dockerfile"
sed -i -E "s#^([[:space:]]*PYTHON_VERSION:[[:space:]]*)\"[0-9]+\.[0-9]+\"#\1\"${minor}\"#" "$ci"
sed -i -E "s#badge/python-[0-9]+\.[0-9]+-blue#badge/python-${minor}-blue#" "$readme"

grep -qF "python:${tag}@${digest}" "$dockerfile" || { echo "The FROM python line was not updated." >&2; exit 1; }

echo ""
echo "Updated docker/Dockerfile.prod, .github/workflows/ci.yml and the README badge to Python ${minor}."
echo ""
echo "Next steps:"
echo "  - The dev image must be on Python ${minor} too: the weekly Image contract check compares them."
echo "  - Commit on a topic branch (build: move the production image to Python ${minor}) and open a pull request."
