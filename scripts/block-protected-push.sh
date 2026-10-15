#!/usr/bin/env bash
# =============================================================
#  scripts/block-protected-push.sh — pre-push guard
#
#  Blocks `git push` to main and develop. A local convenience; the GitHub
#  rulesets are the real gate. A branch is only blocked once it exists on the
#  remote, so creating it (the first push when a project is set up) still works.
#
#  Run by pre-commit at the pre-push stage (see .pre-commit-config.yaml), which
#  exports PRE_COMMIT_REMOTE_NAME and PRE_COMMIT_REMOTE_BRANCH (refs/heads/<name>).
# =============================================================
set -euo pipefail

remote="${PRE_COMMIT_REMOTE_NAME:-origin}"
branch="${PRE_COMMIT_REMOTE_BRANCH:-}"
branch="${branch#refs/heads/}"

if { [[ "$branch" == "main" ]] || [[ "$branch" == "develop" ]]; } \
    && git show-ref --verify --quiet "refs/remotes/${remote}/${branch}"; then
    echo ""
    echo "✖ Direct push to '${branch}' is blocked."
    echo "  Push to a topic branch, then open a pull request into develop"
    echo "  (a release is a pull request from develop into main)."
    echo ""
    exit 1
fi
