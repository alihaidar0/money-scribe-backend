#!/usr/bin/env bash
# =============================================================
#  scripts/welcome.sh — shown on every container start
#  (postStartCommand in .devcontainer/devcontainer.json)
#
#  Prints the next steps for the project's current state. No venv
#  activation is needed: the dev image puts /workspace/.venv/bin
#  first on PATH, so python / celery / pytest resolve to the project.
# =============================================================

set -euo pipefail

WORKSPACE=/workspace
RULE="━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

step() {  # step "<title>" <line>...
    local title=$1; shift
    echo ""
    echo "  ▸ ${title}"
    printf '      %s\n' "$@"
}

# ── SSH host alias and key ──────────────────────────────────────────────────
# Keys come from your host's ssh-agent (forwarded by VS Code); ~/.ssh is not
# mounted. A host-only alias in the remote URL (for example
# git@github.com-work:owner/repo.git, defined in the host's ~/.ssh/config) would
# not resolve here, and with several accounts' keys in the agent GitHub would
# accept whichever key comes first. pin-ssh-key.sh maps the alias to github.com
# and pins this repository's key (public key + IdentitiesOnly).
# Non-fatal: it is a convenience and must never block container start.
bash "$WORKSPACE/scripts/pin-ssh-key.sh" || true

# ── Git hooks ───────────────────────────────────────────────────────────────
# postCreateCommand installs them. Repeat it when they are missing (an
# interrupted first start, a recreated container), so the branch guards and the
# commit-message check protect even the very first commit. Non-fatal.
if [[ -f "$WORKSPACE/.pre-commit-config.yaml" ]] && git -C "$WORKSPACE" rev-parse --git-dir >/dev/null 2>&1 \
    && { [[ ! -x "$WORKSPACE/.git/hooks/pre-commit" ]] || [[ ! -x "$WORKSPACE/.git/hooks/pre-push" ]]; }; then
    echo "  🪝  Installing the git hooks (pre-commit install)..."
    (cd "$WORKSPACE" && pre-commit install --install-hooks >/dev/null 2>&1) \
        || echo "  ⚠️  Could not install the git hooks — run: pre-commit install"
fi

echo ""
echo "$RULE"
echo "  🐍  Django dev container ready — $(python --version 2>&1), uv $(uv --version | cut -d' ' -f2)"
echo "$RULE"

if [[ -f "$WORKSPACE/manage.py" ]]; then
    echo ""
    echo "  ✅  Django project detected"
    step "Run" \
        "pmr   python manage.py runserver 0.0.0.0:8000" \
        "pmm   python manage.py migrate      ·  pmmk  makemigrations" \
        "pms   python manage.py shell        ·  pmsu  createsuperuser" \
        "pytest                              ·  pre-commit run --all-files"
    step "Services" \
        "Django   http://localhost:8000" \
        "Mailpit  http://localhost:8025" \
        "Flower   celery -A config flower --port=5555  →  http://localhost:5555"
    if [[ ! -f "$WORKSPACE/openapi/schema.yaml" ]]; then
        step "API contract (with the first endpoint)" \
            "bash scripts/schema.sh generate   then commit openapi/schema.yaml"
    fi
    if ! grep -qE 'django-devcontainer:[^[:space:]]*@sha256:' "$WORKSPACE/docker-compose.yml" 2>/dev/null; then
        step "Freeze the toolchain (once)" \
            "bash scripts/pin-image.sh   pins the dev image to an exact build"
    fi
    if [[ "$(head -n 1 "$WORKSPACE/README.md" 2>/dev/null)" == "# django-template" ]]; then
        step "Write your project README (once)" \
            "bash scripts/init-readme.sh --description \"What it does.\""
    fi

elif [[ -f "$WORKSPACE/pyproject.toml" ]]; then
    echo ""
    echo "  ℹ️   pyproject.toml found — Django not initialised yet"
    step "Add dependencies (if not done yet)" \
        "uv add django djangorestframework 'psycopg[binary,pool]' dj-database-url \\" \
        "       redis celery django-celery-beat gunicorn whitenoise drf-spectacular django-cors-headers \\" \
        "       \"drf-standardized-errors[openapi]\"" \
        "uv add --dev pytest pytest-django pytest-cov factory-boy faker \\" \
        "             mypy ruff django-stubs celery-types flower pip-audit ipython"
    step "Scaffold Django (settings split, Celery, env-driven config)" \
        "bash scripts/init-django.sh"
    step "Hooks, database, server" \
        "pre-commit install" \
        "python manage.py migrate" \
        "python manage.py runserver 0.0.0.0:8000"

else
    echo ""
    echo "  👋  Fresh template — six steps to a running Django project, then two one-time commands"
    step "1. Initialise uv" \
        "uv init --bare"
    step "2. Production dependencies" \
        "uv add django djangorestframework 'psycopg[binary,pool]' dj-database-url \\" \
        "       redis celery django-celery-beat gunicorn whitenoise drf-spectacular django-cors-headers \\" \
        "       \"drf-standardized-errors[openapi]\""
    step "3. Dev / test dependencies" \
        "uv add --dev pytest pytest-django pytest-cov factory-boy faker \\" \
        "             mypy ruff django-stubs celery-types flower pip-audit ipython"
    step "4. Scaffold Django (settings split, Celery, env-driven config)" \
        "bash scripts/init-django.sh"
    step "5. Git hooks (pre-commit · commit-msg · pre-push)" \
        "pre-commit install"
    step "6. Database and server" \
        "python manage.py migrate" \
        "python manage.py runserver 0.0.0.0:8000"
    step "7. Freeze the toolchain (once)" \
        "bash scripts/pin-image.sh   pins the dev image to an exact build"
    step "8. Write your project README (once)" \
        "bash scripts/init-readme.sh --description \"What it does.\""
    step "9. Optional: publish the production image" \
        "set the repository variable PUBLISH_IMAGE to ghcr or dockerhub (README → 3.2)"
    step "10. With the first endpoint: start the API contract" \
        "bash scripts/schema.sh generate   then commit openapi/schema.yaml (README → step 9)"
fi

step "Commit on a topic branch — develop and main are protected" \
    "git switch -c feat/<topic>   ·   git commit   ·   git push -u origin feat/<topic>" \
    "then open a pull request into develop (merge commit); a release is develop → main."

step "Committing from the host too, not just this container?" \
    "The same git hooks run there — install pre-commit + uv on the host once," \
    "or a host commit stops with \"pre-commit not found\" (README → Git and SSH)."

echo ""
echo "  📖  https://github.com/alihaidar0/django-template#readme"
echo "$RULE"
echo ""
