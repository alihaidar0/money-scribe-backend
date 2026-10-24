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
  puts `/workspace/.venv/bin` first on `PATH`. The Celery worker starts by itself once the project and its
  dependencies exist; Beat also waits until `pm migrate` has been run.

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
- [ ] `celery -A config flower --port=5555` lists the worker at <http://localhost:5556> (Ctrl+C to stop)
- [ ] `python manage.py check --deploy` has no errors that matter for the current stage
- [ ] `ssh -T git@github.com-freelance` greets `alihaidar0` (this repository's remote alias)
- [ ] `git switch develop && pre-commit run no-commit-to-branch` **fails** (develop is protected); then `git switch -`

What you should see:

| Where | What you should see |
| --- | --- |
| <http://localhost:8000> | the Django start page (debug mode) |
| <http://localhost:8000/admin/> | the admin login; your superuser signs in |
| <http://localhost:8025> | Mailpit with the test email |
| <http://localhost:5556> | Flower listing the worker |
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
- [ ] Staging hosting: Neon, Render and the GitHub Container Registry on free tiers, with the worker and Beat deferred,
  [ADR 0011](adr/0011-staging-hosting.md). Email delivery on staging: the outbox. Production hosting, real email delivery
  and the shared domain of the web app and the API: later (section 12)

## 8. Stage 1: the auth layer (one topic branch per step)

Only authentication comes first: register, verify, sign in, refresh, sign out and everything around them. Each step is its
own branch and pull request, and API work is contract-first: agree the request and response shapes with the Flutter
developer in a request issue, implement with tests (including a test that another user cannot reach the data, where it
applies), regenerate `openapi/schema.yaml`, and open the pull request with the `api-change` label. The shapes are fixed in
[`api-fields.md`](api-fields.md), [`api-conventions.md`](api-conventions.md) and the records in [`adr/`](adr/README.md);
the process is in [`api-workflow.md`](api-workflow.md).

The Flutter developer tests only against staging, so **stage 2 starts right after step 8.2**: steps 8.3 to 8.5 are built
while staging already runs, and each one deploys to staging by itself and is tested as it lands.

- [x] **8.1 `feat/api-foundation`**: `/api/v1/` routing; the shared error handling (the error-code registry, `409` through
  `config.exceptions.conflict` and a per-operation `CONFLICT_RESPONSE`, `Retry-After` on `429`; ADR 0004); default
  permissions (authenticated, JSON only, every query scoped to `request.user` through `OwnerScopedMixin`); the bearer
  security scheme in the schema; the root test fixtures and the per-user isolation helper; **the admin panel**
  (`ADMIN_URL`, staff only, the users admin ready, superuser creation with email and name); and **the email outbox**
  (the `outbox` app: a database email backend, a read-only admin to copy a 6-digit code from, a daily purge, and the
  `APP_ENVIRONMENT` checks that refuse it in production), with tests (ADR 0010)
- [x] `.github/CODEOWNERS` needs no change: the client repository's owner, `@alihaidar0`, is already listed for
  `/openapi/` and `/.spectral.yaml`. Add another developer's handle to those two lines when one joins
- [x] **8.2 `feat/auth-register-signin`**: the `Session` model and the `device` object (ADR 0009); register, verify email
  (this call creates the session) and resend the code; sign in; refresh with the 30 second replay window; sign out; the
  session check on every request; throttling and the sign-in lock with `Retry-After`; hashed 6-digit codes, sent through
  Mailpit in development; Argon2id password hashing and the NIST password policy (15 characters, no composition rules, a breach check); a registration that reveals nothing; limits that are atomic, per client and fail closed; the daily purge of unverified accounts; `language` and `time_zone` stored on the user (ADR 0001); the
  read-only profile (`GET /api/v1/auth/profile/`), which the app needs after a restart; the signing key, lifetimes and
  replay window in `.env.example` and `docker-compose.yml`. **The first `openapi/schema.yaml` is generated and committed here**
  (`bash scripts/schema.sh generate`), which switches on the three API CI gates (schema current, style, breaking changes)
  and makes the draft release carry the schema as `openapi.yaml`. The Flutter developer can start on the register and
  sign-in screens from this contract
- [ ] **8.3 `feat/auth-account`**: profile (edit the name; the read is in 8.2), change password, password reset (request, verify, set),
  email change (request, confirm, cancel) and delete account
- [ ] **8.4 `feat/auth-sessions`**: the keyset cursor class (`config/pagination.py`, ADR 0003) with its tests; list the
  sessions, end one, end all others; the purge task for ended sessions (Celery Beat, 90 days); the new-device email
- [ ] **8.5 `feat/auth-web-cookie`**: the web form of the tokens: the `HttpOnly` cookie, `X-Client: web`, the `Origin`
  check, and CORS with credentials for an explicit list of origins (ADR 0006), tested on `localhost`. A shared domain is
  needed only when the web build is tested against staging
- [ ] `docs/` updated wherever a step changed a convention
- [ ] Stage 1 is done when the five steps are merged, the schema covers every auth operation, and the Flutter developer
  has the screens working against staging

## 9. Stage 2: staging deployment and the first test in the Flutter app

`develop` is staging. Start it right after step 8.2, so the first test in the app happens early; steps 8.3 to 8.5 are then
built while staging already runs. Staging runs on free tiers (Neon for PostgreSQL 18, a Render web service for the
image, a Render Key Value instance for Redis), chosen in [ADR 0011](adr/0011-staging-hosting.md); the one-time setup, the
checks and the rollback are in [`staging.md`](staging.md). Staging holds invented data only. What it does not do yet is
listed at the end of this section. Each step is its own branch and pull request.

- [x] **9.1 Staging hosting recorded**: [ADR 0011](adr/0011-staging-hosting.md) (Neon, Render, the Render Key Value
  instance, the `onrender.com` address, the image from the GitHub Container Registry, the worker and Beat deferred)
- [ ] **9.2 Codes on staging through the outbox**: staging sends no real email yet. It uses the email outbox
  (`EMAIL_BACKEND` set to the database backend), and you copy each 6-digit code from the emails in the admin panel at the
  staging `ADMIN_URL` (ADR 0010). Real delivery is a separate step before production (section 12)
- [x] **9.3 `ci/staging-deploy`: the staging image and its deploy**: `docker.yml` also builds on every merge into
  `develop` (tags `staging` and `sha-<short>`, never `latest`) with the commit as a build argument, so `/healthz` reports
  it in the `X-Release` header; its job "Deploy to staging" then calls the reusable workflow `deploy-staging.yml` with the
  digest of that build (and the same workflow runs by hand for a rollback or an older commit). `scripts/deploy-staging.sh`
  calls the Render deploy hook pinned to the digest, waits until `/healthz` reports the same release, and shows the digest
  in its summary. One pull request, because the deploy is called from `docker.yml`. The repository variable
  `PUBLISH_IMAGE=ghcr` is set and the package is public
- [x] **9.4 `feat/api-docs`: Swagger UI**: `/api/docs/` and `/api/schema/`, served only outside production and only to
  staff accounts (signed in through the admin panel; anyone else gets a `404`, never a redirect that would reveal the
  admin path), with the UI bundled in the image and no CDN, tested; the committed
  `openapi/schema.yaml` is unchanged
- [x] **9.5 The services and the first deploy** (the owner, by hand, following [`staging.md`](staging.md)): the Neon
  project ([`staging-neon.md`](staging-neon.md)), the Render Key Value instance and web service
  ([`staging-render.md`](staging-render.md)), the environment variables, the GitHub environment `staging`
  (deployment branches: `develop`) with its secret `RENDER_DEPLOY_HOOK_URL` and variable `STAGING_URL`, the first deploy
  by hand (deploys stay manual for now; the repository variable `DEPLOY_STAGING=true` would switch the automatic deploy
  on), the superuser, the `NUM_PROXIES` measured (`3`), and, optionally, an uptime monitor (deferred: without it staging sleeps after 15 minutes
  idle and the first request takes about a minute)
- [ ] **9.6 The first contract release**: check that `version` in `pyproject.toml` is the version to release, open the
  release pull request (`develop` → `main`), check the draft `v<version>` (notes and the `openapi.yaml` asset), and
  publish it so the Flutter developer pins that tag ([`consuming-the-api.md`](consuming-the-api.md)); or pin a commit and
  wait, if you prefer
- [ ] **9.7 The first test in the app**: the staging flavor points at the staging address. First check the admin panel and
  Swagger UI on staging ([`staging.md`](staging.md)), then run this list and record the result:
  - register, copy the code from the outbox in the admin panel, verify, and land in the app; sign out and sign in again
  - an unknown email and a wrong password, each under its own field, then the sign-in lock with its countdown (`Retry-After`)
  - the access token expires after 10 minutes and the app refreshes without asking the user
  - a lost response on refresh (airplane mode) recovers within 30 seconds
  - password reset, change password, change email, delete account (once step 8.3 is deployed)
  - a session ended from another device shows the reason from its `session_*` code (once step 8.4 is deployed)
- [ ] Gate: start stage 3 only when the app works against staging. Fix what the test found first.

Not on staging yet (ADR 0011 has the reasons):

- The Celery worker and Beat. The two daily purges (old outbox emails, unverified accounts) do not run, and the leftovers
  are invented data. Add a paid worker running `celery worker -B` when a job matters (step 8.4, stage 3); production has
  its own worker and Beat (section 12).
- Real email delivery, backups and the hardened admin panel: section 12.
- The web build and its token cookie: they need the owner's own domain shared by the web app and the API (steps 8.5 and
  12), because `onrender.com` is a public suffix. Staging is for the mobile app only.
- Migrations as a separate release step: the container runs them when it starts, which is safe for one instance.

## 10. Stage 3: the features, one at a time (fingerprint sign-in, then salary)

Each feature is a loop: agree the shapes in a request issue, implement with tests, regenerate the schema, open the pull
request with the `api-change` label, merge, let staging deploy by itself, **test it in the Flutter app**, then move to the
next. A decision a feature needs gets its record first. The order below is the owner's (2026-10-24): the fingerprint
sign-in first, because the store disclosures for it need time and it depends on no money feature, then salary, then the
others. The Flutter developer's dependency view put the settings and the ledger before salary; salary needs neither, so it
stays ahead of them. Each step ships in a tagged release with `openapi.yaml` attached, and the app pins that tag
([`consuming-the-api.md`](consuming-the-api.md)).

- [ ] **10.1 `feat/auth-device-key`**: sign-in with a device key pair and a signed challenge (the model passkeys use,
  without a domain), [ADR 0018](adr/0018-sign-in-with-a-device-key.md): the key table, the public challenge and sign-in calls, the
  authenticated registration with a proof of possession and the Android key attestation, the list and removal of keys,
  the limits that fail closed, the removal of every key when the password or the email changes or the account is deleted,
  and the privacy and store-disclosure sentences. Moves to passkeys when the shared domain exists
- [ ] **10.2 `feat/money-core`** (before salary): `common/money.py`, the money value type (integer minor units, currency
  table, half-up rounding, cross-currency error) with unit tests, decimal-string parsing and writing in the form of ADR
  0008, and the shared `Amount` and `SignedAmount` serializer field
- [ ] **10.3 `feat/write-safety`** (before the first resource with a create): the `Idempotency-Key` store and mixin
  (the key and the stored answer saved in the same transaction as the record, 24 hours, user scoped), the `version` field
  with `ETag` and `If-Match` handling in one statement, the `412` and `422` declarations in `config/api_errors.py`, the
  five new codes in `config.error_codes.ErrorCode`, and the CORS headers; all of it tested with a throwaway resource
  (a repeated create, the same key with another body, two simultaneous requests, a stale edit), [ADR 0016](adr/0016-safe-writes.md)
- [ ] **10.4 salary**: salary months computed on the server (ADR 0007), addressed by `YYYY-MM` with `PUT`; fixed items
  inline; balances per part and for the month; the history list with each result (the cursor class exists since 8.4);
  payment mode kept per month (month comparison is a read model and comes with 10.8); one month per user per `YYYY-MM`,
  the currency a field of the month; at most 100 fixed items and the explicit copy of the items of another month; `If-None-Match: *` on the creating `PUT`;
  the read of defaults for a month not saved yet (precedence in `api-fields.md`: a Settings value the person has set,
  then the latest saved month, then the app's own defaults; the currency follows the Settings default, and the carried
  amounts only when it equals the latest month's; until 10.5 the latest month is the only source). The worked examples of ADR 0007 become unit tests,
  and the file of invented cases is shared with the Flutter developer so the live preview matches to the minor unit
- [ ] **10.5 preferences**: default currency, default payment mode, pay days and `remind_on_pay_days`; `time_zone` and
  `language` are stored since 8.2 and become changeable here through the same settings `PATCH` (the profile update
  accepts only `name`), so a language change in the app reaches the notifications without a new sign-in
- [ ] **10.6 ledger**: accounts (delete refused with `409` when transactions exist; reorder); categories (the built-in
  rows with their keys, read-only; custom ones, reorder; deleting a custom one moves its transactions to `other`, ADR
  0017); transactions (the category must allow the kind; ordered `-date`, `-created_at`, `-id` with the filters,
  including `search` on the note, through `django-filter`)
- [ ] **10.7 debts**: debts with repayments, derived `repaid`, `remaining`, `is_settled`, `progress` and `status`, with the
  `today` parameter
- [ ] **10.8 reports**: month overview, day totals, category spend, month comparison, debts report and period report, with
  the `part` filter
- [ ] **10.9 CSV export and import** of transactions, debts and salary months: the server builds the export; the import is
  a bulk call of rows the app has parsed (the server never parses an uploaded file), with skipped-row reporting and a
  lossless round trip
- [ ] **10.10 notifications**: push device FIDs (Firebase installation IDs, one per session, removed when it ends),
  pay-day reminders through Celery Beat, and the push provider wiring (the last open decision for this stage; `firebase-admin`
  7.5.0 or later, `Message(fid=...)`). The rules are in
  [`api-fields.md`](api-fields.md#push-notifications). Build notes: one service function ends sessions and deletes their
  FIDs in the same transaction, and every place that ends a session uses it (a test guards this); sending selects only
  FIDs of sessions that are not ended and not past `expires_at`; registering is atomic, so two simultaneous calls
  cannot leave two rows; the FID is write-only, hidden in the admin and never logged; a daily sweep of FIDs of
  expired sessions joins the Celery Beat jobs when a worker runs in production; register refuses a `web` session with a
  documented error code (new codes go into ADR 0004 first), and the `screen` values are part of the schema

## 11. Before the first production release

- [ ] `pytest`, `mypy .`, `ruff check .`, `pre-commit run --all-files` and `bash scripts/schema.sh check` are clean
- [ ] `python manage.py check --deploy` passes with production settings
- [ ] Production image builds and answers `/healthz` (CI smoke test is green)
- [ ] Staging has run the same build, and the Flutter app has been tested against it
- [ ] **Rate limiting by address and by account, across the whole API.** The authentication operations are limited
  already ([ADR 0006](adr/0006-authentication-tokens.md): sign-in, registration, code resend and verification, per
  account and per client address, counters in Redis, `429` with `Retry-After`). Before production, give every other
  operation default limits too: per signed-in user, and per client address for requests without a token; stricter ones
  for the costly operations (reports, CSV export and import, from stage 3). The numbers and the reasons go into an ADR;
  each limit is documented as a `429` in the schema and tested; `NUM_PROXIES` is the proxy count measured on staging and
  re-checked for the production topology, because a wrong count either shares one address between all clients or lets a
  client choose its own
- [ ] **App attestation.** Decide with the Flutter developer whether the server requires proof that a request comes from
  the genuine, unmodified app on a real device: Play Integrity on Android and App Attest on iOS, or Firebase App Check,
  which wraps both. The server would verify the proof on the operations that start a session (register, sign-in,
  refresh) and refuse a failing one with a documented error code; roll it out in a monitoring-only mode first, then
  enforce, and keep a way to switch it off. It needs work in both repositories and its own ADR. A plain API key in the
  app is **not** used: it can be read out of the app, so it would identify no one and protect nothing the tokens do not
  already protect
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

- [ ] Production hosting chosen, with its own PostgreSQL, Redis, Celery worker and exactly one Beat, separate from staging.
  A free tier is not a platform for real financial data (it sleeps and has no backups worth the name), so the free start
  described in the next item is a pre-launch stage only; staging's choice ([ADR 0011](adr/0011-staging-hosting.md)) does
  not carry over as it is, because production gets its own workspace, database and Redis
- [ ] **Production starts on free tiers, in its own Render workspace** (direction chosen on 2026-10-17; ADR 0012 records
  it, with prices checked at that time, when this section is reached). It is a pre-launch stage: the owner and a few
  trusted testers, no real users' financial data. What it needs and accepts:
  - its own workspace, project and free Key Value instance (a workspace has one free Key Value instance and 750 free
    instance hours, so it cannot share staging's), and its own Neon project, never staging's
  - email over an HTTPS API of a provider, not SMTP: a free Render web service cannot use the outbound ports 25, 465 and
    587, so the email backend of [ADR 0010](adr/0010-admin-panel-and-email-outbox.md) changes for production
  - no Celery worker (a worker is a paid instance), so the daily purges, the ended-session purge and, from stage 3, the
    reminders and pushes do not run until a worker is paid for
  - the limits of free instances: it sleeps after 15 minutes without a request (a monitor keeps it awake and uses 720
    to 744 of the 750 hours), a slow 0.1 CPU, restarts at any time, no zero-downtime deploys
  - Neon free keeps only a **6-hour** history and one manual snapshot: no real backups (the manual `pg_dump` procedure
    in [`staging-neon.md`](staging-neon.md) is a stop-gap, not a backup strategy). **Upgrade before real financial
    data is stored**: the web service to a paid type, the Key Value instance to a paid one, Neon to a paid plan (all
    possible in place), and the backups item below done
- [ ] The GitHub environment `production` (deployment branches: `main`; a required reviewer once there is a second
  maintainer; administrators cannot bypass) with its own secrets and variables
- [ ] One registrable domain shared by the web app and the API (for example `app.<domain>` and `api.<domain>`), for staging
  and for production, because the web refresh cookie is `SameSite=Strict` (ADR 0006); then the allowed origins and the
  cookie settings. It is needed as soon as the web build is tested against staging
- [ ] The production deploy workflow: on the published release or a merge into `main`; deploy by image digest, run
  `migrate` once as a release step when more than one instance runs (`RUN_MIGRATIONS=false` on the instances), probe
  `/healthz` for the expected release. `NUM_PROXIES` set to the measured proxy count
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
