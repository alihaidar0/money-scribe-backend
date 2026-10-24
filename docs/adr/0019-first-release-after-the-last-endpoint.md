# ADR 0019: The first release is published after the last endpoint, and staging is deployed at agreed points

- Status: accepted
- Date: 2026-10-24
- Amended: 2026-10-24, the same day and before any release used it. Release pull requests may merge before the last
  endpoint; only the publishing of the draft waits.

## Context

The contract is still being built, one resource at a time, with the Flutter developer. A tagged release makes a contract
immutable (`docs/consuming-the-api.md`), and after the first release a published path may not be broken, only
versioned or deprecated (`docs/api-workflow.md`). Releasing a half-built API would therefore freeze shapes that are
still being agreed, and every step of stage 3 would need its own release for little gain. The owner decided on
2026-10-24 how releases and staging deploys are handled until the API is complete.

## Decision

### Releases

- **No version is published until every endpoint of the API exists** (the end of stage 3: the ledger, the debts, the
  reports, the CSV and the notifications, on top of what is built). Until then there is no published release and no
  tag. A release pull request `develop` → `main` may still be opened and merged whenever the owner wants `main` and the
  draft up to date: the merge only creates or refreshes the draft `v0.1.0` (ADR 0002), and a draft is not a release,
  so nothing is immutable and the client does not pin it. The draft `v0.1.0` **stays a draft**; the rolling draft
  "Staging", refreshed by merges into `develop`, is never published.
- **The first release is `v0.1.0`**, the version in `pyproject.toml` today. The owner publishes the draft once, after
  the last endpoint and a final release pull request with the usual checks (`docs/steps.md`, section 11), whose merge
  refreshes the notes and the `openapi.yaml` of the draft. Raise `version` in `pyproject.toml` before that merge only
  if the first release should carry another number; the draft of the old number then stays behind and can be deleted
  on the Releases page.
- **After the first release is published, a release is an update.** A release pull request is opened when `develop`
  holds something worth shipping, as `docs/api-workflow.md` describes: a new endpoint or field raises the
  minor version, a fix raises the patch, and a change that breaks a published path is never made in `v1` (it needs
  `/api/v2/` or a deprecation period).
- **Meanwhile the client pins a commit**, not a tag: the file `openapi/schema.yaml` as it is at a commit of `develop`
  (`docs/consuming-the-api.md`, "Before the first release exists"), and the staff-only Swagger UI of staging shows the
  contract that is deployed. The client moves to a tag when the first release is published.

### Staging

- **Staging is deployed by hand** (the repository variable `DEPLOY_STAGING` stays unset), and the next deploy happens when
  the endpoints that are agreed with the frontend are finished, not after every merge. The owner decides the moment and
  runs "Deploy staging" (`docs/staging.md`); an earlier deploy stays possible whenever it is needed.
- **The app tests after that deploy.** A step that needs the app on staging (the fingerprint sign-in on a real phone,
  salary, the settings) stays unticked in `docs/steps.md` until the test has run and its result is recorded.

## Consequences

- Rule 9 ("after every merge into `develop`, say whether a release PR is needed") keeps its normal answer, and adds that
  nothing is published before the last endpoint: the owner decides when a release pull request is merged, and the
  draft is never suggested for publishing earlier.
- Step 9.6 (publishing the first contract release) moves to the end of stage 3 (section 11); step 10's loop no longer
  ends in a tagged release or in an automatic staging deploy.
- The risk is a large first release and a long time without a staging test. It is limited by CI on every pull request
  (including the schema, style and breaking-change checks), by the clean-copy test run before every hand-over, and by the
  owner's right to deploy staging earlier.
- The Celery worker and Beat stay off staging until then (`docs/staging.md`), so the daily purge jobs have not run on it.
