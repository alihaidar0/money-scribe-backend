#!/usr/bin/env bash
# =============================================================
#  scripts/pin-image.sh — freeze the dev image of THIS project
#
#  The template follows the newest django-devcontainer image (`:latest`). A
#  project created from it should not change under you, so run this once after
#  the quickstart (and any time you want to move to a newer image on purpose):
#
#    docker-compose.yml: the dev `image:` line becomes
#      <repo>:python-X.Y.Z.R@sha256:<digest>
#    so Docker pulls exactly that build until you run the script again or merge
#    a Dependabot pull request (its docker-compose ecosystem reads this line).
#
#  Usage: scripts/pin-image.sh [tag]
#    tag  defaults to the newest immutable python-X.Y.Z.R published for the
#         Python release of this container (X.Y.Z = Python release, R = image
#         revision; a tag is never changed or deleted). When that release has
#         no tag, the newest build of the same Python line (X.Y) is used, then
#         the newest python-X.Y.Z.R overall, and `latest` as the last resort.
#         The Python line only changes on purpose: pass a tag of the new line.
#
#  Needs curl and network access to Docker Hub; changes nothing else.
# =============================================================
set -euo pipefail

repo="alihaidar199527/django-devcontainer"
compose="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/docker-compose.yml"

# Newest python-X.Y.Z.R tag whose name contains $1 (empty when none exists).
newest_tag() {
    curl -fsSL "https://hub.docker.com/v2/repositories/${repo}/tags?page_size=100&name=$1" 2>/dev/null \
        | grep -oE '"name"[[:space:]]*:[[:space:]]*"python-[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+"' \
        | sed -E 's/.*"(python-[^"]+)"$/\1/' | sort -V | tail -1 || true
}

tag="${1:-}"
if [[ -z "$tag" ]]; then
    python_version=""
    if command -v python >/dev/null 2>&1; then
        python_version="$(python -c 'import platform; print(platform.python_version())' 2>/dev/null || true)"
    fi
    [[ -n "$python_version" ]] && tag="$(newest_tag "python-${python_version}.")"
    [[ -z "$tag" && -n "$python_version" ]] && tag="$(newest_tag "python-${python_version%.*}.")"
    [[ -z "$tag" ]] && tag="$(newest_tag "python-")"
    [[ -z "$tag" ]] && tag="latest"
    echo "Selected image tag: ${tag}"
fi

# ── Resolve the tag to its immutable digest (anonymous Docker Hub pull token) ──
token="$(curl -fsSL "https://auth.docker.io/token?service=registry.docker.io&scope=repository:${repo}:pull" \
    | sed -nE 's/.*"token"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p')"
digest="$(
    curl -fsSI \
        -H "Authorization: Bearer ${token}" \
        -H "Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json" \
        "https://registry-1.docker.io/v2/${repo}/manifests/${tag}" \
        | awk 'tolower($1) == "docker-content-digest:" { print $2 }' | tr -d '\r'
)" || digest=""
if [[ ! "$digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    echo "Could not resolve ${repo}:${tag} on Docker Hub (is the tag published?)." >&2
    echo "Pass an existing tag explicitly (see the tags page on Docker Hub), for example:" >&2
    echo "  scripts/pin-image.sh python-X.Y.Z.R   or   scripts/pin-image.sh latest" >&2
    exit 1
fi

pinned="${repo}:${tag}@${digest}"
sed -i -E "s#^([[:space:]]*image:[[:space:]]*)${repo}[^[:space:]]*#\1${pinned}#" "$compose"
if ! grep -qF "image: ${pinned}" "$compose"; then
    echo "docker-compose.yml has no 'image: ${repo}…' line to pin." >&2
    exit 1
fi

echo "Pinned the dev image:"
echo "  image: ${pinned}"
echo ""
echo "Next steps:"
echo "  - Commit docker-compose.yml."
echo "  - Rebuild the container (Dev Containers: Rebuild Container) to use the pinned image."
echo "  - Dependabot (docker-compose ecosystem) now proposes newer python-X.Y.Z.R builds as pull requests."
