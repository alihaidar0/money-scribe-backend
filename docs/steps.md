# Project steps

A living checklist for Money Scribe's backend, from the empty template to a released API. Tick a box
(`- [x]`) when a step is done. Steps run top to bottom; each section says where it runs.

| Where | Meaning |
| --- | --- |
| **Host** | your Windows machine (browser, Docker Desktop, VS Code, PowerShell) |
| **Container** | the VS Code terminal inside the dev container (Linux) |
| **GitHub** | the repository's web settings |

Commits and pushes use the owner's identity and the remote alias `github.com-freelance`
(`git@github.com-freelance:alihaidar0/money-scribe-backend.git`). Never commit on `develop` or `main`.

## 0. Prerequisites (Host, once per machine)

- [x] Docker Desktop installed and running
- [x] VS Code installed with the **Dev Containers** extension (`ms-vscode-remote.remote-containers`)
- [x] The SSH agent runs and holds the key for the `alihaidar0` account (PowerShell as Administrator):

  ```powershell
  Get-Service ssh-agent | Set-Service -StartupType Automatic
  Start-Service ssh-agent
  ssh-add "$env:USERPROFILE\.ssh\id_ed25519_freelance"
  ```

- [x] The host `~/.ssh/config` has the `github.com-freelance` alias pointing at that key
- [ ] Optional, for commits made from the host: `pre-commit` on the host `PATH` (`uv tool install pre-commit`)

## 1. Repository from the template (GitHub)

- [x] Repository created with **Use this template → Create a new repository**, *Include all branches* left unchecked
  (the new repository starts on `develop`, its default branch)
- [x] **Actions** shows **Bootstrap main** and **Labels** green, so `main` and the labels exist
  (if one did not run: Actions → the workflow → *Run workflow*)
- [x] `main` exists on the remote (`git ls-remote --heads origin`)
- [x] Labels match `.github/labels.yml` exactly (GitHub's five default labels are deleted by the workflow)

Apply these repository settings now, before the first clone (full tables and the scripted alternative:
[`github-setup.md`](github-setup.md)). The rulesets come later, in section 6.

- [x] Settings → General:
  - [x] Default branch `develop`; Issues on; Wikis, Projects, Discussions and Sponsorships off
  - [x] The *Template repository* box stays **off**
  - [x] Pull Requests: merge commits on (default message: pull request title and description); squash merging off;
    rebase merging off; always suggest updating pull request branches on; auto-merge on; automatically delete head
    branches on; forking off (private projects)
  - [x] Releases: release immutability on (if offered)
  - [x] Pages off; no webhooks and no Codespaces prebuilds
- [x] Settings → Actions → General:
  - [x] Actions permissions: allow `alihaidar0`, and select non-`alihaidar0`, actions and reusable workflows; GitHub
    actions allowed; Marketplace actions by verified creators off; the allowed-actions list from
    [`github-setup.md`](github-setup.md) filled in
  - [x] Require actions to be pinned to a full-length commit SHA: **on**
  - [x] Artifact and log retention 30 days
  - [x] Fork pull request workflows from outside collaborators: require approval for all external contributors;
    send write tokens and secrets to fork pull request workflows off
  - [x] Workflow permissions: **read repository contents and packages**; allow GitHub Actions to create and approve
    pull requests off
  - [x] Runners: GitHub-hosted only
- [x] Settings → Advanced Security:
  - [x] Dependency graph, Dependabot alerts, Dependabot malware alerts, Dependabot security updates and grouped
    security updates on
  - [x] Private vulnerability reporting **on**
  - [x] Code scanning → CodeQL default setup, query suite **Default**, language **Actions** (Python is added in
    section 6)
  - [x] Secret scanning, push protection (**on**) and validity checks on

## 2. Clone and open in the container

- [x] Repository cloned to the host
- [x] Open the folder in VS Code (Host): `code money-scribe-backend`
- [x] VS Code command palette → **Dev Containers: Reopen in Container** (first start pulls the dev image and starts
  app, celery, celery-beat, postgres, redis and mailpit)
- [ ] The terminal prints the next-steps banner; `post-create.sh` finished without errors
- [x] Git works inside the container (Container):

  ```bash
  ssh-add -l                       # lists your key
  ssh -T git@github.com-freelance  # "Hi alihaidar0!"
  git config user.name             # Ali Haidar
  git config user.email            # alihaidar199527@gmail.com
  git remote -v                    # git@github.com-freelance:alihaidar0/money-scribe-backend.git
  ```

- [x] Git hooks are installed (`ls .git/hooks/pre-commit .git/hooks/pre-push`; otherwise run `pre-commit install`)

## 3. Create the Django project (Container)

- [x] Decide the `User` fields now, before the first migration: the client's spec is recorded in
  [`adr/0001-user-model.md`](adr/0001-user-model.md); see 3.2

### 3.1 The first five commands

- [x] 1. Initialise uv

  ```bash
  uv init --bare
  ```

- [x] 2. Production dependencies

  ```bash
  uv add django djangorestframework "psycopg[binary,pool]" dj-database-url \
         redis celery django-celery-beat gunicorn whitenoise drf-spectacular django-cors-headers \
         "drf-standardized-errors[openapi]"
  ```

- [x] 3. Dev and test dependencies

  ```bash
  uv add --dev pytest pytest-django pytest-cov factory-boy faker \
               mypy ruff django-stubs celery-types flower pip-audit ipython
  ```

- [x] 4. Scaffold Django (settings split, custom `users.User`, Celery, `/healthz`, tool config)

  ```bash
  bash scripts/init-django.sh
  ```

- [x] 5. Install the git hooks (pre-commit, commit-msg, pre-push; the container already installs them on creation, so
  repeating it is harmless)

  ```bash
  pre-commit install
  ```

### 3.2 Optional: fields on `User` before the first migration

Command 4 already generated `users/migrations/0001_initial.py`. Swapping the user model later is hard, so add the
fields first and regenerate that one migration, before command 6 runs `migrate`. Fields added after command 6 need
no regeneration; they are a normal follow-up migration.

- [x] Edit `users/models.py`, `users/managers.py`, `users/admin.py`, `users/validators.py` and the password validators
  in `config/settings/base.py` to match the spec
- [x] Regenerate the first migration:

  ```bash
  rm users/migrations/0001_initial.py
  python manage.py makemigrations users
  ```

### 3.3 The sixth command: database and server

- [ ] 6. Database and server (after 3.2 if `User` needs fields)

  ```bash
  python manage.py migrate
  python manage.py runserver 0.0.0.0:8000
  ```

- [ ] Open <http://localhost:8000> (the Django start page in debug mode). No venv activation is needed: the dev image
  puts `/workspace/.venv/bin` first on `PATH`. The Celery worker and Beat start by themselves once the project and its
  dependencies exist.

### 3.4 First run: check that everything works

Open a second terminal in the container (the first one runs the server).

- [ ] `curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/healthz` prints `200`
- [ ] `pmsu` creates a superuser; sign in at <http://localhost:8000/admin/>
- [ ] `pytest` passes (the user model tests; on a project with no tests, exit 5 "no tests ran" is normal)
- [ ] `mypy .` prints `Success: no issues found`
- [ ] `ruff check . && ruff format --check .` pass
- [ ] `pre-commit run --all-files` passes every hook
- [ ] `psql "$DATABASE_URL" -c "select version();"` shows PostgreSQL 18
- [ ] `redis-cli -h redis ping` prints `PONG`
- [ ] `celery -A config inspect ping` answers `pong` (the worker starts a few seconds after the project exists)
- [ ] `python manage.py sendtestemail you@example.com` shows the message in Mailpit at <http://localhost:8025>
- [ ] `celery -A config flower --port=5555` lists the worker at <http://localhost:5555> (Ctrl+C to stop)
- [ ] `python manage.py check --deploy` has no errors that matter for the current stage
- [ ] `ssh -T git@github.com-freelance` greets `alihaidar0` (this repository's remote alias)
- [ ] `git switch develop && pre-commit run no-commit-to-branch` **fails** (develop is protected); then `git switch -`

What you should see:

| Where | What you should see |
| --- | --- |
| <http://localhost:8000> | the Django start page (debug mode) |
| <http://localhost:8000/admin/> | the admin login; your superuser signs in |
| <http://localhost:8025> | Mailpit with the test email |
| <http://localhost:5555> | Flower listing the worker |
| VS Code → Run and Debug | *Django: Dev Server* and *Django: Attach Debugger (debugpy)* |
| VS Code → Terminal → Run Task | *Pytest*, *Ruff*, *Mypy*, *Pre-commit* and *uv* tasks |

## 4. Freeze the toolchain and write the README (Container)

- [x] Pin the dev image to an exact build (changes only `docker-compose.yml`):

  ```bash
  bash scripts/pin-image.sh
  ```

- [x] Replace the template guide with this project's README:

  ```bash
  bash scripts/init-readme.sh --description "Django REST backend for Money Scribe, a personal finance tracker."
  ```

- [ ] Review the generated `README.md`: name, description, stack table, template release
- [ ] Copy `.env.example` to `.env` only if a value must be overridden locally (`.env` is never committed)

## 5. First commit and pull request (Container)

`develop` and `main` are protected, so the first commit goes on a topic branch. Stage explicit paths, never `git add -A`.

- [ ] Review what changed: `git status --short`
- [ ] Branch, identity check, stage, commit, push (three logical commits):

  ```bash
  git switch develop && git pull --ff-only origin develop
  git switch -c feat/initial-django-project

  git config user.name
  git config user.email
  git config --show-origin --get user.email

  git add docker-compose.yml
  git commit -m "chore: pin the dev image"

  git add README.md
  git commit -m "docs: replace the template guide with the project README"

  git add pyproject.toml uv.lock manage.py config users .github/dependabot.yml \
          docs/adr/0001-user-model.md docs/steps.md
  git status --short
  git diff --cached --stat
  git commit -m "feat: add Django project with an email-based user model"

  git push -u origin feat/initial-django-project
  ```

  If the commit stops because a hook fixed files, run `git add` for those paths again and commit again.

- [ ] Pull request `feat/initial-django-project` → `develop` opened (title in Conventional Commits form)
- [ ] **CI passed** is green (this is the first time the check exists)
- [ ] Merged with **Create a merge commit**
- [ ] Local clean-up after the merge: switch to `develop`, `git pull --ff-only origin develop`, delete the topic branch

## 6. Rulesets and final GitHub setup (GitHub)

General, Actions and Advanced Security settings were applied in section 1. Full tables and the scripted alternative:
[`github-setup.md`](github-setup.md).

- [x] Import the three rulesets from `.github/rulesets/` (`protect-main`, `protect-develop`, `protect-tags`),
  enforcement **Active**, empty bypass list. Only after **CI passed** has run once: a required check that has never
  reported would block the first pull request.
- [ ] `.github/CODEOWNERS` lists the right owners; review `LICENSE`, `.github/SECURITY.md`, `.github/CONTRIBUTING.md`
- [ ] CodeQL languages: **Actions**, and **Python** now that the project has code (Settings → Advanced Security →
  CodeQL analysis → Edit configuration)
- [ ] Optional: publish the production image. CI builds and smoke-tests it on every pull request and pushes nothing.
  To publish, set the repository variable `PUBLISH_IMAGE` to `ghcr` (no secrets) or `dockerhub` (variable
  `DOCKERHUB_USERNAME` and secret `DOCKERHUB_TOKEN`). Leave it unset to publish nothing.
- [ ] Optional: secret `CODECOV_TOKEN` enables the coverage upload. CI needs no other secrets and no environments;
  create environments only once a deployment workflow exists.
- [ ] Verify the protection:
  - [ ] a direct push to `develop` or `main` is rejected (the pre-push hook also rejects it locally)
  - [ ] a pull request from a topic branch into `main` fails **Verify source branch**, so **CI passed** is red
  - [ ] a pull request from `develop` into `main` offers only **Create a merge commit**
  - [ ] a pull request into `develop` offers only **Create a merge commit** and waits for **CI passed**
  - [ ] a commit message such as `Fixed stuff` fails **Commit messages**
  - [ ] Security → Code scanning lists CodeQL for *Actions* (and *Python* once added); Security → Dependabot lists no
    errors

## 7. Decisions to settle before the API (with the owner)

These are listed as open in the project notes; settle each one before the endpoint that depends on it.

- [ ] Wire format of money (proposal: decimal string next to a currency code, stored as integer minor units)
- [ ] Token and auth scheme (session, token, JWT, ...)
- [ ] Who owns the salary calculation: server or client
- [ ] Push notification provider wiring (FCM device tokens)
- [ ] Error status codes to document beyond the default 400, 401, 403, 404 and 429, for example `409` for a conflict
  (`DRF_STANDARDIZED_ERRORS["ALLOWED_ERROR_STATUS_CODES"]`)
- [ ] Pagination style per list
- [ ] Django app split (suggested: `users`, `ledger`, `salary`, `debts`, `reports`, `preferences`, `notifications`)
- [ ] Hosting target for staging and production (needed only for the deployment step, section 11)

## 8. Project foundations (Container, one topic branch per item)

Each item is its own branch and pull request. API work is contract-first: see [`api-workflow.md`](api-workflow.md) and
[`api-conventions.md`](api-conventions.md).

- [ ] `feat/api-foundation`: `/api/v1/` routing, the one pagination class, the shared error body (`{type, errors[]}`,
  see `api-conventions.md`), default permissions (authenticated, every query scoped to `request.user`)
- [ ] `feat/money-core`: money value type (integer minor units, currency table, half-up rounding, cross-currency error)
  with unit tests
- [ ] Test foundation: a per-user isolation test helper (factory-boy is set up, with `UserFactory`)
- [ ] First `openapi/schema.yaml` generated and committed on a topic branch with the first endpoint
  (`bash scripts/schema.sh generate`). This switches on the three API CI gates (schema current, style, breaking
  changes) and makes every release carry the schema as the `openapi.yaml` asset.
- [ ] `.github/CODEOWNERS`: add the client owner next to `/openapi/` and `/.spectral.yaml`
- [ ] `docs/` extended where a decision changed a convention

## 9. Features (roadmap)

Order is a proposal. For each: agree the request/response shape, implement with tests, regenerate the schema, open the
pull request with the `api-change` label.

- [ ] **users**: register with 4-digit email code, verify (signs in), sign in with lockout, sign out, profile,
  change password, password reset (request, verify, set), delete account
- [ ] **preferences**: default currency, pay schedule, notification preferences, push device tokens
- [ ] **ledger**: accounts (delete refused when transactions exist), categories (built-ins read-only, custom ones),
  transactions (category must allow the kind)
- [ ] **salary**: salary months with history, payment mode kept per month, month comparison
- [ ] **debts**: debts with repayments, derived `repaid`, `remaining`, `is_settled`, `progress`, status
- [ ] **reports**: month overview, category spend, month comparison, debts report, period report
- [ ] **CSV export and import** of transactions, debts and salary months, with skipped-row reporting
- [ ] **notifications**: pay-day reminders through Celery Beat and the push provider

## 10. Before the first release

- [ ] `pytest`, `mypy .`, `ruff check .`, `pre-commit run --all-files` and `bash scripts/schema.sh check` are clean
- [ ] `python manage.py check --deploy` passes with production settings
- [ ] Production image builds and answers `/healthz` (CI smoke test is green)
- [ ] `version` in `pyproject.toml` is the version to release (the draft release is named after it)
- [ ] One pull request `develop` → `main`, titled `release: <summary>`, merged with a merge commit
- [ ] The draft release `v<version>` is correct: notes grouped by label and the `openapi.yaml` asset attached
- [ ] The draft is published from the Releases page (**Publish release**; the release is then immutable) and the client
  developer is told the tag

A merge into `main` never publishes by itself: it creates or refreshes the draft `v<version>`. A merge into `develop`
refreshes the rolling draft "Staging", which is never published. See
[`adr/0002-draft-releases-and-environments.md`](adr/0002-draft-releases-and-environments.md).

## 11. Deployment (later, a separate branch)

`develop` is staging and `main` is production. Nothing here is set up yet and the draft releases need none of it. Do it
in this order, once the first real endpoints exist (staging first, production when a release candidate is stable).

- [ ] Hosting target chosen (app, PostgreSQL, Redis, Celery worker and Beat), with separate staging and production
  instances
- [ ] GitHub environments created: `staging` (deployment branches: `develop`) and `production` (deployment branches:
  `main`; a required reviewer once there is a second maintainer; administrators cannot bypass), each holding its own
  secrets and variables
- [ ] Image publishing on (`PUBLISH_IMAGE`), and an image built from `develop` for staging
- [ ] Deploy workflows: staging after **CI passed** on `develop`, production on the published release or a merge into
  `main`; deploy by image digest, run `migrate` once, probe `/healthz`
- [ ] Secrets and production environment variables prepared (never in the repository)
- [ ] Backups in place before production holds real financial data; staging holds invented data only

## 12. Routine (every time)

- [ ] Sync before starting: fetch, fast-forward `main` and `develop`, switch to `develop`, remove merged local branches
- [ ] Weekly: review Dependabot pull requests (they target `develop`), merge the ones you want
- [ ] Weekly: open one `develop` → `main` release pull request when `develop` is verified
- [ ] After a merge into `main`: check the draft release `v<version>` and publish it only when you want the release
- [ ] When `django-celery-beat` supports Django 6.1 (its releases require `Django<6.1` today): remove the `django`
  ignore entry in `.github/dependabot.yml` and let Dependabot propose the Django 6.1 update
