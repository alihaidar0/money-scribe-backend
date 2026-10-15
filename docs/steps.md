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

Each decision has a record in [`adr/`](adr/README.md). Settle the open ones before the endpoint that depends on them.

- [x] Wire format of money: a decimal string next to a currency code, chosen by the Flutter developer,
  [ADR 0008](adr/0008-money-on-the-wire.md)
- [x] Token and auth scheme: a short-lived JWT access token and a rotating refresh token with a 30 second replay window,
  an `HttpOnly` cookie on the web and a session check on every request,
  [ADR 0006](adr/0006-authentication-tokens.md)
- [x] Sessions and device records, with their retention and privacy list,
  [ADR 0009](adr/0009-sessions-and-devices.md)
- [x] Who owns the salary calculation: the server, [ADR 0007](adr/0007-salary-calculation-on-the-server.md)
- [x] Error status codes and the initial error-code registry, with `Retry-After`,
  [ADR 0004](adr/0004-error-status-codes.md)
- [x] Pagination: one keyset cursor class for every list, with filters and day totals,
  [ADR 0003](adr/0003-cursor-pagination.md)
- [x] Django app split and model conventions, [ADR 0005](adr/0005-apps-and-model-conventions.md)
- [x] The admin panel and a database email outbox for development and staging, with real email delivery and the admin
  hardening left for the production step, [ADR 0010](adr/0010-admin-panel-and-email-outbox.md)
- [x] Open questions answered with the Flutter developer: the balance rule for fixed items, built-in category keys, the
  `today` parameter, the replay window, day totals, the session fields, the email change, and the user's `time_zone` and
  `language` ([`api-fields.md`](api-fields.md))
- [ ] The web cookie needs a shared domain: the developer agrees in principle and waits for the owner's choice of domain
  (sections 9 and 12)
- [ ] Push notification provider wiring (FCM device tokens): later, with the `notifications` feature
- [ ] Staging hosting and email delivery: decided in stage 2 (section 9). Production hosting and the shared domain of the
  web app and the API: later (section 12)

## 8. Stage 1: the auth layer (one topic branch per step)

Only authentication comes first: register, verify, sign in, refresh, sign out and everything around them. Each step is its
own branch and pull request, and API work is contract-first: agree the request and response shapes with the Flutter
developer in a request issue, implement with tests (including a test that another user cannot reach the data, where it
applies), regenerate `openapi/schema.yaml`, and open the pull request with the `api-change` label. The shapes are fixed in
[`api-fields.md`](api-fields.md), [`api-conventions.md`](api-conventions.md) and the records in [`adr/`](adr/README.md);
the process is in [`api-workflow.md`](api-workflow.md).

- [ ] **8.1 `feat/api-foundation`**: `/api/v1/` routing; the shared error handling with `409` documented, `Retry-After` on
  `429` and the error-code registry (ADR 0004); default permissions (authenticated, every query scoped to
  `request.user`); the bearer security scheme in the schema; a per-user isolation test helper;
  `.github/CODEOWNERS` gets the client owner next to `/openapi/` and `/.spectral.yaml`; **the admin panel**
  (`ADMIN_URL`, staff only, the users admin ready, superuser creation with email and name); and **the email outbox**
  (the `outbox` app: a database email backend, a read-only admin to copy a 4-digit code from, a daily purge, a deploy
  check that fails if production uses it), with tests (ADR 0010)
- [ ] **8.2 `feat/auth-register-signin`**: the `Session` model and the `device` object (ADR 0009); register, verify email
  (this call creates the session) and resend the code; sign in; refresh with the 30 second replay window; sign out; the
  session check on every request; throttling and the sign-in lock with `Retry-After`; hashed 4-digit codes, sent through
  Mailpit in development; Argon2id password hashing; `language` and `time_zone` stored (where they live is decided in this
  step; the proposal is a settings row of `preferences`, created with the user); the signing key, lifetimes and replay
  window in `.env.example` and `docker-compose.yml`. **The first `openapi/schema.yaml` is generated and committed here**
  (`bash scripts/schema.sh generate`), which switches on the three API CI gates (schema current, style, breaking changes)
  and makes the draft release carry the schema as `openapi.yaml`. The Flutter developer can start on the register and
  sign-in screens from this contract
- [ ] **8.3 `feat/auth-account`**: profile (read, edit the name), change password, password reset (request, verify, set),
  email change (request, confirm, cancel) and delete account
- [ ] **8.4 `feat/auth-sessions`**: the keyset cursor class (`config/pagination.py`, ADR 0003) with its tests; list the
  sessions, end one, end all others; the purge task for ended sessions (Celery Beat, 90 days); the new-device email
- [ ] **8.5 `feat/auth-web-cookie`**: the web form of the tokens: the `HttpOnly` cookie, `X-Client: web`, the `Origin`
  check, and CORS with credentials for an explicit list of origins (ADR 0006), tested on `localhost`. A shared domain is
  needed only when the web build is tested against staging
- [ ] `docs/` updated wherever a step changed a convention
- [ ] Stage 1 is done when the five steps are merged, the schema covers every auth operation, and the Flutter developer
  has the screens working against it locally

## 9. Stage 2: staging deployment and the first test in the Flutter app

`develop` is staging. Choose the simplest hosting that works for a phone and the web build; production comes later
(section 12). Staging holds invented data only. Each choice gets a short record in [`adr/`](adr/README.md).

- [ ] **9.1 Choose the staging hosting** and record it: it must run the app, PostgreSQL 18, Redis 8 and the Celery worker
  and Beat, with a public HTTPS address, environment variables, an image pulled from the registry, deploys by image
  digest, and a low cost
- [ ] **9.2 Codes on staging through the outbox**: staging sends no real email yet. It uses the email outbox
  (`EMAIL_BACKEND` set to the database backend), and you copy each 4-digit code from the emails in the admin panel at the
  staging `ADMIN_URL` (ADR 0010). Real delivery is a separate step before production (section 12)
- [ ] **9.3 The GitHub environment `staging`** (deployment branches: `develop`) with its own secrets and variables;
  image publishing on (`PUBLISH_IMAGE`); an image built from `develop` (tags `staging` and `sha-<short>`)
- [ ] **9.4 The staging deploy workflow**: runs after **CI passed** on `develop`; deploys by image digest, runs `migrate`
  once, probes `/healthz`, and shows the deployed digest in its summary
- [ ] **9.5 The first contract release**: check that `version` in `pyproject.toml` is the version to release, open the
  release pull request (`develop` → `main`), check the draft `v<version>` (notes and the `openapi.yaml` asset), and
  publish it so the Flutter developer pins that tag ([`consuming-the-api.md`](consuming-the-api.md)); or pin a commit and
  wait, if you prefer
- [ ] **9.6 The first test in the app**: the staging flavor points at the staging address. Run this list and record the
  result:
  - register, copy the code from the outbox in the admin panel, verify, and land in the app; sign out and sign in again
  - a wrong password, then the sign-in lock with its countdown (`Retry-After`)
  - the access token expires after 10 minutes and the app refreshes without asking the user
  - a lost response on refresh (airplane mode) recovers within 30 seconds
  - password reset, change password, change email, delete account
  - a session ended from another device shows the reason from its `session_*` code
- [ ] Gate: start stage 3 only when the app works against staging. Fix what the test found first.

## 10. Stage 3: the features, one at a time (salary first)

Each feature is a loop: agree the shapes in a request issue, implement with tests, regenerate the schema, open the pull
request with the `api-change` label, merge, let staging deploy by itself, **test it in the Flutter app**, then move to the
next. A decision a feature needs gets its record first. The order below is the owner's: salary first, then the others.

- [ ] **10.1 `feat/money-core`** (before salary): `common/money.py`, the money value type (integer minor units, currency
  table, half-up rounding, cross-currency error) with unit tests, decimal-string parsing and writing in the form of ADR
  0008, and the shared `Amount` and `SignedAmount` serializer field
- [ ] **10.2 salary**: salary months computed on the server (ADR 0007), addressed by `YYYY-MM` with `PUT`; fixed items
  inline; balances per part and for the month; the history list with each result (the cursor class exists since 8.4);
  payment mode kept per month; month comparison. The worked examples of ADR 0007 become unit tests
- [ ] **10.3 preferences**: default currency, default payment mode, pay days and `remind_on_pay_days` (`time_zone` and
  `language` exist since 8.2)
- [ ] **10.4 ledger**: accounts (delete refused with `409` when transactions exist); categories (the built-in rows with
  their keys, read-only; custom ones); transactions (the category must allow the kind; ordered `-date`, `-created_at`,
  `-id` with the filters, through `django-filter`)
- [ ] **10.5 debts**: debts with repayments, derived `repaid`, `remaining`, `is_settled`, `progress` and `status`, with the
  `today` parameter
- [ ] **10.6 reports**: month overview, day totals, category spend, month comparison, debts report and period report, with
  the `part` filter
- [ ] **10.7 CSV export and import** of transactions, debts and salary months, with skipped-row reporting and a lossless
  round trip
- [ ] **10.8 notifications**: push device tokens (one per session, removed when it ends), pay-day reminders through Celery
  Beat, and the push provider wiring (the last open decision for this stage)

## 11. Before the first production release

- [ ] `pytest`, `mypy .`, `ruff check .`, `pre-commit run --all-files` and `bash scripts/schema.sh check` are clean
- [ ] `python manage.py check --deploy` passes with production settings
- [ ] Production image builds and answers `/healthz` (CI smoke test is green)
- [ ] Staging has run the same build, and the Flutter app has been tested against it
- [ ] `version` in `pyproject.toml` is the version to release (the draft release is named after it)
- [ ] One pull request `develop` → `main`, titled `release: <summary>`, merged with a merge commit
- [ ] The privacy policy and the Google Play Data safety form list exactly what
  [ADR 0009](adr/0009-sessions-and-devices.md) stores about sessions and devices, with its retention
- [ ] The draft release `v<version>` is correct: notes grouped by label and the `openapi.yaml` asset attached
- [ ] The draft is published from the Releases page (**Publish release**; the release is then immutable) and the client
  developer is told the tag

A merge into `main` never publishes by itself: it creates or refreshes the draft `v<version>`. A merge into `develop`
refreshes the rolling draft "Staging", which is never published. See
[`adr/0002-draft-releases-and-environments.md`](adr/0002-draft-releases-and-environments.md).

## 12. Production deployment (later, a separate branch)

`main` is production. Nothing here is set up yet. It follows the staging deployment of section 9 and is done when a
release candidate is stable.

- [ ] Production hosting chosen, with its own PostgreSQL, Redis, Celery worker and Beat, separate from staging
- [ ] The GitHub environment `production` (deployment branches: `main`; a required reviewer once there is a second
  maintainer; administrators cannot bypass) with its own secrets and variables
- [ ] One registrable domain shared by the web app and the API (for example `app.<domain>` and `api.<domain>`), for staging
  and for production, because the web refresh cookie is `SameSite=Strict` (ADR 0006); then the allowed origins and the
  cookie settings. It is needed as soon as the web build is tested against staging
- [ ] The production deploy workflow: on the published release or a merge into `main`; deploy by image digest, run
  `migrate` once, probe `/healthz`
- [ ] Real email delivery: an SMTP provider behind the same `EMAIL_BACKEND` setting, with the sending domain
  authenticated (SPF, DKIM and DMARC) so that mail is not marked as spam; production must not use the outbox
  ([ADR 0010](adr/0010-admin-panel-and-email-outbox.md))
- [ ] The admin panel hardened: login throttling or a network restriction (an allow-list or a private network at the
  proxy), two-factor sign-in with a one-time code from an authenticator app, and a non-default `ADMIN_URL`
- [ ] Secrets and production environment variables prepared (never in the repository)
- [ ] Backups in place before production holds real financial data

## 13. Routine (every time)

- [ ] Sync before starting: fetch, fast-forward `main` and `develop`, switch to `develop`, remove merged local branches
- [ ] Weekly: review Dependabot pull requests (they target `develop`), merge the ones you want
- [ ] Weekly: open one `develop` → `main` release pull request when `develop` is verified
- [ ] After a merge into `main`: check the draft release `v<version>` and publish it only when you want the release
- [ ] When `django-celery-beat` supports Django 6.1 (its releases require `Django<6.1` today): remove the `django`
  ignore entry in `.github/dependabot.yml` and let Dependabot propose the Django 6.1 update
