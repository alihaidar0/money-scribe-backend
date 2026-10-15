# Contributing & repository operations

Source of truth for **branching, protection rules, CI/CD, releases, and
dependency automation**. Some pieces are applied on GitHub (rulesets,
settings) rather than in the repo — step by step in
[`docs/github-setup.md`](../docs/github-setup.md). Project setup is in the
root [`README.md`](../README.md).

---

## 1. Branching model

```text
feat/*  fix/*  docs/*  ci/*  chore/*  deps/*      Dependabot
        │                                              │
        └────────── pull request ──────────────────────┘
                          ▼
develop  ──────────────────────────────────────  (default branch, integration)
                          │
                          └──pull request──▶  main   (release: every merge publishes the production image)
```

| Branch | Role | Direct push? | Accepts PRs from |
| --- | --- | --- | --- |
| `main` | Released history | ❌ | `develop` **only** |
| `develop` | Integration branch and the **default branch** | ❌ | topic branches and Dependabot |
| `feat/*` `fix/*` `docs/*` `ci/*` `chore/*` `deps/*` | One change each | ✅ (yours) | — |

- Create a topic branch from an up-to-date `develop`, commit, push it, and
  open a pull request **into `develop`**. Merge it with **Create a merge
  commit** (no squash, no rebase) once **CI passed** is green.
- A release is one pull request **`develop` → `main`**, title
  `release: <summary>`, also merged with a merge commit.
- Dependabot opens every update pull request against `develop`. Review them
  once a week, merge what you need, then release.
- A new repository created from the template starts with `develop` only; the
  **Bootstrap main** workflow creates `main` from it.
- Commit messages and pull request titles follow **Conventional Commits**
  (enforced locally by the `commit-msg` hook and in CI): `feat` `fix` `chore`
  `docs` `style` `refactor` `perf` `test` `build` `ci` `revert` `wip`. A
  breaking change is `type!:` with a `BREAKING CHANGE:` footer.
- Local safety nets (the rulesets are the real gate): `no-commit-to-branch`
  stops a commit on `develop`/`main`, `block-protected-push` stops a push.

---

## 2. GitHub settings (apply in the UI)

The rulesets ship as importable JSON in [`rulesets/`](rulesets/); every other
setting is listed in [`docs/github-setup.md`](../docs/github-setup.md).

| Ruleset | Target | Enforces |
| --- | --- | --- |
| `protect-main` | `main` | no creation/deletion/force-push · pull request required (merge commits only, conversations resolved) · required check **`CI passed`** (source GitHub Actions) |
| `protect-develop` | `develop` | no deletion/force-push · pull request required (merge commits only, conversations resolved) · required check **`CI passed`**, branch up to date |
| `protect-tags` | `v*` | no tag updates or deletion — a released version is immutable |

- Bypass list **empty**; required approvals **0** while solo (raise to 1 and
  enable *Require review from Code Owners* with a second maintainer — the
  author cannot approve their own pull request).
- **"Only `develop` may open PRs into `main`"** isn't expressible as a ruleset.
  The `Verify source branch` job in [`workflows/ci.yml`](workflows/ci.yml)
  fails any PR into `main` whose head isn't this repo's `develop`; requiring
  `CI passed` makes the rule binding.
- **General:** default branch **`develop`**; *Allow merge commits* ✅ (squash
  and rebase ❌); *Automatically delete head branches* ✅. In the **template
  repository only**: *Template repository* ✅.
- **Advanced Security:** private vulnerability reporting, dependency graph,
  Dependabot alerts + security updates, CodeQL default setup, secret
  protection + push protection — all ✅. Security updates open against
  `develop`, the default branch, like the weekly version updates.
- **Secrets and variables → Actions:** nothing is required. Optional: variable
  **`PUBLISH_IMAGE`** (`ghcr` or `dockerhub`; unset publishes nothing) and, for
  Docker Hub only, variable `DOCKERHUB_USERNAME` + secret `DOCKERHUB_TOKEN`; optional
  secret `CODECOV_TOKEN`.

---

## 3. CI/CD

| Workflow | Triggers | What |
| --- | --- | --- |
| [`ci.yml`](workflows/ci.yml) → **`CI passed`** (required) | PR → `develop` / `main` | source branch · Lint · commit messages · pre-commit · template guard · tiered project jobs (below) |
| [`lint.yml`](workflows/lint.yml) | called by `ci.yml`, dispatch | hadolint · ShellCheck · actionlint · zizmor · `docker compose config` · cspell · markdownlint |
| [`pr-labels.yml`](workflows/pr-labels.yml) | PR opened / edited | labels from the Conventional Commit title (drive the release notes) |
| [`labels.yml`](workflows/labels.yml) | push `develop` / `main` touching labels, PR validation, dispatch | `.github/labels.yml` → GitHub labels (`gh` CLI); removes GitHub's five default labels; a manual run with `prune` removes every label not in the file |
| [`bootstrap-main.yml`](workflows/bootstrap-main.yml) | push `develop`, dispatch | projects only: creates `main` from `develop` once |
| [`release.yml`](workflows/release.yml) | push `main` | GitHub Release with generated notes (template: `vYYYY.MM.DD`; project: `pyproject.toml` version, then starts `docker.yml` on the tag) |
| [`docker.yml`](workflows/docker.yml) → **Docker** | push `main` (release), tags `v*.*.*`, dispatch | **opt-in** (`PUBLISH_IMAGE`): publish the production image to GHCR or Docker Hub; ends green with a notice when unset or until the project is initialised |
| [`dockerhub-description.yml`](workflows/dockerhub-description.yml) | push `main` touching `README.md`, dispatch | **opt-in** (`PUBLISH_IMAGE=dockerhub`): README → the image's Docker Hub page |
| [`image-contract.yml`](workflows/image-contract.yml) | weekly, dispatch | template only: PostgreSQL / Python versions, image reference and shell aliases vs `django-devcontainer` (not required) |

### CI tiers — never fails on a fresh template

| Tier | Detected by | Jobs that run |
| --- | --- | --- |
| 1 · template | no `pyproject.toml` | `pre-commit` · `Production image` (scaffolds a sample project with the documented steps, checks it with ruff · mypy strict · pytest · migrations · deploy check, then builds it and probes `/healthz` + the CSP header) |
| 2 · pre-Django | `pyproject.toml` | `pre-commit` · `pip-audit` |
| 3 · Django | `manage.py` + `uv.lock` | `pre-commit` · `mypy` · `pytest` (PostgreSQL 18 service) · `Django deploy check` (+ missing migrations) · `pip-audit` · `Production image` |

The `CI passed` gate job passes when every applicable job passed — skipped
jobs are simply not applicable at the current tier.

**API contract jobs** (any tier, opt-in): when `openapi/schema.yaml` exists, `API schema is
current`, `API style (Spectral)` and `API breaking changes (oasdiff)` also run. They use the
pinned images in [`api-tools/docker-compose.yml`](api-tools/docker-compose.yml) and no GitHub
Action, so a missing allowed-actions entry can never fail the workflow. Details:
[`docs/api-workflow.md`](../docs/api-workflow.md).

### Releases

CI builds and smoke-tests the production image on every pull request and pushes
nothing. A project that opted in to publishing (`PUBLISH_IMAGE`) gets, on a merge
`develop` → `main`, `<registry>/<repo>` as `latest` + `sha-<short>` (native
amd64 + arm64, SBOM, Sigstore-signed provenance), scanned with Trivy.
In a project the same merge runs **Release**: when `version` in `pyproject.toml`
is new it publishes `vX.Y.Z` (with `openapi/schema.yaml` attached as `openapi.yaml` when the
project has one; notes generated from the merged pull requests,
grouped by label — [`release.yml`](release.yml) lists the categories) and, when
publishing is on, starts `docker.yml` on that tag for the `X.Y.Z` and `X.Y` image tags. In the template
repository **Release** publishes `vYYYY.MM.DD`.

### Python version

**Python follows Django.** The production image (and CI) run the newest Python minor that
Django officially supports, read from the version classifiers Django publishes on PyPI: not the
newest Python released (Django 6.1 lists 3.12–3.14, so 3.15 is not used yet). The dev image
follows the same rule. Dependabot therefore proposes only Python **patch** updates
(`3.14.8` → `3.14.9`) for `docker/Dockerfile.prod`; a new minor, or a release candidate of one,
never arrives as a pull request. When Django adds support for a newer minor and the dev image has
moved, the weekly *Image contract* run turns red, and you move on purpose with one command:
`bash scripts/set-python.sh` (it picks the newest minor Django supports, resolves the image tag
and digest, and updates `docker/Dockerfile.prod`, `ci.yml` and the README badge; `--check`
shows the change without making it). It refuses a minor that Django does not list. The generated
project's mypy uses the interpreter it runs in, so no version is written into `pyproject.toml`.

### Workflow hardening (keep it when editing)

- Every action and reusable workflow pinned to a **full commit SHA** with a
  `# vX.Y.Z` comment; Dependabot bumps both.
- Explicit runners (`ubuntu-26.04`, `ubuntu-26.04-arm`), never `ubuntu-latest`.
- Top-level `permissions: contents: read`; jobs widen only what they need.
- `persist-credentials: false` on every checkout; `timeout-minutes` on every job.
- Event values reach `run:` scripts only through `env:`, never inline `${{ }}`.
- A job downstream of a skipped job needs an explicit
  `if: ${{ !cancelled() && needs.<job>.result == 'success' }}`.

---

## 4. Dependency automation — [`dependabot.yml`](dependabot.yml)

Weekly (Monday 09:00 UTC), grouped, PRs against **`develop`**, **7-day
cooldown**:

| Ecosystem | Scans |
| --- | --- |
| `github-actions` | workflow action SHAs (incl. the reusable builder) |
| `docker` | `docker/Dockerfile.prod` — python base (patch updates only: a new minor is moved to with `scripts/set-python.sh`) + uv (tag + digest) |
| `docker-compose` | `docker-compose.yml` — postgres, redis, mailpit **and the dev image**; `api-tools/docker-compose.yml` — oasdiff and Spectral (merge stable tags only) once `scripts/pin-image.sh` pinned it (tag + digest) |
| `pre-commit` | `.pre-commit-config.yaml` hook revs |
| `uv` | `pyproject.toml` + `uv.lock` — enabled automatically by `scripts/init-django.sh` |

Every label Dependabot applies exists in [`labels.yml`](labels.yml).

---

## 5. Local checks

Inside the dev container:

```bash
pre-commit run --all-files     # ruff, django-upgrade, file hygiene
pytest                         # once the project exists
mypy .
```

Repository linters (from any machine with Docker):

```bash
docker run --rm -i hadolint/hadolint hadolint - < docker/Dockerfile.prod
docker run --rm -v "$PWD:/mnt" -w /mnt koalaman/shellcheck:stable scripts/*.sh .github/scripts/*.sh
docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest
docker compose config --quiet
```
