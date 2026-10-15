# ADR 0002: Draft releases and environments

- Status: accepted
- Date: 2026-10-15

## Context

`develop` is the integration branch and `main` is the released branch. The owner wants `develop` to stand for
staging and `main` for production, and wants to decide when a release goes out. Until now a merge into `main`
published the release at once, and a published release is immutable, so a mistaken release could not be undone.
The Flutter client already uses the opposite arrangement for its app builds (a rolling draft for `develop` and a
versioned draft for `main`, published by hand), and the two repositories should read the same way.

## Decision

`.github/workflows/release.yml` keeps two draft releases current through `scripts/release-draft.sh`. Nothing is
published by a workflow.

| Branch | Environment | Draft | Behaviour |
| --- | --- | --- | --- |
| `develop` | staging | **Staging**, tag `staging`, pre-release | Replaced by every merge. Never published. |
| `main` | production | **`v<version>`**, from `pyproject.toml` | Created or replaced by every merge until the owner publishes it from the Releases page. |

Both drafts carry `openapi/schema.yaml` as the asset `openapi.yaml`, so the contract for a version is attached before
the release becomes immutable. Notes for the production draft are generated from the merged pull requests.

Publishing the production draft creates the tag `v<version>` as the owner, which starts the Docker workflow for the
`X.Y.Z` and `X.Y` image tags when image publishing is on. A published release is left alone; the next release needs a
higher version. The script finds a draft by tag or by title, because GitHub renames the tag of a draft after it has
been updated. It fails when the Staging release has been published by mistake, because that release must stay a draft.

## Consequences

- Drafts are visible only to people with write access. The client repository's weekly contract check reads the newest
  published release, so it sees a version only after the owner publishes it. A client pins a published tag or a commit.
- `release.yml` needs `contents: write` only. The step that started the Docker workflow is no longer needed.
- The template repository keeps its calendar releases (`vYYYY.MM.DD`) on `main`; the draft logic is inert there.
- The tag `v<version>` is immutable (ruleset `protect-tags`), so check the draft before publishing.

## Not decided here: deployment

Deployment is a separate, later step. It needs choices that are not made yet and none of it is required for drafts:

- the hosting target for the app, PostgreSQL, Redis and the Celery worker and Beat, with separate staging and
  production instances;
- the GitHub environments `staging` (limited to `develop`) and `production` (limited to `main`), with their secrets;
- an image built from `develop` for staging, and deploy workflows that deploy by digest, run `migrate` once, and probe
  `/healthz`.

Staging holds invented data only. Production needs backups before it holds anyone's financial data.
