#!/usr/bin/env bash
# =============================================================
#  scripts/init-readme.sh — replace the template guide with YOUR README
#
#  The template's README.md describes the template. This writes a README for
#  THIS project instead: name, description, badges, the stack versions it
#  really uses, how to run it, and which template release it started from.
#  Run it once, after the quickstart (and scripts/pin-image.sh).
#
#  Usage: scripts/init-readme.sh [--description "text"] [--name "Project Name"]
#                                [--repo OWNER/REPO] [--template-release vYYYY.MM.DD[.n]]
#                                [--force]
#    --description       what the project does, one or two sentences (default: a TODO line)
#    --name              the title (default: the repository name, "money-scribe" -> "Money Scribe")
#    --repo              for the badges (default: read from the `origin` remote)
#    --template-release  the template release the project started from (default: the newest
#                        release on GitHub; pass the one you started from if you run this later)
#    --force             overwrite a README that is no longer the template guide
#
#  Everything is read from the project: Python from the container, Django from
#  uv.lock, the dev image and PostgreSQL / Redis from docker-compose.yml, uv
#  from the container, the copyright line from LICENSE. Nothing private is written.
# =============================================================
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readme="$root/README.md"
template_repo="alihaidar0/django-template"

usage() {
    sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]}" | sed -e '$d' -e 's/^# \{0,1\}//'
}

die() {
    echo "$1" >&2
    exit 1
}

# ── Arguments ────────────────────────────────────────────────
name="" description="" repo="" template_release="" force=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --name) name="${2:?--name needs a value}"; shift 2 ;;
        --description) description="${2:?--description needs a value}"; shift 2 ;;
        --repo) repo="${2:?--repo needs OWNER/REPO}"; shift 2 ;;
        --template-release) template_release="${2:?--template-release needs a tag}"; shift 2 ;;
        --force) force=1; shift ;;
        -h | --help) usage; exit 0 ;;
        *) usage >&2; die "Unknown argument: $1" ;;
    esac
done

# ── Preconditions ────────────────────────────────────────────
[[ -f "$root/pyproject.toml" ]] \
    || die "No pyproject.toml yet: run the quickstart ('uv init --bare' …) first, then this script."

# The template guide starts with this heading. Anything else is already your README.
if [[ "$force" -ne 1 && -f "$readme" && "$(head -n 1 "$readme")" != "# django-template" ]]; then
    die "README.md is no longer the template guide, so it was left alone. Use --force to overwrite it."
fi

# ── Repository (for the badges) ──────────────────────────────
if [[ -z "$repo" ]]; then
    url="$(git -C "$root" remote get-url origin 2>/dev/null || true)"
    repo="$(sed -nE 's#^.*[:/]([^/:]+/[^/]+)$#\1#p' <<<"$url")"
    repo="${repo%.git}"
fi
[[ "$repo" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] \
    || die "Could not read OWNER/REPO from the origin remote. Pass it: --repo OWNER/REPO"

# ── Name and description ─────────────────────────────────────
if [[ -z "$name" ]]; then
    # "money-scribe" / "money_scribe" -> "Money Scribe"
    name="$(tr '_-' '  ' <<<"${repo#*/}" \
        | awk '{ for (i = 1; i <= NF; i++) $i = toupper(substr($i, 1, 1)) substr($i, 2) } 1')"
fi
todo=0
if [[ -z "$description" ]]; then
    description="TODO: describe what the project does in one or two sentences."
    todo=1
fi
description="$(fold -s -w 80 <<<"$description" | sed 's/[[:space:]]*$//')"

# ── Versions ─────────────────────────────────────────────────
python_version=""
if command -v python >/dev/null 2>&1; then
    python_version="$(python -c 'import platform; print(platform.python_version())' 2>/dev/null || true)"
fi
uv_version=""
if command -v uv >/dev/null 2>&1; then
    uv_version="$(uv --version 2>/dev/null | awk '{ print $2 }' || true)"
fi
# uv.lock lists `name = "django"` followed by its `version = "x.y.z"` line.
django_version=""
if [[ -f "$root/uv.lock" ]]; then
    django_version="$(awk '/^name = "django"$/ { getline; gsub(/"/, "", $3); print $3; exit }' "$root/uv.lock")"
fi
image="$(sed -nE 's/^[[:space:]]*image:[[:space:]]*(alihaidar199527\/django-devcontainer[^[:space:]@]*).*/\1/p' "$root/docker-compose.yml" | head -n 1)"
postgres_major="$(sed -nE 's/^[[:space:]]*image:[[:space:]]*postgres:([0-9]+).*/\1/p' "$root/docker-compose.yml" | head -n 1)"
redis_major="$(sed -nE 's/^[[:space:]]*image:[[:space:]]*redis:([0-9]+).*/\1/p' "$root/docker-compose.yml" | head -n 1)"

grep -qE 'django-devcontainer:[^[:space:]]*@sha256:' "$root/docker-compose.yml"     || echo "Note: the dev image is not pinned yet; run scripts/pin-image.sh and re-run this script." >&2
[[ -n "$django_version" ]] || echo "Note: Django was not found in uv.lock; run 'uv lock' and re-run this script." >&2

# ── Template release ─────────────────────────────────────────
if [[ -z "$template_release" ]]; then
    template_release="$(curl -fsSL --max-time 10 "https://api.github.com/repos/${template_repo}/releases/latest" 2>/dev/null \
        | sed -nE 's/.*"tag_name"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p' | head -n 1 || true)"
fi
if [[ -n "$template_release" ]]; then
    template_text="Created from [\`${template_repo}\`](https://github.com/${template_repo})
release [\`${template_release}\`](https://github.com/${template_repo}/releases/tag/${template_release})."
else
    template_text="Created from [\`${template_repo}\`](https://github.com/${template_repo}).
Note the template release it started from here
(see its [Releases](https://github.com/${template_repo}/releases) page)."
    echo "Note: could not read the template's newest release; fill in the Template section yourself." >&2
fi

# ── License ──────────────────────────────────────────────────
license_badge=""
license_text="See [LICENSE](LICENSE)."
if [[ -f "$root/LICENSE" ]]; then
    if head -n 1 "$root/LICENSE" | grep -qi '^MIT License'; then
        license_badge="[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)"
        license_text="[MIT](LICENSE)"
    fi
    copyright="$(sed -nE 's/^Copyright \(c\) (.+)$/\1/p' "$root/LICENSE" | head -n 1)"
    if [[ -n "$copyright" ]]; then
        license_text="${license_text%.} © ${copyright}"
    fi
fi

# ── Write ────────────────────────────────────────────────────
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
{
    printf '# %s\n\n%s\n\n' "$name" "$description"
    printf '[![CI](https://github.com/%s/actions/workflows/ci.yml/badge.svg?branch=develop)](https://github.com/%s/actions/workflows/ci.yml)\n' "$repo" "$repo"
    [[ -z "$license_badge" ]] || printf '%s\n' "$license_badge"
    printf '\n> **Status:** in development.\n\n'
    printf '## Stack\n\n| Component | Version |\n| --- | --- |\n'
    printf '| Python | %s |\n' "${python_version:-—}"
    printf '| Django | %s |\n' "${django_version:-—}"
    printf '| uv | %s |\n' "${uv_version:-—}"
    printf '| PostgreSQL | %s |\n' "${postgres_major:-—}"
    printf '| Redis | %s |\n' "${redis_major:-—}"
    printf '| Dev container image | %s |\n\n' "${image:+\`${image}\`}"
    # shellcheck disable=SC2016 # the backticks are Markdown, not command substitution
    printf 'Application libraries and their exact versions are pinned in `pyproject.toml` and\n`uv.lock`.\n\n'
    # shellcheck disable=SC2016
    printf '## Getting started\n\nInstall Docker Desktop and VS Code with the Dev Containers extension, then:\n\n```bash\ngit clone git@github.com:%s.git\ncode %s\n```\n\n' "$repo" "${repo#*/}"
    # shellcheck disable=SC2016
    printf 'Click **Reopen in Container**. The first start installs the dependencies and the git\nhooks (`uv sync --locked`, `pre-commit install`); the Celery worker and Beat start by\nthemselves. Then:\n\n```bash\npython manage.py migrate\npython manage.py runserver 0.0.0.0:8000\n```\n\n'
    # shellcheck disable=SC2016
    printf '| Service | URL |\n| --- | --- |\n| Django | <http://localhost:8000> |\n| Mailpit | <http://localhost:8025> |\n| Flower | <http://localhost:5555> (`celery -A config flower --port=5555`) |\n\n'
    # shellcheck disable=SC2016
    printf '## Development\n\n`develop` and `main` are protected: work happens on topic branches and pull requests\ninto `develop`; only `develop` opens a pull request into `main` (the release). The git hooks\n(Conventional Commits, no commits or pushes on `develop`/`main`) are installed with the container;\nthe single required check is **CI passed**.\n\n- Branches, hooks, SSH keys, CI and releases: [`.github/CONTRIBUTING.md`](.github/CONTRIBUTING.md)\n- GitHub settings, Actions permissions, environments, security and rulesets: [`docs/github-setup.md`](docs/github-setup.md)\n- The dev image is pinned in `docker-compose.yml`; Dependabot proposes updates as pull requests.\n- CI builds and smoke-tests the production image on every pull request and pushes nothing. To publish it, set the repository variable `PUBLISH_IMAGE` (`ghcr` or `dockerhub`), see [`docs/github-setup.md`](docs/github-setup.md) section 3.\n\n'
    printf '## Template\n\n%s\n\n' "$template_text"
    printf '## License\n\n%s\n' "$license_text"
} >"$tmp"
cat "$tmp" >"$readme"

echo "Wrote README.md for ${name} (${repo})."
echo ""
echo "Next steps:"
if [[ "$todo" -eq 1 ]]; then
    echo "  - Replace the TODO description in README.md (or re-run with --description \"…\" --force)."
fi
echo "  - Add what the project is for (features, how to deploy it) as it grows; the Stack table is yours to extend."
echo "  - Commit README.md."
