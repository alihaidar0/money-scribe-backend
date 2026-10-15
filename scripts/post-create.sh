#!/usr/bin/env bash
# =============================================================
#  scripts/post-create.sh — runs once when the dev container is
#  created (postCreateCommand in .devcontainer/devcontainer.json)
#
#  1. Git + SSH: trusts /workspace and checks the forwarded ssh-agent.
#     Keys come from the host's agent; no private key and no ~/.ssh is
#     mounted. welcome.sh (every start) maps a host-only remote alias to
#     github.com and pins this repository's key — scripts/pin-ssh-key.sh.
#  2. Hands the .venv volume to the `dev` user (Docker creates new
#     named volumes root-owned).
#  3. Git hooks: installs pre-commit, commit-msg and pre-push, so the
#     branch guards and Conventional Commits checks are active from the
#     very first commit (also on a fresh template).
#  4. Existing project (uv.lock present, e.g. a fresh clone): installs
#     the locked dependencies — ready to run.
#     Fresh template: does nothing more; welcome.sh shows the steps.
# =============================================================

set -euo pipefail

cd /workspace

# Bind-mounted files from Windows/macOS hosts show a different owner, which git
# refuses to work with ("dubious ownership"). Trust the workspace once.
if ! git config --global --get-all safe.directory 2>/dev/null | grep -qx /workspace; then
    git config --global --add safe.directory /workspace
fi

if ! ssh-add -l >/dev/null 2>&1; then
    echo "⚠ No SSH keys reachable in the container — start the host's ssh-agent and add"
    echo "  your key (Windows: the 'OpenSSH Authentication Agent' service + ssh-add), then"
    echo "  reopen the container. See README → Git and SSH."
fi

if [[ -d .venv && ! -O .venv ]]; then
    echo "▶ Giving the .venv volume to $(id -un)"
    sudo chown "$(id -u):$(id -g)" .venv
fi

if [[ -f uv.lock ]]; then
    echo "▶ uv sync --locked"
    uv sync --locked
fi

# Hooks do not need the project's venv (pre-commit is a global tool of the image),
# so they are installed on a fresh template too.
if [[ -f .pre-commit-config.yaml ]] && git rev-parse --git-dir >/dev/null 2>&1; then
    echo "▶ pre-commit install"
    pre-commit install --install-hooks
fi
