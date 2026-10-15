#!/usr/bin/env bash
# =============================================================
#  scripts/release-draft.sh — keep the project's DRAFT releases up to date
#
#  Called by .github/workflows/release.yml on every push to develop or main.
#  Nothing is published: a draft is visible to people with write access only,
#  and the owner publishes the production draft by hand when the version is
#  final (Releases page → the draft → Publish release).
#
#    develop  → ONE rolling draft "Staging" (tag staging, marked pre-release). Every
#               merge replaces its notes and files. Never publish it.
#    main     → the draft "v<version>" for the `version` in pyproject.toml. Every merge
#               to main replaces its notes and files, until the owner publishes it.
#               A published release is immutable, so it is left alone: raise the
#               version to prepare the next one.
#
#  Both drafts carry openapi/schema.yaml as the asset `openapi.yaml` when the
#  project has one. Publishing creates the tag `v<version>` (as the owner), which
#  starts docker.yml for the X.Y.Z and X.Y image tags when image publishing is on.
#
#  Needs: GH_TOKEN (contents: write), GH_REPO (OWNER/REPO), TARGET_SHA, BRANCH (the
#  branch that was pushed), the GitHub CLI and jq. Run from the repository root with
#  full history and tags (the workflow checks out with fetch-depth 0).
#  Optional: GITHUB_STEP_SUMMARY (the job summary file).
#
#  Inert until the project has a manage.py and a pyproject.toml.
# =============================================================
set -euo pipefail

: "${GH_TOKEN:?GH_TOKEN is required}"
: "${GH_REPO:?GH_REPO is required}"
: "${TARGET_SHA:?TARGET_SHA is required}"
: "${BRANCH:?BRANCH is required}"

summary="${GITHUB_STEP_SUMMARY:-/dev/null}"

if [[ ! -f manage.py || ! -f pyproject.toml ]]; then
    echo "No Django project yet: nothing to draft."
    exit 0
fi

version="$(sed -nE '/^version[[:space:]]*=/{s/^version[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/p;q}' pyproject.toml)"
if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]]; then
    echo "::error::pyproject.toml needs a semantic 'version' such as 1.0.0 (found '${version:-none}')."
    exit 1
fi

# ── What to draft ─────────────────────────────────────────────
case "$BRANCH" in
    main)
        tag="v${version}"
        title="$tag"
        prerelease=false
        if [[ "$version" == *-* ]]; then prerelease=true; fi
        ;;
    develop)
        tag="staging"
        title="Staging"
        prerelease=true
        ;;
    *)
        echo "::error::Drafts are kept for develop and main only (got '${BRANCH}')."
        exit 1
        ;;
esac

# ── Find what exists ──────────────────────────────────────────
# GitHub renames the tag of a draft (to untagged-...) once the draft has been updated,
# so a draft is found by its tag or else by its title; a published release by its tag.
list="$(gh api --paginate "repos/${GH_REPO}/releases?per_page=100" | jq -c '.[] | {id, draft, name, tag_name}')"
published="$(jq -r --arg tag "$tag" 'select(.draft == false and .tag_name == $tag) | .id' <<<"$list" | sed -n 1p)"
drafts="$(jq -r --arg tag "$tag" --arg title "$title" \
    'select(.draft and (.tag_name == $tag or .name == $title)) | .id' <<<"$list")"
draft_id="$(sed -n 1p <<<"$drafts")"
draft_count="$(grep -c . <<<"$drafts" || true)"

# A tag without a release (created by hand) counts as released too.
if [[ "$BRANCH" == "main" ]] && git rev-parse -q --verify "refs/tags/${tag}" >/dev/null; then
    published="${published:-tag}"
fi

if [[ -n "$published" && "$BRANCH" == "develop" ]]; then
    echo "::error::The Staging release was published, but it must stay a draft. Delete it on the Releases page so the next merge into develop can create a new draft."
    exit 1
fi
if [[ -n "$published" ]]; then
    echo "::notice::${tag} is already published and immutable; nothing was changed. Raise the version in pyproject.toml to prepare the next release."
    echo "${tag} is already published; its draft was not touched. Raise the version in \`pyproject.toml\` to prepare the next release." >>"$summary"
    exit 0
fi
if [[ "$draft_count" -gt 1 ]]; then
    echo "::warning::${draft_count} drafts match ${title}; the newest is used. Delete the others on the Releases page."
fi

# ── Notes ─────────────────────────────────────────────────────
if [[ "$BRANCH" == "main" ]]; then
    generated="$(gh api -X POST "repos/${GH_REPO}/releases/generate-notes" \
        -f tag_name="$tag" -f target_commitish="$TARGET_SHA" --jq .body)"
    body="$(printf 'Version %s, from commit %s.\n\n%s\n' "$version" "$TARGET_SHA" "$generated")"
else
    body="$(printf 'Staging snapshot of version %s, from commit %s on the develop branch.\n\nEvery merge into develop replaces the notes and files of this draft. Do not publish it.\n' \
        "$version" "$TARGET_SHA")"
fi

# ── Create or refresh the draft ───────────────────────────────
if [[ -z "$draft_id" ]]; then
    id="$(gh api -X POST "repos/${GH_REPO}/releases" \
        -f tag_name="$tag" -f target_commitish="$TARGET_SHA" -f name="$title" \
        -f body="$body" -F draft=true -F prerelease="$prerelease" --jq .id)"
    action="created"
else
    id="$draft_id"
    # Sending the tag again keeps the draft under it.
    reported="$(gh api -X PATCH "repos/${GH_REPO}/releases/${id}" \
        -f tag_name="$tag" -f target_commitish="$TARGET_SHA" -f name="$title" \
        -f body="$body" -F prerelease="$prerelease" --jq .tag_name)"
    if [[ "$reported" != "$tag" ]]; then
        echo "::notice::GitHub lists this draft under the tag ${reported}; it is found again by its title."
    fi
    # Replace the files: remove the old ones, then upload the new ones.
    for asset in $(gh api --paginate "repos/${GH_REPO}/releases/${id}/assets" --jq '.[].id'); do
        gh api -X DELETE "repos/${GH_REPO}/releases/assets/${asset}"
    done
    action="refreshed"
fi

# The API contract travels with the release, so a client developer pins a tagged, immutable
# file instead of copying one from a commit. A project without openapi/schema.yaml has none.
files="none"
if [[ -f openapi/schema.yaml ]]; then
    gh api -X POST \
        "https://uploads.github.com/repos/${GH_REPO}/releases/${id}/assets?name=openapi.yaml&label=OpenAPI%20contract%20%28openapi.yaml%29" \
        -H "Content-Type: application/octet-stream" --input openapi/schema.yaml >/dev/null
    files="openapi.yaml"
fi

{
    echo "Draft release **${title}** ${action} (release ${id}, commit \`${TARGET_SHA:0:7}\`). Files: ${files}."
    if [[ "$BRANCH" == "main" ]]; then
        echo "It stays a draft until you publish it from the Releases page."
    else
        echo "This draft is replaced by every merge into develop and is never published."
    fi
} >>"$summary"
echo "Draft release ${title} ${action} (release ${id})."
