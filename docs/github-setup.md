# GitHub repository setup

Everything that can live in the repository (workflows, templates, labels,
Dependabot, rulesets as JSON) is versioned under `.github/`. This guide covers
the parts that can only be configured on GitHub. It applies twice: once to the
`django-template` repository, and again to **every project created from it**,
because the workflows and rulesets are copied along but the repository
settings are not. Menu names change occasionally; the setting names below are
what to look for.

## Branch model

```text
feat/*  fix/*  docs/*  ci/*  chore/*  deps/*
        │  pull request, merge commit
        ▼
     develop   ← default branch, protected: pull requests only
        │  pull request, merge commit, source must be `develop`
        ▼
       main    ← stable branch, protected: pull requests only
        │  push (the merge)
        ▼
  release.yml (draft GitHub Release) + docker.yml (production image, only if PUBLISH_IMAGE is set)
```

- Nobody pushes to `develop` or `main` directly (rulesets enforce it; the
  `no-commit-to-branch` and pre-push hooks stop it locally as a convenience).
- `develop` is the default branch. Every change goes on a topic branch and
  opens a pull request against `develop`; Dependabot does the same.
- When `develop` is verified, one pull request `develop` → `main` promotes it.
- Both branches use **merge commits** (no squash, no rebase). Squashing into
  `main` rewrites `develop`'s commits and makes the next promotion conflict.
- `develop` is the staging line and `main` the production line. A push to `develop`
  refreshes the rolling draft release "Staging"; a push to `main` refreshes the draft
  `v<version>`. Nothing is published automatically: you publish the production draft
  yourself (section 8).

A pull request into `main` from any branch other than `develop` fails the
**Verify source branch** job. GitHub rulesets cannot restrict a pull request's
source branch, so the rule lives in `ci.yml` and is made mandatory through the
required **CI passed** check.

## 1. Settings → General

| Setting | Value |
| --- | --- |
| Repository name and description | a one-line description of what the project does |
| Default branch | `develop` |
| Template repository | **On** for `django-template` only (leave **Off** in projects created from it) |
| Features | Issues **on** · Wikis **off** · Projects **off** · Discussions **off** · Sponsorships off |
| Pull Requests → Allow merge commits | **On** (default message: pull request title and description) |
| Pull Requests → Allow squash merging | **Off** |
| Pull Requests → Allow rebase merging | **Off** |
| Pull Requests → Always suggest updating pull request branches | On |
| Pull Requests → Allow auto-merge | On |
| Pull Requests → Automatically delete head branches | On |
| Pull Requests → Allow forking (private projects) | Off |
| Releases → Enable release immutability (if offered) | **On**: it is what lets a client verify a release asset (`gh release verify-asset`), see `docs/consuming-the-api.md` |
| Pages | Off (nothing is published as a site) |
| Webhooks, Codespaces prebuilds | none |

The default branch is `develop`, the integration branch, so every automatic
pull request (Dependabot *version* and *security* updates) and every new pull
request targets it by default, and `main` is reached only through the
`develop` → `main` release pull request. For `django-template` itself also set
a description and these topics: `django`, `python`, `uv`, `docker`,
`devcontainer`, `celery`, `postgresql`, `pre-commit`, `github-template`,
`ci-cd`.

## 2. Settings → Actions → General

| Setting | Value |
| --- | --- |
| Actions permissions | Allow `<your user>`, and select non-`<your user>`, actions and reusable workflows |
| Allow actions created by GitHub | On |
| Allow Marketplace actions by verified creators | Off |
| Allowed actions (separated by commas, one per line: an entry without its comma runs into the next line and neither is allowed) | `astral-sh/setup-uv@*`, `hadolint/hadolint-action@*`, `raven-actions/actionlint@*`, `zizmorcore/zizmor-action@*`, `streetsidesoftware/cspell-action@*`, `DavidAnson/markdownlint-cli2-action@*`, `docker/*`, `aws-actions/configure-aws-credentials@*`, `google-github-actions/auth@*`, `azure/login@*` and `chainguard-dev/setup-chainctl@*` (never run here: they sit in optional registry-login steps inside Docker's reusable builder, each behind an `if` that is false unless that registry is configured, and GitHub checks every action a called workflow references, so without them `docker.yml` fails at startup even when publishing is off; the last two came with builder v1.18.0, and the startup error of the run names the ones that are missing, so a later builder version may need another entry), `aquasecurity/trivy-action@*`, `aquasecurity/setup-trivy@*` (the Trivy action runs it inside the Scan job of `docker.yml`; GitHub checks nested actions too, so without this pattern the Scan job fails at set-up and the image scan never runs), `codecov/codecov-action@*`, `peter-evans/dockerhub-description@*`, `github/codeql-action/*`, `dependabot/fetch-metadata@*` (only if you enable the optional auto-merge) |
| Require actions to be pinned to a full-length commit SHA | **On** |
| Artifact and log retention | 30 days |
| Fork pull request workflows from outside collaborators | Require approval for all external contributors |
| Send write tokens / secrets to fork pull request workflows | Off |
| Workflow permissions | **Read repository contents and packages permissions** |
| Allow GitHub Actions to create and approve pull requests | Off |
| Runners | GitHub-hosted only (no self-hosted runners) |

Every workflow also declares its own top-level `permissions:` block
(`contents: read`, or none at all); the jobs that need more request it
explicitly:

| Workflow | Extra permission | Why |
| --- | --- | --- |
| `bootstrap-main.yml` | `contents: write` | create the `main` branch |
| `release.yml` | `contents: write` | template: publish the calendar release; project: create and refresh the draft releases and attach the API contract |
| `labels.yml` | `issues: write` | create the labels |
| `pr-labels.yml` | `pull-requests: write` | add labels from the PR title |
| `docker.yml` | `id-token: write`, `packages: write`, `security-events: write` | keyless signing; push to ghcr.io (only when `PUBLISH_IMAGE=ghcr`); upload the scan |

Workflows run on an explicit runner image (`ubuntu-26.04`) rather than
`ubuntu-latest`. GitHub moves `ubuntu-latest` on its own schedule, which
changes the toolchain under every job at once; moving to a newer image is a
deliberate edit of the `runs-on:` lines once the build has been verified.

## 3. Environments and secrets

CI needs **no secrets and no environments**: it only uses the automatic
`GITHUB_TOKEN`, and it verifies the production image (build + smoke test)
without pushing it anywhere. Publishing the image to a registry is **opt-in**,
chosen per project with one repository variable (Settings → Secrets and
variables → Actions → Variables):

| `PUBLISH_IMAGE` | What happens on a merge into `main` (and into `develop`: the `staging` image) | What to add |
| --- | --- | --- |
| *(unset — the default)* | nothing is published; `docker.yml` ends green with a notice | nothing |
| `ghcr` | the image goes to GitHub Container Registry, `ghcr.io/<owner>/<repo>` | nothing: it signs in with the automatic token. Set the package visibility under the repository's *Packages* if you want it public |
| `dockerhub` | the image goes to `docker.io/<DOCKERHUB_USERNAME>/<repo>`, and the README is synced to the Docker Hub page | variable `DOCKERHUB_USERNAME` (your namespace) and secret `DOCKERHUB_TOKEN` (an access token, Read & Write) |

A merge into `develop` also builds the image when publishing is on, with the tags `staging` and `sha-<short>` (never
`latest`); `main` and the `v*.*.*` tags keep their own tags. A project that deploys a staging environment adds the GitHub
environment `staging` (deployment branches: `develop`) with the secrets and variables of its host; this project's are
in [`staging.md`](staging.md).

Any other value fails the run on purpose, so a typo is noticed. A different
registry or host: replace the `publish` job in `docker.yml` and add the
project's own deployment workflow; the template assumes neither.

| Kind | Name | Value |
| --- | --- | --- |
| Variable | `PUBLISH_IMAGE` | `ghcr` or `dockerhub` — leave it unset to publish nothing |
| Variable (Docker Hub only) | `DOCKERHUB_USERNAME` | your Docker Hub namespace (not secret) |
| Secret (Docker Hub only) | `DOCKERHUB_TOKEN` | a Docker Hub access token (Read & Write) |
| Secret (optional) | `CODECOV_TOKEN` | enables the coverage upload |

The workflow also waits until the project has `manage.py` + `uv.lock`.
**Dependabot secrets** (a separate tab) stay empty: Dependabot pull requests
cannot read Actions secrets, and the coverage upload simply skips itself there.

**Environments** (Settings → Environments) are only needed once you add a
deployment workflow. Create one per target (each project deploys where it
wants) and keep the
credentials in the environment, not in repository secrets (a repository secret
stays readable from any branch):

| Environment | Deployment branches and tags | Protection | Holds |
| --- | --- | --- | --- |
| `production` | Selected branches → `main` only | Required reviewers (you or the team, once there is a second maintainer); Prevent self-review; Allow administrators to bypass **off** | the target's credentials and non-secret settings |
| `staging` (optional) | Selected branches → `develop` | none | the staging credentials |

The environment names mirror the branches: `develop` is staging and `main` is production. Deployment
itself (the hosting target, the environments with their secrets and the deploy workflows) is a separate,
later step that is deliberately not set up yet; the draft releases need none of it. The decision and
its order are recorded in [`adr/0002-draft-releases-and-environments.md`](adr/0002-draft-releases-and-environments.md).

Never commit a key, token or `.env` value; any file in the repository is
readable by everyone who can read the repository.

## 4. Settings → Advanced Security (Code security)

| Feature | Value |
| --- | --- |
| Dependency graph | On |
| Dependabot alerts | On |
| Dependabot malware alerts | On |
| Dependabot security updates | On (they open against `develop`, the default branch) |
| Grouped security updates | On |
| Dependabot version updates | Driven by `.github/dependabot.yml` (weekly, grouped, 7-day cooldown, pull requests to `develop`); nothing to toggle |
| Private vulnerability reporting | **On** (`SECURITY.md` links to it) |
| Code scanning → CodeQL | Default setup, query suite **Default**. Languages: **Actions** from day one (analyses the workflow files). **Add Python as soon as the project has code**: Settings → Advanced Security → CodeQL analysis → ⋯ → Edit configuration → tick *Python* → Save. A Django project that scans only the workflows is not scanned at all |
| Secret Protection → Secret scanning | On |
| Secret Protection → Push protection | **On** |
| Secret Protection → Validity checks | On |

## 5. Rulesets

Settings → Rules → Rulesets → **New ruleset** → **Import a ruleset**, once per
file in `.github/rulesets/`. Enforcement is **Active** and the bypass list is
**empty**, so the rules apply to administrators too.

| File | Targets | What it enforces |
| --- | --- | --- |
| `protect-main.json` | `main` | No creation, deletion or force-push; pull request required (merge commits only, conversations resolved, stale approvals dismissed); required check **CI passed** |
| `protect-develop.json` | `develop` | No deletion or force-push; pull request required (merge commits only, conversations resolved, stale approvals dismissed); required check **CI passed**, and the branch must be up to date before merging |
| `protect-tags.json` | `v*` tags | Released tags cannot be moved or deleted |

The individual rules, as they appear in the UI:

| Rule | `protect-main` | `protect-develop` |
| --- | --- | --- |
| Restrict creations | ✅ | ❌ |
| Restrict deletions | ✅ | ✅ |
| Restrict updates | ❌ (would block every merge) | ❌ |
| Block force pushes | ✅ | ✅ |
| Require a pull request before merging | ✅ | ✅ |
| &nbsp;&nbsp;Required approvals | 0 | 0 |
| &nbsp;&nbsp;Dismiss stale approvals on new commits | ✅ | ✅ |
| &nbsp;&nbsp;Require review from Code Owners | ❌ while solo | ❌ while solo |
| &nbsp;&nbsp;Require conversation resolution | ✅ | ✅ |
| &nbsp;&nbsp;Allowed merge methods | Merge only | Merge only |
| Require status checks to pass | **CI passed** (GitHub Actions) | **CI passed** (GitHub Actions) |
| &nbsp;&nbsp;Require branches to be up to date | ❌ | ✅ |

Notes:

- **The required check is bound to GitHub Actions** (`integration_id`
  15368), so no other app or status API can report a green **CI passed** on
  its own.
- **Required approvals are 0.** A pull request author cannot approve their own
  pull request, so requiring 1 approval would stop a solo maintainer from
  merging at all. Once a second maintainer joins, set
  `required_approving_review_count` to `1` and `require_code_owner_review` to
  `true` (`CODEOWNERS` is already in place), then import the file again.
- **`protect-main` does not require "up to date"**
  (`strict_required_status_checks_policy: false`). After every promotion
  `main` holds one merge commit that `develop` does not, so a strict rule
  would force a `main` → `develop` sync before each release. `protect-develop`
  is strict: a topic branch must contain the latest `develop` before it merges.
- **CI passed** is the only required check. It always runs, fails if any job
  failed or was cancelled, and passes when jobs were skipped by design (the
  Django jobs on a project that has no `manage.py` yet, the template guard in a
  generated project).
- **"Only `develop` may open a pull request into `main`"** cannot be written as
  a ruleset; the *Verify source branch* job of `ci.yml` enforces it and
  **CI passed** makes it binding.
- Import the rulesets *after* `main` exists and **CI passed** has run once,
  otherwise nothing has reported the required check yet and the first pull
  request could not merge.
- Optional hardening, not in the JSON: *Require signed commits* (needs commit
  signing set up in the container) and *Require code scanning results*
  (CodeQL). Do not enable *Require linear history*: it forbids merge commits.
- Rulesets are free on public repositories; on private repositories they
  depend on your GitHub plan.

Scripted alternative, with the GitHub CLI authenticated for the repository
(run it from the repository root):

```bash
gh api repos/OWNER/REPO --method PATCH \
  -F allow_merge_commit=true -F allow_squash_merge=false -F allow_rebase_merge=false \
  -F allow_auto_merge=true -F delete_branch_on_merge=true \
  -F has_wiki=false -F has_projects=false -F has_discussions=false
for ruleset in protect-main protect-develop protect-tags; do
  gh api repos/OWNER/REPO/rulesets --method POST --input ".github/rulesets/$ruleset.json"
done
```

## 6. First-time bootstrap

### The template repository

1. Push `main` and `develop` (both exist before the rulesets are imported),
   then make `develop` the default branch (section 1).
2. Apply sections 1, 2 and 4.
3. Check that the **Labels** workflow created every label (Actions → Labels);
   if they are missing, run it with Actions → Labels → Run workflow.
4. Import the three rulesets (section 5).
5. Tick **Template repository** (section 1) once everything above is in place.
6. Verify (next section).

The template repository itself needs **no Actions secrets and no variables**: it
builds and smoke-tests the production image in CI but publishes nothing, so leave
`PUBLISH_IMAGE` unset and keep *Settings → Secrets and variables* empty (delete any
leftover registry token and revoke it at the registry). Registry credentials belong to
each project that opts in (section 3).

### Every project created from the template

1. **Use this template** → Create a new repository. Leave **Include all
   branches** unchecked: the new repository starts with one branch, the
   template's default (`develop`). `main` is created for you by the
   **Bootstrap main** workflow, the first time it is missing. It starts by
   itself on the repository's first commit, so `main` appears within a minute;
   if it did not run, start it through Actions → Bootstrap main → Run
   workflow, or by hand: `git push origin develop:main` (the pre-push hook only
   blocks a branch that already exists on the remote). The workflow never
   touches an existing `main` and can be deleted once `main` exists.
2. The **Labels** workflow creates every label by itself on that first push (and removes GitHub's
   five default labels, so only the labels in `.github/labels.yml` remain),
   because the first commit contains `.github/labels.yml`. Check Actions →
   Labels before the first pull requests (Dependabot opens its first pull
   requests within minutes, and `pr-labels.yml` labels every pull request from
   its title); only if the labels are missing, run it with Actions → Labels →
   Run workflow, or `gh workflow run labels.yml`. Later edits of
   `.github/labels.yml` sync by themselves when they reach `develop` or `main`.
3. Update `.github/CODEOWNERS` with your username and review `LICENSE`,
   `SECURITY.md` and `CONTRIBUTING.md`: they describe the template until you
   replace them.
4. Open the project in VS Code, **Reopen in Container**, and follow the
   banner: the six quickstart commands, then `bash scripts/pin-image.sh` (pins
   the dev image to an exact build) and `bash scripts/init-readme.sh` (replaces
   the template guide with your project's README). Commit these on a topic
   branch and open the first pull request into `develop`.
5. Apply sections 1 to 4 (with **Template repository** left off) and import
   the rulesets once **CI passed** has run on the first pull request and `main`
   exists (section 5). As soon as the project has code, add **Python** to
   CodeQL (section 4).
6. `image-contract.yml`, the template release job and the template guard are
   inert in a project (they only run where the repository is a template).
   Delete them if you like. The `app-draft` job of `release.yml` keeps two draft
   releases current: the rolling "Staging" draft on every merge into `develop`, and
   the `v<version>` draft (the `pyproject.toml` version) on every merge into `main`.
   You publish the second one yourself.
7. Optional: set `PUBLISH_IMAGE` (section 3) if the project should push its
   production image to a registry, and add a deployment workflow, an
   environment and its secrets once you know where the project runs.

## 7. Verify the protection works

- `git push origin main` and `git push origin develop` are rejected by the
  rulesets (and, locally, by the pre-push hook).
- A pull request from a topic branch into `main` fails **Verify source
  branch**, so **CI passed** is red and the pull request cannot merge.
- A pull request from `develop` into `main` offers only **Create a merge
  commit**.
- A pull request from a topic branch into `develop` offers only **Create a
  merge commit** and cannot merge until **CI passed** is green.
- On a fresh checkout (no `pyproject.toml`) **CI passed** is green, with the
  Django jobs skipped but **Lint**, **Commit messages**, **pre-commit** and the
  scaffolded **Production image** test still running.
- A commit message such as `Fixed stuff` fails **Commit messages**.
- Security → Code scanning lists CodeQL for *Actions* (and *Python* once added);
  Security → Dependabot lists no errors.

## 8. Everyday workflow

```bash
# 0. confirm the identity the commit will use, and where the push will go
git config user.name
git config user.email
git remote -v

# 1. start from an up-to-date develop
git switch develop
git pull --ff-only

# 2. create a topic branch (feat/ fix/ docs/ ci/ chore/ deps/)
git switch -c fix/example-topic

# 3. stage explicit paths, commit with a Conventional Commit message, push
git add path/to/changed-file
git commit -m "fix(scope): describe the change"
git push -u origin fix/example-topic

# 4. open a pull request into develop and merge it with "Create a merge commit"
```

Every week: review the Dependabot pull requests (they target `develop`), merge
the ones you want, then open **one** pull request `develop` → `main`
(title `release: <summary>`) and merge it with a merge commit. That push runs
**Docker** (publishes the production image, when `PUBLISH_IMAGE` is set) and **Release**.

In a project, **Release** never publishes by itself. It keeps two drafts, which only
people with write access can see:

- A merge into `develop` replaces the notes and files of the pre-release draft
  **Staging** (tag `staging`). Never publish it.
- A merge into `main` creates or refreshes the draft `v<version>` for the `version` in
  `pyproject.toml`, with notes grouped by pull request label and the API contract attached
  as `openapi.yaml`. When the version is final, open the draft on the Releases page and
  click **Publish release**. That creates the tag `v<version>` (which starts **Docker** for
  the `X.Y.Z` and `X.Y` image tags when publishing is on) and makes the release immutable:
  raise the version to prepare the next one. Until then, every merge into `main` keeps
  replacing the draft's files.

In the template repository **Release** publishes a calendar-versioned release
(`vYYYY.MM.DD`) on a merge into `main`; a promotion that only changes documentation
produces none.

Once a week **Image contract** (template repository only) compares the
template with the `django-devcontainer` image: the PostgreSQL and Python
versions, the image the compose file pulls and the documented shell aliases. A
red run means one of the two repositories moved on; fix it with a topic branch
like any other change.

## 9. Optional: auto-merge Dependabot patch updates (not enabled)

Nothing in this repository merges a pull request for you: you review the
Dependabot pull requests once a week. If you later want the safest class —
**patch** updates of dependencies — merged automatically once **CI passed** is
green, add this workflow to the project. It is deliberately **not** shipped,
because an auto-merged update is code you did not read.

Prerequisites: *Allow auto-merge* is on (section 1), `protect-develop` requires
**CI passed** (section 5), and `dependabot/fetch-metadata@*` is in the allowed
actions (section 2). Auto-merge then waits for the required check.

```yaml
# .github/workflows/dependabot-automerge.yml
name: Dependabot auto-merge

on:
  pull_request:
    branches: [develop]

permissions: {}

jobs:
  automerge:
    name: Auto-merge patch updates
    if: ${{ github.event.pull_request.user.login == 'dependabot[bot]' }}
    runs-on: ubuntu-26.04
    timeout-minutes: 5
    permissions:
      contents: write
      pull-requests: write
    steps:
      - name: Read the update type
        id: meta
        uses: dependabot/fetch-metadata@25dd0e34f4fe68f24cc83900b1fe3fe149efef98 # v3.1.0

      - name: Merge when CI passed is green
        if: ${{ steps.meta.outputs.update-type == 'version-update:semver-patch' }}
        env:
          GH_TOKEN: ${{ github.token }}
          PR_URL: ${{ github.event.pull_request.html_url }}
        run: gh pr merge --auto --merge "$PR_URL"
```

Keep minor and major updates manual, and never auto-merge the dev image or the
`docker/` base images: a new Python minor arrives as an ordinary Dependabot
pull request and deserves a read. Re-check the action's pinned SHA with
`git ls-remote https://github.com/dependabot/fetch-metadata.git 'refs/tags/v*'`
before using it.
