# django-template

> **GitHub Template** — the starting point for every new Django project.
> Click **Use this template**, open in VS Code, **Reopen in Container**, start coding.

[![CI](https://github.com/alihaidar0/django-template/actions/workflows/ci.yml/badge.svg?branch=develop)](https://github.com/alihaidar0/django-template/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.14-blue?logo=python&logoColor=white)](https://www.python.org)
[![Django](https://img.shields.io/badge/django-6.x-green?logo=django&logoColor=white)](https://www.djangoproject.com)
[![uv](https://img.shields.io/badge/uv-package%20manager-blueviolet)](https://docs.astral.sh/uv/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://docs.astral.sh/ruff/)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit)](https://pre-commit.com)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](https://github.com/alihaidar0/django-template/blob/main/LICENSE)

---

## Contents

1. [What this template is](#1-what-this-template-is)
2. [Create a new project — from zero to the first run](#2-create-a-new-project--from-zero-to-the-first-run)
3. [Set up the GitHub repository](#3-set-up-the-github-repository)
4. [Daily workflow: branches and pull requests](#4-daily-workflow-branches-and-pull-requests)
5. [Git inside and outside the container](#5-git-inside-and-outside-the-container)
6. [Git hooks](#6-git-hooks)
7. [CI/CD: checks, releases and the production image](#7-cicd-checks-releases-and-the-production-image)
8. [Versions: the template follows the newest, projects freeze](#8-versions-the-template-follows-the-newest-projects-freeze)
9. [Reference](#9-reference)
10. [Troubleshooting](#10-troubleshooting)
11. [Maintaining the template itself](#11-maintaining-the-template-itself)

---

## 1. What this template is

A GitHub Template that gives every new Django project the same
**infrastructure and tooling skeleton** — the Django code itself is created
per project in six commands after the container starts.

- **Nothing to install locally** except Docker Desktop and VS Code: the dev
  environment is the shared image
  [`alihaidar199527/django-devcontainer`](https://hub.docker.com/r/alihaidar199527/django-devcontainer)
  — **pulled, never built** on your machine.
- **Full stack pre-wired:** PostgreSQL 18, Redis 8, Celery worker + Beat, Mailpit.
- **Pure-Python git hooks** (pre-commit + Conventional Commits + branch guards) — no Node.js.
- **Protected branches:** topic branch → pull request → `develop` → pull request → `main`.
  Nobody pushes to `develop` or `main`; rulesets ship as importable JSON.
- **One required check, `CI passed`:** lint, commit messages, pre-commit, mypy,
  pytest, deploy check, dependency audit and a production-image smoke test —
  tiered, so it never fails on a fresh template.
- **Production image** (Gunicorn, non-root, amd64 + arm64, SBOM, signed
  provenance), built and smoke-tested on every pull request; publishing it to
  GitHub Container Registry or Docker Hub is opt-in, per project.
- **Releases and labels as code:** labels sync from `.github/labels.yml`, pull
  requests are labelled from their title, release notes are generated from them.
- **Dependabot** for Actions, Docker images, compose services (including the
  dev image), pre-commit hooks and Python packages — weekly, grouped, with a
  7-day cooldown, always pull requests into `develop`.
- **API contract, contract-first:** `openapi/schema.yaml` is generated from the code,
  committed and checked by CI (current, style, breaking changes). It switches on by itself
  when you commit the file; see [`docs/api-workflow.md`](https://github.com/alihaidar0/django-template/blob/main/docs/api-workflow.md).

```text
django-devcontainer ──(CI builds & publishes)──▶ Docker Hub: alihaidar199527/django-devcontainer
                                                     │ pulled by
django-template ──(Use this template)──▶ your project: app · celery · celery-beat
                                          + postgres · redis · mailpit
```

| Repo | Responsibility |
| --- | --- |
| [`django-devcontainer`](https://github.com/alihaidar0/django-devcontainer) | The environment — Python, uv, tools, shell — one image for every project |
| `django-template` ← **you are here** | The project scaffold — compose stack, dev container, hooks, CI, production image |

The template contains **no Django code**: `manage.py`, `config/`, apps and
`pyproject.toml` are created per project by the steps below.

---

## 2. Create a new project — from zero to the first run

### 2.1 One-time prerequisites (your machine)

| Tool | Purpose |
| --- | --- |
| [Docker Desktop](https://docs.docker.com/get-docker/) | Runs the dev stack |
| [VS Code](https://code.visualstudio.com/) + **Dev Containers** extension (`ms-vscode-remote.remote-containers`) | Opens the project inside the container |
| An SSH key loaded in your **host** ssh-agent | `git push` from inside the container — see [Git and SSH](#git-and-ssh) |

No Python, Node.js, or database on your machine.

### 2.2 The journey at a glance

```text
Use this template ─▶ develop exists ─▶ main + labels appear (automatic, ~1 min)
      │
      ▼
clone ─▶ Reopen in Container ─▶ six commands ─▶ check it works ─▶ pin the image + write the README
      │
      ▼
topic branch ─▶ pull request into develop ─▶ GitHub setup (rulesets, settings)
```

### 2.3 Step by step

**1. Create the repository.** Click **Use this template → Create a new
repository** (leave *Include all branches* unchecked). The new repository
starts on **`develop`**, its default branch. Two workflows then run by
themselves on the first commit:

- **Bootstrap main** creates `main` from `develop`.
- **Labels** creates every label from `.github/labels.yml` and deletes GitHub's five default
  labels (`accessibility`, `help wanted`, `invalid`, `question`, `wontfix`), so the project has
  exactly the labels in that file.

Check **Actions** after a minute: both should be green. If one did not run,
start it with *Actions → the workflow → Run workflow*.

**2. Clone and open it.**

```bash
git clone git@github.com:<you>/<your-project>.git
code <your-project>
```

Click **Reopen in Container**. VS Code pulls the dev image, starts every
service, and prints the next steps in the terminal on each start.

**3. Create the Django project — six commands.**

```bash
# 1. uv project
uv init --bare

# 2. production dependencies
uv add django djangorestframework "psycopg[binary,pool]" dj-database-url \
       redis celery django-celery-beat gunicorn whitenoise drf-spectacular django-cors-headers \
       "drf-standardized-errors[openapi]"

# 3. dev / test dependencies
uv add --dev pytest pytest-django pytest-cov factory-boy faker \
             mypy ruff django-stubs celery-types flower pip-audit ipython

# 4. scaffold Django: settings split, env-driven config, Celery
bash scripts/init-django.sh

# 5. git hooks (pre-commit, commit-msg, pre-push)
pre-commit install

# 6. database and server
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

Open <http://localhost:8000>. No venv activation needed — the dev image puts
`/workspace/.venv/bin` first on `PATH`, in every container and shell. The
Celery worker and Beat start by themselves as soon as the project and its
dependencies exist.

**4. Check that everything works** — the checklist in [2.4](#24-first-run-check-that-everything-works).

**5. Freeze the toolchain and write your README** (once, inside the container):

```bash
# pin the dev image to an exact python-X.Y.Z.R build + digest in docker-compose.yml
bash scripts/pin-image.sh

# replace this template guide with a README for your project
bash scripts/init-readme.sh --description "What the project does."
```

The template follows the newest dev image; a project should not change under
you, so `pin-image.sh` freezes it. After that, Dependabot proposes newer image
builds as reviewed pull requests ([section 8](#8-versions-the-template-follows-the-newest-projects-freeze)).
`init-readme.sh` fills in the name, badges, the stack versions it really uses
and the template release the project started from.

**6. Commit on a topic branch and open the first pull request.** `develop` and
`main` are protected, so the first commit goes on a topic branch:

```bash
git switch -c chore/initial-setup
git add -A
git commit -m "feat: initial Django project"
git push -u origin chore/initial-setup
```

Open a pull request into `develop` (the default base) and merge it with
**Create a merge commit** once **CI passed** is green.

**7. Set up the GitHub repository** — settings, Actions permissions, security
and the three rulesets — with [section 3](#3-set-up-the-github-repository).

**8. Optional: publish the production image.** CI already builds and smoke-tests it on
every pull request and pushes nothing. If the project should push it to a registry, set
the repository variable `PUBLISH_IMAGE` to `ghcr` (GitHub Container Registry, no secrets)
or `dockerhub` (needs `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN`); see
[3.2](#32-settings-to-apply). Leave it unset to publish nothing, for example until you
have chosen where the project runs. Each project decides this on its own.

**9. Start the API contract** (when the project has its first endpoint). The three API CI jobs and
the release asset are off until `openapi/schema.yaml` exists, so a new project stays green:

```bash
bash scripts/schema.sh generate      # writes openapi/schema.yaml from the code
git add openapi/schema.yaml          # commit it on a topic branch with the endpoint
```

From then on every pull request checks that the schema is current, well formed and not breaking,
and every project release carries it as the `openapi.yaml` asset for the client developer. Workflow:
[`docs/api-workflow.md`](https://github.com/alihaidar0/django-template/blob/main/docs/api-workflow.md);
the client side:
[`docs/consuming-the-api.md`](https://github.com/alihaidar0/django-template/blob/main/docs/consuming-the-api.md).

### 2.4 First run: check that everything works

Open a **second terminal** in the container (the first one runs `runserver`) and
go through the list once. Every line should end as the comment says; if one does
not, see [Troubleshooting](#10-troubleshooting).

```bash
# Django and the admin
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/healthz   # 200
pmsu                                    # createsuperuser; then sign in at http://localhost:8000/admin/

# Quality gates (the same ones CI runs)
pytest                                  # "no tests ran" (exit 5) is normal on a new project
mypy .                                  # Success: no issues found
pre-commit run --all-files              # every hook Passed

# Services
psql "$DATABASE_URL" -c "select version();"   # PostgreSQL 18
redis-cli -h redis ping                 # PONG
celery -A config inspect ping           # pong from the worker (it starts a few seconds after the project exists)
python manage.py sendtestemail you@example.com   # the message appears in Mailpit: http://localhost:8025
celery -A config flower --port=5555     # Celery monitor: http://localhost:5555 (Ctrl+C to stop)

# Git
ssh -T git@github.com                   # "Hi <account>!" (with a host alias: ssh -T git@<host in your origin URL>)
git switch develop && pre-commit run no-commit-to-branch   # must FAIL: develop is protected
git switch -                            # back to your topic branch
```

| Where | What you should see |
| --- | --- |
| <http://localhost:8000> | the Django start page (debug mode) |
| <http://localhost:8000/admin/> | the admin login; your superuser signs in |
| <http://localhost:8025> | Mailpit with the test email |
| <http://localhost:5555> | Flower listing the worker |
| VS Code → Run and Debug | *Django: Dev Server* and *Django: Attach Debugger (debugpy)* |
| VS Code → Terminal → Run Task | *Pytest*, *Ruff*, *Mypy*, *Pre-commit* and *uv* tasks |

### 2.5 What the scaffold generates

`scripts/init-django.sh` generates a production-ready layout:

| Generated | What it gives you |
| --- | --- |
| `config/settings/{base,development,production,testing}.py` | settings split; configured from the environment (`SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `DATABASE_URL`, `CELERY_*`, `LOG_LEVEL` …) |
| `production.py` | HTTPS behind a proxy, secure cookies, Django 6 **Content Security Policy** (report-only until tuned) |
| WhiteNoise | static files served by Gunicorn with hashed, cacheable names |
| `config/health.py` | `GET /healthz` for platform and load-balancer probes (answers before host checks) |
| `users/` app | Custom user model (`AUTH_USER_MODEL = "users.User"`), with its first migration already generated — Django's own docs call swapping it in later "a substantial task" |
| `config/celery.py` | Celery app (`celery -A config`), `django-celery-beat` scheduler |
| API contract | `drf-spectacular` settings (schema class, title, version; the schema is **not** served) and `scripts/schema.sh` to write and verify `openapi/schema.yaml`, the file an API client builds against |
| API errors | One error body for every API error (`drf-standardized-errors`), documented in the contract so generated clients get a typed error model; `config/api_errors.py` holds the shared 400 schema |
| logging | everything to stdout, level via `LOG_LEVEL` |
| `pyproject.toml` | ruff rules (incl. bugbear, bandit, Django), **mypy strict** with django-stubs, pytest-django, coverage |
| `.github/dependabot.yml` | the Python (`uv`) ecosystem is switched on |

The generated project passes ruff, mypy strict, `makemigrations --check` and
`check --deploy` out of the box — CI runs this same script on every pull
request to prove it, then builds and probes the production image. Django 6.0 or
newer is required (the Content Security Policy middleware); the script checks.

**Want fields on `User` before the database exists?** Step 4 already generates
`users/migrations/0001_initial.py`, so step 6's `migrate` has something to apply — Django's own docs call adding
`AUTH_USER_MODEL` after that first migration "a substantial task". Before running step 6, edit `users/models.py`,
then regenerate that one migration so it already contains your fields:

```bash
rm users/migrations/0001_initial.py
python manage.py makemigrations users
```

Fields added after step 6 don't need this — that's just a normal follow-up migration.

**Teammates cloning an existing project** don't run these steps: on container
creation `scripts/post-create.sh` runs `uv sync --locked` and `pre-commit install`
automatically.

---

## 3. Set up the GitHub repository

The workflows, templates, labels, Dependabot and rulesets all ship in
`.github/`; the repository **settings** do not travel with a template, so
every project needs the steps below once. The same material, plus the
scripted version, the recovery notes and an optional Dependabot auto-merge,
is in
[`docs/github-setup.md`](https://github.com/alihaidar0/django-template/blob/main/docs/github-setup.md),
which also travels with your project.

### 3.1 Order of operations

1. **Use this template** (leave *Include all branches* unchecked). The repository starts
   with `develop` only; **Bootstrap main** and **Labels** run by themselves on the first
   commit. Check *Actions*: both green.
2. Apply the settings in [3.2](#32-settings-to-apply) (General, Actions, Advanced Security).
3. Clone, **Reopen in Container**, run the quickstart ([section 2](#2-create-a-new-project--from-zero-to-the-first-run)),
   pin the image, write the README, and merge the first pull request into `develop`.
   That run is also the first time **CI passed** exists.
4. Import the three rulesets ([3.3](#33-rulesets)). Only now: a required check that has
   never reported would block the first pull request.
5. Update `.github/CODEOWNERS` with your username and review `LICENSE`, `SECURITY.md`
   and `.github/CONTRIBUTING.md` (they describe the template until you replace them).
6. When the project has code, add **Python** to CodeQL. Optional: set `PUBLISH_IMAGE` if the
   project should push its production image to a registry.
7. Verify ([3.4](#34-check-that-it-works)).

### 3.2 Settings to apply

#### Settings → General

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

#### Settings → Actions → General

| Setting | Value |
| --- | --- |
| Actions permissions | Allow `<your user>`, and select non-`<your user>`, actions and reusable workflows |
| Allow actions created by GitHub | On |
| Allow Marketplace actions by verified creators | Off |
| Allowed actions (one per line) | `astral-sh/setup-uv@*`, `hadolint/hadolint-action@*`, `raven-actions/actionlint@*`, `zizmorcore/zizmor-action@*`, `streetsidesoftware/cspell-action@*`, `DavidAnson/markdownlint-cli2-action@*`, `docker/*`, `aws-actions/configure-aws-credentials@*` and `google-github-actions/auth@*` (never run here: they sit in optional registry-login steps inside Docker's reusable builder, and GitHub checks every action a called workflow references, so without them `docker.yml` fails at startup even when publishing is off), `aquasecurity/trivy-action@*`, `codecov/codecov-action@*`, `peter-evans/dockerhub-description@*`, `github/codeql-action/*`, `dependabot/fetch-metadata@*` (only if you enable the optional auto-merge) |
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
| `release.yml` | `contents: write` (+ `actions: write` in a project) | publish the release; start `docker.yml` on the new tag |
| `labels.yml` | `issues: write` | create the labels |
| `pr-labels.yml` | `pull-requests: write` | add labels from the PR title |
| `docker.yml` | `id-token: write`, `security-events: write` | keyless signing; upload the scan |

Workflows run on an explicit runner image (`ubuntu-26.04`) rather than
`ubuntu-latest`. GitHub moves `ubuntu-latest` on its own schedule, which
changes the toolchain under every job at once; moving to a newer image is a
deliberate edit of the `runs-on:` lines once the build has been verified.

#### Settings → Secrets and variables, and Environments

CI needs **no secrets and no environments**: it only uses the automatic
`GITHUB_TOKEN`, and it verifies the production image (build + smoke test)
without pushing it anywhere. Publishing the image to a registry is **opt-in**,
chosen per project with one repository variable (Settings → Secrets and
variables → Actions → Variables):

| `PUBLISH_IMAGE` | What happens on a merge into `main` | What to add |
| --- | --- | --- |
| *(unset — the default)* | nothing is published; `docker.yml` ends green with a notice | nothing |
| `ghcr` | the image goes to GitHub Container Registry, `ghcr.io/<owner>/<repo>` | nothing: it signs in with the automatic token. Set the package visibility under the repository's *Packages* if you want it public |
| `dockerhub` | the image goes to `docker.io/<DOCKERHUB_USERNAME>/<repo>`, and the README is synced to the Docker Hub page | variable `DOCKERHUB_USERNAME` (your namespace) and secret `DOCKERHUB_TOKEN` (an access token, Read & Write) |

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
| `production` | Selected branches → `main` only | Required reviewers (you or the team, once there is a second maintainer); Prevent self-review; Allow administrators to bypass **off** | the cloud credentials and project/region variables |
| `staging` (optional) | Selected branches → `develop` | none | the staging credentials |

Never commit a key, token or `.env` value; any file in the repository is
readable by everyone who can read the repository.

#### Settings → Advanced Security

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

### 3.3 Rulesets

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

### 3.4 Check that it works

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

---

## 4. Daily workflow: branches and pull requests

```text
feat/*  fix/*  docs/*  ci/*  chore/*  deps/*
        │  pull request, merge commit, CI passed
        ▼
     develop   ◀── Dependabot update pull requests
        │  pull request (develop → main only), merge commit
        ▼
       main    ──▶ production image published · GitHub Release
```

1. Start from an up-to-date `develop`, create a topic branch, commit, push:

   ```bash
   git switch develop
   git pull --ff-only
   git switch -c fix/example-topic
   git add path/to/changed-file
   git commit -m "fix(scope): describe the change"
   git push -u origin fix/example-topic
   ```

2. Open a pull request into **`develop`** and merge it with **Create a merge
   commit** once **CI passed** is green.
3. Once a week, review the Dependabot pull requests (they target `develop`),
   merge the ones you want, then open **one** pull request `develop` → `main`
   (title `release: <summary>`) and merge it with a merge commit. That push
   publishes the production image and the GitHub Release.

A pull request into `main` from any other branch fails **CI passed**. Squash and
rebase merges are off: squashing into `main` rewrites `develop`'s commits and
makes the next promotion conflict.

### Commit convention

Commits and pull request titles follow
[Conventional Commits](https://www.conventionalcommits.org/):
`type(scope): summary`. The title drives the labels and the release notes.

| Type | Use for | Label added |
| --- | --- | --- |
| `feat` | a new capability | `feature` |
| `fix` | a bug fix | `bug` |
| `docs` | documentation only | `documentation` |
| `ci` | workflows and automation | `ci` |
| `build` `chore` `refactor` `style` `test` `perf` `revert` `wip` | everything else | `chore` |
| `type!:` or a `BREAKING CHANGE:` footer | a change generated projects must adapt to | `breaking change` |
| scope `deps` | dependency bumps | `dependencies` |

The `develop` → `main` pull request is labelled `skip-changelog` (the pull
requests it carries are listed in the release notes already).

---

## 5. Git inside and outside the container

You can use git from the VS Code terminal in the container **or** from a terminal
or GUI on your host, even in the same session.

| | In the container | On the host |
| --- | --- | --- |
| Working tree and `.git` | the same folder (bind mount) | the same folder |
| `user.name` / `user.email` | the repository-local setting, otherwise your host `~/.gitconfig` (copied in by VS Code) | the same |
| Authentication | your host **ssh-agent**, forwarded by VS Code; no private key enters the container | your own SSH keys or credential manager |
| Hooks | **run** (pre-commit, commit-msg, pre-push) | run too, but need `pre-commit` on the host `PATH` (see below) |

### Git and SSH

Neither `~/.gitconfig` nor `~/.ssh` is mounted into the container. VS Code Dev
Containers copies your Git identity in and forwards your host **ssh-agent**, so
`git push` works without a private key ever existing inside the container. Make
sure the agent runs and holds your key *before* opening the folder in the
container:

```powershell
# Windows (once, PowerShell as Administrator): start the agent automatically, then load your key
Get-Service ssh-agent | Set-Service -StartupType Automatic
Start-Service ssh-agent
ssh-add "$env:USERPROFILE\.ssh\id_ed25519"
```

```bash
# macOS / Linux
ssh-add ~/.ssh/id_ed25519
```

Check inside the container with `ssh-add -l` and `ssh -T git@github.com`.

**Several GitHub accounts.** GitHub accepts the first key the agent offers, and
the forwarded agent holds every key you loaded, so without help a push could
authenticate as the wrong account (`Permission to owner/repo.git denied to
<other-account>`). `welcome.sh` therefore runs `scripts/pin-ssh-key.sh` on every
start. When your `origin` remote uses a host alias from your host's `~/.ssh/config`
(for example `git@github.com-work:owner/repo.git`), it finds the key in the agent
that belongs to this repository's account (the only key, else the one GitHub greets
as the repository owner, else the first one that can read it) and writes a `Host`
entry for it in the container's `~/.ssh/config`:

```text
Host github.com-work
  HostName github.com
  User git
  IdentityFile ~/.ssh/github.com-work.pub
  IdentitiesOnly yes
```

This is OpenSSH's documented way to pick one key: `IdentityFile` names the
**public** key (the private half stays in the agent on your host, so nothing secret
enters the container) and `IdentitiesOnly yes` stops ssh from offering the agent's
other keys. If you later switch keys on the host, run `scripts/pin-ssh-key.sh
--force`. Keep `origin` on the alias; do not rewrite it to `https://`.

Only aliases are pinned, because an alias names exactly one account. A plain
`git@github.com:` remote is left alone: pinning `github.com` itself would apply to
every repository the container fetches (other accounts' private repositories,
submodules). With several accounts, use an alias for the project's remote, or load
only that account's key on the host.

**Choosing the key yourself.** The automatic choice is a guess: when two of your
accounts can read the same repository (an organisation), it takes the first one that
can, and some agents ask you to approve every key it tries. To choose the key
explicitly, list the fingerprints and pin one:

```bash
ssh-add -l                                  # 256 SHA256:AbC... you@example.com (ED25519)
scripts/pin-ssh-key.sh --key SHA256:AbC...  # pins exactly that key, nothing is probed
```

The fingerprint is public. It is remembered in this clone's `.git/config`
(`devcontainer.sshkey`), so it survives container rebuilds and is not tracked. If that
key is not in the agent the script says so and pins nothing, instead of falling back
to another account's key. Go back to the automatic choice with `git config --unset
devcontainer.sshkey`.

**Identity.** Check what a commit will use, and set it for this repository only
(never `--global`) if needed; it applies on both sides:

```bash
git config user.name
git config user.email
git config --show-origin --get user.email   # which file the email comes from
git config user.email "you@example.com"
```

**Committing from the host.** The hooks installed by `pre-commit install` also
run for commits made on the host, so the host needs `pre-commit` on `PATH` too —
e.g. `uv tool install pre-commit` (Windows: `winget install astral-sh.uv`
first). Without it, host commits stop with "`pre-commit` not found".

Agent forwarding works through VS Code (or the `devcontainer` CLI), not through a
plain `docker compose exec` shell.

---

## 6. Git hooks

Installed automatically when the container is created (`post-create.sh`) and
re-checked on every start (`welcome.sh`), so the branch guards and the commit
message check protect even the very first commit; step 5 of the quickstart
(`pre-commit install`) is the same command, harmless to repeat. One config, three
hook types:

| Stage | Hook | Purpose |
| --- | --- | --- |
| `pre-commit` | `pre-commit-hooks` | whitespace, EOF, YAML/TOML/JSON validity, private keys, large files, LF endings |
| `pre-commit` | `no-commit-to-branch` | no commit on `main` or `develop` — use a topic branch |
| `pre-commit` | `ruff-check` + `ruff-format` | lint (auto-fix) and format |
| `pre-commit` | `django-upgrade` | modernise code to the project's Django version (read from `pyproject.toml`) |
| `pre-commit` | `uv-lock` | keep `uv.lock` in sync with `pyproject.toml` |
| `pre-commit` | `check-github-workflows` · `check-dependabot` | schema-validate workflow and Dependabot files |
| `commit-msg` | `conventional-pre-commit` | Conventional Commits (`feat` `fix` `chore` `docs` `style` `refactor` `perf` `test` `build` `ci` `revert` `wip`) |
| `pre-push` | `block-protected-push` | no push to an existing `main` or `develop` (creating the branch the first time still works) |

The branch guards are a local convenience; the GitHub rulesets are the real
gate. CI runs the same hooks (`pre-commit run --all-files`) and lints every
commit message of a pull request, so commits made without the hooks are caught.
Hook versions are bumped weekly by Dependabot. Run all hooks with
`pre-commit run --all-files`.

---

## 7. CI/CD: checks, releases and the production image

| Workflow | Runs on | What |
| --- | --- | --- |
| `ci.yml` → **CI passed** (required) | pull requests into `develop` and `main` | source branch · Lint · commit messages · pre-commit · template guard · tiered project jobs |
| `lint.yml` | called by `ci.yml` | hadolint · ShellCheck · actionlint · zizmor · `docker compose config` · cspell · markdownlint |
| `pr-labels.yml` | pull request opened/edited | labels from the Conventional Commit title |
| `labels.yml` | push to `develop`/`main` touching `labels.yml` | labels as code (`gh`); removes GitHub's five default labels; a manual run with `prune` removes every label not in the file |
| `bootstrap-main.yml` | push to `develop` (projects) | creates `main` once |
| `docker.yml` | push to `main`, tags, dispatch | **opt-in** (`PUBLISH_IMAGE`): publishes + scans the production image |
| `release.yml` | push to `main` | GitHub Release with generated notes |
| `dockerhub-description.yml` | push to `main` touching `README.md` | **opt-in** (`PUBLISH_IMAGE=dockerhub`): README → Docker Hub |
| `image-contract.yml` | weekly (template only) | template vs dev image: PostgreSQL, Python, image, aliases |

### CI tiers — never fails on a fresh template

| Tier | When | Runs |
| --- | --- | --- |
| 1 · template | no `pyproject.toml` | a sample project scaffolded with the quickstart steps, checked (ruff · mypy strict · pytest · migrations · deploy check) and served from the production image |
| 2 · pre-Django | `pyproject.toml` | pip-audit |
| 3 · Django | `manage.py` + `uv.lock` | mypy · pytest (PostgreSQL 18) · `check --deploy` · missing-migrations check · pip-audit · production image build + smoke test (`/healthz`, CSP header) |

The single **CI passed** gate job is the required check; jobs skipped by design
simply don't apply to the current tier.

**API contract (opt-in, any tier):** once a project commits `openapi/schema.yaml`
(`bash scripts/schema.sh generate`), three more jobs run and feed the same gate: *API schema
is current*, *API style* (Spectral, rules in `.spectral.yaml`) and *API breaking changes*
(oasdiff against the pull request's base; a breaking change needs the `breaking change` label).
They use pinned containers from `.github/api-tools/docker-compose.yml`, not GitHub Actions,
so nothing changes in the allowed-actions list. Workflow and conventions:
[`docs/api-workflow.md`](https://github.com/alihaidar0/django-template/blob/main/docs/api-workflow.md),
[`docs/api-conventions.md`](https://github.com/alihaidar0/django-template/blob/main/docs/api-conventions.md). Every action is pinned to a full commit
SHA and every workflow runs with least-privilege permissions.

### Releases

Merging `develop` → `main` publishes the production image
(`latest` + `sha-<short>`) and runs **Release**:

- in a **project**: when `version` in `pyproject.toml` is new, it publishes
  `vX.Y.Z` with notes generated from the merged pull requests (grouped by
  label, see `.github/release.yml`), attaches `openapi/schema.yaml` as the
  `openapi.yaml` asset when the project has one, and starts the image build for the
  `X.Y.Z` / `X.Y` tags. Raise the version in the release pull request.
- in the **template**: a calendar release `vYYYY.MM.DD`; documentation-only
  promotions publish nothing. The tag tells a project which state of the
  template it started from.

### Production image

Built from `docker/Dockerfile.prod` in two stages — a pinned `uv` resolves the
locked dependencies; the runtime stage is slim Python + the venv + your code,
running as the non-root user `app`:

- Static files collected at build time and served by WhiteNoise.
- `GET /healthz` → 200 for platform probes; Django 6 CSP header (report-only);
  logs to stdout.
- `scripts/entrypoint.prod.sh` runs `migrate` (skip with `RUN_MIGRATIONS=false`
  when a separate job migrates), then Gunicorn bound to `$PORT` (default 8080)
  with `$WEB_CONCURRENCY` workers (default 2); override anything via
  `GUNICORN_CMD_ARGS`.
- CI builds it and smoke-tests it (`/healthz`, CSP header, non-root user) on every
  pull request, without pushing it. When the project opts in (`PUBLISH_IMAGE`), a merge
  into `main` publishes it as `latest` + `sha-<short>`, plus `X.Y.Z` / `X.Y` when the
  **Release** workflow tags the `pyproject.toml` version.

```bash
docker run --rm -p 8080:8080 \
  -e SECRET_KEY=change-me -e DATABASE_URL=postgres://… -e ALLOWED_HOSTS=example.com \
  <registry>/<owner>/<repo>:latest        # e.g. ghcr.io/<owner>/<repo>
```

Where a project deploys is its own choice, so the template ships no deployment
workflow and assumes no host or registry. To publish the image, set the repository
variable `PUBLISH_IMAGE` to `ghcr` (no secrets) or `dockerhub` (needs
`DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN`); leave it unset and nothing is pushed.
For another registry, replace the `publish` job in `docker.yml`; add the deployment
workflow your project needs once it has chosen a host.

### Dependabot

Weekly (Monday 09:00 UTC), grouped, 7-day cooldown, every pull request into
`develop`:

| Ecosystem | Scans |
| --- | --- |
| `github-actions` | workflow action SHAs (incl. the reusable builder) |
| `docker` | `docker/Dockerfile.prod` — python base (**patch updates only**, see [section 8](#8-versions-the-template-follows-the-newest-projects-freeze)) + uv (tag + digest) |
| `docker-compose` | `docker-compose.yml` — postgres, redis, mailpit **and the dev image**; `.github/api-tools/docker-compose.yml` — the oasdiff and Spectral images once `scripts/pin-image.sh` pinned it (tag + digest) |
| `pre-commit` | `.pre-commit-config.yaml` hook revs |
| `uv` | `pyproject.toml` + `uv.lock` — enabled by `scripts/init-django.sh` |

Review them once a week, merge what you need, then promote with one
`develop` → `main` pull request.

Automatic merging of patch updates is deliberately off. The optional workflow
and its prerequisites are in
[`docs/github-setup.md`](https://github.com/alihaidar0/django-template/blob/main/docs/github-setup.md#9-optional-auto-merge-dependabot-patch-updates-not-enabled).

---

## 8. Versions: the template follows the newest, projects freeze

The template's `docker-compose.yml` pulls
`alihaidar199527/django-devcontainer:latest`, so a new project always starts on
the newest published image. The first thing a project does is **freeze it**
(`bash scripts/pin-image.sh`): the `image:` line becomes
`python-X.Y.Z.R@sha256:…`, and from then on the toolchain changes only through
a reviewed Dependabot pull request.

The image has three kinds of tags:

| Tag | Moves? | Use |
| --- | --- | --- |
| `latest` | yes | newest image of any Python minor — what the template follows |
| `python-3.14` | yes | newest image of that Python line: every tool and OS update, never a new Python minor |
| `python-3.14.8.1` (`python-X.Y.Z.R`) | **never** | one exact image: Python `X.Y.Z`, image revision `R` |

The image stays on the newest Python minor that Django supports (Django 6.1:
Python 3.12–3.14). `pin-image.sh` stays on the Python line of your container;
moving to a new minor is deliberate: `bash scripts/pin-image.sh python-X.Y.Z.R`.
Dependabot follows the image to every newer build — minor and major too — as its
own pull request; read the image's release notes before merging a new Python
minor (the weekly *Image contract* run flags a Python minor that
`docker/Dockerfile.prod` and `ci.yml` do not share).

**What the dev image provides:** `uv`/`uvx`, `pre-commit`, `watchfiles`, `git`,
`gh`, `ssh`, `psql` 18 and `pg_dump`, `redis-cli`, GNU gettext
(`makemessages`/`compilemessages`), `jq`, `vim`/`nano`, plus the libpq headers
and a C toolchain for building wheels. Project tools (ruff, mypy, Celery,
Flower, IPython) come from your `uv.lock`; run anything else ad hoc with
`uvx`, e.g. `uvx pgcli`.

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

Every other pinned version (GitHub Actions, the production image's Python and
uv, postgres/redis/mailpit, hook revisions) is tracked by Dependabot and
digest-pinned. Configuration: every value has a working default in
`docker-compose.yml`; copy `.env.example` to `.env` to override.

---

## 9. Reference

### Services

| Service | URL / port | Purpose |
| --- | --- | --- |
| Django | <http://localhost:8000> | `pmr` → `runserver 0.0.0.0:8000` |
| Mailpit | <http://localhost:8025> (SMTP `1025`) | catches every outgoing email |
| Flower | <http://localhost:5555> | `celery -A config flower --port=5555` → Celery monitor |
| PostgreSQL 18 | `localhost:5432` | database (`django` / `django_pass` / `django_dev`) |
| Redis 8 | `localhost:6379` | cache, Celery broker + results |
| debugpy | `localhost:5678` | VS Code *Django: Attach Debugger* |

Celery worker and Beat wait until `manage.py` and the project's dependencies
exist, then start on their own (the worker reloads on code changes). Host ports
are bound to `127.0.0.1` only. Each project gets its own containers, network
and volumes (named after the project folder), so several projects can run side
by side.

**Fast on Windows and macOS:** the project's `.venv` lives in a Docker volume
(Linux filesystem) instead of the bind-mounted folder, so `uv sync` and Python
imports don't pay the cross-OS file-sharing cost. It's shared by the app and
Celery containers; on your host the `.venv` folder stays empty.

### Shell aliases (from the dev image)

| Alias | Runs |
| --- | --- |
| `pm` | `python manage.py` |
| `pmr` | `runserver 0.0.0.0:8000` |
| `pms` · `pmsu` | `shell` · `createsuperuser` |
| `pmm` · `pmmk` | `migrate` · `makemigrations` |
| `pmcs` · `pmt` | `collectstatic` · `test` |
| `uvs` `uva` `uvr` `uvl` `uvf` | `uv sync` · `add` · `remove` · `pip list` · `pip freeze` |
| `gs` `ga` `gc` `gp` `gl` `gco` `gb` | git status · add · commit · push · log · checkout · branch |
| `ll` `la` `cls` | listing and clear |

### Repository structure

```text
.
├── .devcontainer/devcontainer.json   # VS Code: compose service `app`, extensions, settings
├── .github/
│   ├── workflows/
│   │   ├── ci.yml                    # pull request checks — required check "CI passed"
│   │   ├── lint.yml                  # repo linters, called by ci.yml
│   │   ├── pr-labels.yml             # labels from the Conventional Commit title
│   │   ├── labels.yml                # labels.yml → GitHub labels
│   │   ├── bootstrap-main.yml        # projects: creates `main` from `develop` once
│   │   ├── release.yml               # GitHub Release (template: vYYYY.MM.DD, project: pyproject version)
│   │   ├── docker.yml                # publish + scan the production image
│   │   ├── dockerhub-description.yml # README → Docker Hub
│   │   └── image-contract.yml        # template only: weekly check against the dev image
│   ├── api-tools/docker-compose.yml  # pinned oasdiff + Spectral images for the API-contract CI jobs
│   ├── rulesets/                     # importable rulesets: protect-main · protect-develop · protect-tags
│   ├── scripts/                      # check-image-contract.sh
│   ├── ISSUE_TEMPLATE/ · CODEOWNERS · CODE_OF_CONDUCT.md · CONTRIBUTING.md · SECURITY.md · pull_request_template.md
│   └── actionlint.yaml · dependabot.yml · labels.yml · release.yml
├── .vscode/launch.json · tasks.json  # debug configs + task shortcuts
├── docs/                             # github-setup.md · api-workflow.md · api-collaboration.md · consuming-the-api.md · api-conventions.md
├── .spectral.yaml                    # API style rules (Spectral)
├── docker/Dockerfile.prod            # production image
├── scripts/
│   ├── init-django.sh                # step 4: scaffold config/ + settings + Celery + tool config
│   ├── pin-image.sh                  # freeze the dev image to python-X.Y.Z.R@sha256:…
│   ├── set-python.sh                 # move the production image to the newest Python minor Django supports
│   ├── schema.sh                     # generate / check openapi/schema.yaml (the API contract)
│   ├── init-readme.sh                # replace this guide with your project's README
│   ├── post-create.sh                # container created: .venv volume owner, uv sync, git hooks
│   ├── pin-ssh-key.sh                # every start: map the remote alias to github.com, pin this repo's SSH key
│   ├── block-protected-push.sh       # pre-push guard: no direct push to main or develop
│   ├── entrypoint.prod.sh            # migrate → Gunicorn
│   └── welcome.sh                    # next-steps banner on container start
├── .env.example · .dockerignore · .gitignore · .gitattributes · .editorconfig
├── .pre-commit-config.yaml           # pure-Python hooks
├── .hadolint.yaml · .markdownlint-cli2.jsonc · cspell.json · .cspell/
├── docker-compose.yml                # dev stack
└── LICENSE · README.md
```

---

## 10. Troubleshooting

### `git push` fails — permission denied (publickey)

Keys come from your host's ssh-agent (see [Git and SSH](#git-and-ssh)). Inside the container:

```bash
ssh-add -l            # the agent must list your key; empty = nothing forwarded
ssh -T git@github.com # verify authentication
```

If the list is empty, start the agent and `ssh-add` your key on the host, then
reload the VS Code window (`post-create.sh` also warns when the container sees no keys).

### `git push` fails — `denied to <another-account>`

The agent holds keys of several accounts and ssh used the wrong one. The container
pins the right key on every start (see [Git and SSH](#git-and-ssh)). If the start
printed a notice that it could not tell which key belongs to the repository, or it
pinned the wrong one, choose the key yourself (`ssh-add -l` lists the fingerprints):

```bash
scripts/pin-ssh-key.sh --key SHA256:...
cat ~/.ssh/config            # the Host entry must show IdentityFile and IdentitiesOnly yes
ssh -T git@<host-of-origin>  # "Hi <account>!" must name the repository's account
```

`Could not resolve hostname github.com-…` means the remote alias was not pinned
yet: reopen the container (or run `scripts/pin-ssh-key.sh`) with the key in the
host's agent.

### Commit rejected — "don't commit to branch"

You are on `develop` or `main`. Create a topic branch first:
`git switch -c feat/<topic>`, then commit again (the staged changes come along).

### Push rejected — "Direct push to 'develop' is blocked"

Push your topic branch instead (`git push -u origin <branch>`) and open a pull
request into `develop`. The first push that *creates* `develop` or `main` on an
empty remote is allowed.

### Commit rejected — invalid commit message

Use `type(scope): summary` with a type from the table in
[section 4](#commit-convention). `Fixed stuff` fails; `fix: handle empty input`
passes.

### Git hooks do not run

Run `pre-commit install` once in the project (hooks live in `.git/hooks`, which
is not versioned). From the host, `pre-commit` must be on `PATH` too.

### CI warns "Dev image is not pinned"

Run `bash scripts/pin-image.sh` inside the container and commit
`docker-compose.yml`.

### Upgrading an older project (root-based dev image)

Projects created before the dev image switched to the non-root user `dev`
need the current versions of `docker-compose.yml`,
`.devcontainer/devcontainer.json` and `scripts/welcome.sh` from this template,
and `scripts/entrypoint.dev.sh` deleted.

### Existing PostgreSQL data after the PostgreSQL 18 volume fix

The volume now mounts at `/var/lib/postgresql` (PostgreSQL 18 layout). Old dev
databases lived in an anonymous volume and are not carried over — re-run
`python manage.py migrate` (and reload fixtures) after `docker compose down -v`.

---

## 11. Maintaining the template itself

The template is a project like any other: topic branch → pull request into
`develop` → release pull request into `main`.

- **Blank canvas:** the *Template guard* job fails if `manage.py`, `config/`,
  `pyproject.toml`, `uv.lock` or `package.json` appear. Django code is created
  per project by `scripts/init-django.sh`.
- **Quickstart in three places:** the README, `scripts/welcome.sh` and the CI
  scaffold step run the same commands — change them together. CI scaffolds a
  sample project with them on every pull request.
- **No secrets, no variables:** the template publishes nothing, so its *Settings → Secrets and
  variables* pages stay empty and `PUBLISH_IMAGE` stays unset. Registry credentials belong
  to each project, never to the template.
- **Image contract:** the weekly *Image contract* run compares the template with
  the dev image. A red run means the image or the template moved on (for example a new Python
  minor in the dev image); fix it with a topic branch, using `bash scripts/set-python.sh` for Python.
- **Releases:** each promotion to `main` that changes more than documentation
  publishes a calendar release (`vYYYY.MM.DD`) that projects record as their
  starting point.

- **Settings tables in two places:** the GitHub settings, Actions permissions, environments,
  Advanced Security and ruleset tables appear in this README (section 3) and in
  `docs/github-setup.md`; change them together.

Everything about branches, rulesets and settings is in
[`docs/github-setup.md`](https://github.com/alihaidar0/django-template/blob/main/docs/github-setup.md);
contribution rules are in
[`.github/CONTRIBUTING.md`](https://github.com/alihaidar0/django-template/blob/main/.github/CONTRIBUTING.md).

---

## License

[MIT](https://github.com/alihaidar0/django-template/blob/main/LICENSE) © Ali Haidar

## Security

Report vulnerabilities privately — see
[`SECURITY.md`](https://github.com/alihaidar0/django-template/blob/main/.github/SECURITY.md).

## Related repositories

- [`django-devcontainer`](https://github.com/alihaidar0/django-devcontainer) — the shared dev image this template pulls.
