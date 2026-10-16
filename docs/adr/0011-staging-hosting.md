# ADR 0011: Staging hosting on free tiers

- Status: accepted
- Date: 2026-10-16

## Context

`develop` is staging (ADR 0002). Staging exists so that the Flutter developer can test the API against a real server
early, and so that the owner can see the admin panel and the interactive API docs. It holds invented data only. The
plan asked for the simplest hosting that runs the app, PostgreSQL 18, Redis 8, a Celery worker and Beat behind a public
HTTPS address, with deploys by image digest and a low cost. No free service runs all of that reliably, so the plan is
split into what staging needs now and what it can do without until later.

Facts checked on 2026-10-16 (free tiers change often; check the provider's pricing page before relying on a number):

- Neon offers PostgreSQL 18, generally available since May 2026. Its free plan has 0.5 GB of storage and 100 compute
  hours a month, and the compute suspends when idle.
- Render deploys a prebuilt image from a registry, and a deploy hook accepts `imgURL` to pin a tag or a digest. An
  image-backed service does not redeploy by itself when a new image is pushed, so the hook is how a deploy starts.
- A Render free web service sleeps after about 15 minutes without traffic and needs about a minute to wake. It has no
  free background worker; a worker is a paid instance.

## Decision

| Part | Choice |
| --- | --- |
| App (API, admin panel, API docs) | A Render **free web service** that runs the image from GitHub Container Registry |
| Database | **Neon free**, PostgreSQL 18, the direct (not pooled) connection with `sslmode=require`, in the region nearest to the Render service |
| Redis | A **Render Key Value** free instance (private address). It holds the cache that the sign-in lock and the code limits use, so no persistence is needed. Upstash free is the fallback |
| Image registry | GitHub Container Registry, `PUBLISH_IMAGE=ghcr`. The repository is public and the image holds no secret, so the package is public and Render needs no registry credential |
| Address | `https://<service>.onrender.com`, with HTTPS included. There is no domain yet |
| Codes by email | The database outbox (ADR 0010); the 6-digit codes are read in the admin panel |
| API docs | Swagger UI at `/api/docs/`, only outside production and only for staff accounts |
| Celery worker and Beat | **Not run on staging** (see below) |

Redis is required even without Celery: the limits fail closed (ADR 0006), so without it register, resend, verify and
sign-in answer `503`.

**How a deploy works.** A merge into `develop` builds the image (tags `staging` and `sha-<short>`, the commit passed as a
build argument). The job "Deploy to staging" of `docker.yml` then calls the reusable workflow `deploy-staging.yml` with
the digest of that very build. `scripts/deploy-staging.sh` calls the Render deploy hook with `imgURL` pinned to that
digest, waits until `/healthz` reports the same release in its `X-Release` header, and shows the digest in its summary.
Rolling back is running `deploy-staging.yml` by hand with an earlier commit; it finds the digest by the tag `sha-<short>`.
The deploy is called from `docker.yml`, not triggered by it with `workflow_run`, which the workflow linter rejects as an
insecure trigger. The hook URL is a secret of the GitHub environment `staging`. Migrations run when the container starts (`scripts/entrypoint.prod.sh`);
a free service has one instance, so they cannot race.

**What the free tiers mean in practice.**

- The first request after a pause takes about a minute. A free uptime monitor that calls `/healthz` every five minutes
  keeps the service awake; that endpoint does not touch the database, so Neon can still suspend.
- The first query after Neon suspends is slower than the rest.
- The service runs one instance, with a small number of Gunicorn workers.
- The address behind Render's proxies must be counted correctly (`NUM_PROXIES`, ADR 0006). Render's chain of proxies is
  not documented here, so it is measured on the first deploy (`docs/staging.md`).

## Skipped, later or done another way

| Item | On staging | Later |
| --- | --- | --- |
| Celery worker and Beat | Not run. The only jobs are the two daily purges (old outbox emails, unverified accounts). No step of the first app test needs them, and the leftovers are invented data | When a job matters: the ended-session purge (step 8.4), reminders and pushes (stage 3), and production. A paid Render background worker running `celery worker -B` (Beat inside the worker, exactly one instance) is the cheapest way on staging; production has its own worker and Beat |
| Real email delivery | The outbox | Before production (`docs/steps.md` section 12) |
| Backups | None; the data is invented | Required before production holds real data |
| The web build and its token cookie | Not on staging. `onrender.com` is a public suffix, so a web app on another `onrender.com` address is cross-site and the `SameSite=Strict` cookie of ADR 0006 cannot work | With the owner's own domain shared by the web app and the API (steps 8.5 and 12) |
| Admin hardening (two-factor sign-in, an allow-list) | A non-default `ADMIN_URL` and a long superuser password | Production step |
| Migrations as a separate release step | The container runs them at start | When more than one instance runs (`RUN_MIGRATIONS=false` plus a release job) |
| The topology of production | Staging is not a copy of it | Production hosting is its own decision |

## Not chosen

- **One VPS with Docker Compose and Caddy.** Always on and cheap, but it needs a server to maintain, and a domain for
  HTTPS (or an IP-address certificate on a free port). It stays the fallback if the free tiers get in the way.
- **A paid managed platform.** Less to maintain, but it costs about 25 to 35 USD a month for a stage that holds no real
  data.
- **Oracle Cloud Always Free.** Free and always on, but its allowance was cut in 2026 and instances have been disabled.
- **Free tiers for production.** They sleep, have no backups worth the name and no worker. Production gets its own
  decision, its own database and backups before it holds anyone's financial data.

## Consequences

- Stage 2 (`docs/steps.md` section 9) is rewritten around this decision, and the plan says what staging does not do yet.
- `docker.yml` also builds on `develop` and calls a new workflow that deploys; the image reports its release; the API docs
  exist outside production. These are two pull requests (the deploy, and the API docs).
- The owner creates the three services and the GitHub environment by hand, once, with the runbook
  [`../staging.md`](../staging.md).
- If a free tier changes or gets in the way, only this record and the runbook change: the image, the settings and the
  deploy script do not depend on Render beyond the deploy hook.
