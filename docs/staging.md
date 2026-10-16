# Staging runbook

How staging is built, set up once, deployed, checked and rolled back. The decision and its limits are in
[ADR 0011](adr/0011-staging-hosting.md); the plan is `docs/steps.md` section 9. Staging holds invented data only.

## The pieces

| Part | Service | Notes |
| --- | --- | --- |
| API, admin panel, API docs | Render web service, free instance | Runs the image `ghcr.io/<owner>/<repo>`; address `https://<service>.onrender.com` |
| PostgreSQL 18 | Neon, free plan | The direct (not pooled) connection string, `sslmode=require` |
| Redis | Render Key Value, free instance | The cache for the sign-in lock and the code limits; no persistence needed |
| Image registry | GitHub Container Registry | Public package; Render needs no credential |
| Deploys | GitHub Actions: the job "Deploy to staging" of `docker.yml`, and `deploy-staging.yml` | Calls the service's deploy hook, pinned to an image digest |
| Celery worker and Beat | Not run | Deferred, see ADR 0011 |

## One-time setup (the owner, by hand)

Use the same region for all three services (for example Frankfurt), or every query pays for the distance.

1. **Neon.** Follow [`staging-neon.md`](staging-neon.md), which has every name and command: the project
   `money-scribe-staging` (AWS Europe Central 1, Frankfurt, PostgreSQL 18), the app role `money_scribe_app`, the database
   `money_scribe_staging` that it owns, the **direct** connection string (pooling off, no `-pooler` in the host), and the
   three commands that test the connection, apply the migrations and create the admin account. The string holds the
   database password: it goes into a password manager and is read silently by every command.
2. **Render Key Value.** Create a free instance in the chosen region. Copy its **internal** address
   (`redis://...`); only services in the same Render workspace and region can reach it.
3. **GitHub, before the first deploy:**
   - Settings, Secrets and variables, Actions, **Variables**: `PUBLISH_IMAGE` = `ghcr`.
   - Merge a change into `develop` so the Docker workflow publishes the first image, then open the repository's
     **Packages**, select the package, and set its visibility to **Public**.
   - Settings, **Environments**: create `staging`, with deployment branches limited to `develop`.
4. **Render web service.** Create a new web service from an **existing image**: `ghcr.io/<owner>/<repo>` (lower case),
   free instance, the chosen region, health check path `/healthz`. Add the environment variables below.
5. **The deploy hook.** In the service's Settings copy the **Deploy Hook** URL; it contains a key and is a secret. In the
   GitHub environment `staging` add:
   - secret `RENDER_DEPLOY_HOOK_URL`: the hook URL
   - variable `STAGING_URL`: `https://<service>.onrender.com`
6. **Deploy once.** Actions, **Deploy staging**, **Run workflow**, with the commit of the image you want (the first 7
   characters are enough). The run ends when `/healthz` reports the release it deployed.
7. **Switch the automatic deploy on.** Add the repository variable `DEPLOY_STAGING` = `true`. From then on every merge
   into `develop` is deployed by the Docker workflow, in its job "Deploy to staging". Until the variable is set that job
   is skipped, so a merge never fails for lack of a service.
8. **The admin account** exists already if you did step 8 of [`staging-neon.md`](staging-neon.md); the tables are there
   once `migrate` has run (step 7 of that page, or the first deploy, because the container runs the migrations when it
   starts). Otherwise create it now with the command of that step. The password must pass the same policy as every
   user's: at least 15 characters, not similar to the email or the name, not a common or leaked one.
9. **An uptime monitor** (any free one): call `https://<service>.onrender.com/healthz` every five minutes, so the free
   instance does not sleep between your tests.

## Environment variables of the web service

| Variable | Value |
| --- | --- |
| `APP_ENVIRONMENT` | `staging` (this turns on the API docs and allows the outbox; never `production`) |
| `SECRET_KEY` | a long random string: `python -c "import secrets; print(secrets.token_urlsafe(64))"` |
| `JWT_SIGNING_KEY` | at least 32 random bytes, a different value from `SECRET_KEY`: `openssl rand -hex 32` |
| `JWT_KEY_ID` | `s1` |
| `DATABASE_URL` | the Neon direct connection string |
| `REDIS_URL` | the Render Key Value internal address |
| `ALLOWED_HOSTS` | `<service>.onrender.com` |
| `CSRF_TRUSTED_ORIGINS` | `https://<service>.onrender.com` |
| `ADMIN_URL` | a random path, not `admin` (for example the output of `openssl rand -hex 6`) |
| `EMAIL_BACKEND` | `outbox.backends.DatabaseEmailBackend` |
| `NUM_PROXIES` | `1` at first; measure it (below) |
| `WEB_CONCURRENCY` | `1` (the free instance has 512 MB) |
| `PWNED_PASSWORD_CHECK` | `True` |

Render sets `PORT` itself and the image listens on it. `RUN_MIGRATIONS` stays at its default (migrate when the container
starts). No `CELERY_*` variable is needed while the worker is not run. Never copy a real value into the repository.

## Measure `NUM_PROXIES` (once, on the first deploy)

The limits and the session record use the client address. DRF takes it from `X-Forwarded-For`, counting `NUM_PROXIES`
proxies from the right. Too low shares one address between all clients; too high lets a client choose its own address.

1. Add to the service's environment, temporarily:
   `GUNICORN_CMD_ARGS=--bind=0.0.0.0:$PORT --threads=4 --timeout=120 --access-logfile=- --error-logfile=- --access-logformat='%(h)s xff=%({x-forwarded-for}i)s %(r)s %(s)s'`
2. Call `https://<service>.onrender.com/healthz` from your phone on mobile data, and read the line in the Render logs.
3. Find your phone's address in `xff=` (a list separated by commas). Count from the right end of the list up to and
   including your phone's address: that number is `NUM_PROXIES`. If `xff=203.0.113.9` the number is 1; if
   `xff=203.0.113.9, 10.0.0.4` it is 2.
4. Set `NUM_PROXIES`, and **delete** `GUNICORN_CMD_ARGS` again (the log line holds client addresses).

## Check a deploy

- `https://<service>.onrender.com/healthz` answers `ok`, and its `X-Release` header is the short commit that was
  deployed (the workflow checks this itself).
- `https://<service>.onrender.com/<ADMIN_URL>/` opens the admin sign-in; sign in with the superuser. The saved emails
  (the codes) are under **Outgoing emails**.
- `https://<service>.onrender.com/api/docs/` redirects to the same sign-in, then shows Swagger UI.
- From the app: the test list of `docs/steps.md` step 9.7.

## Roll back or deploy a given commit

Actions, **Deploy staging**, **Run workflow**, and enter the commit (the first 7 characters are enough) of an image
that was built before. The workflow finds that image by its tag `sha-<short>` and deploys it by its digest.

## When something is wrong

| Symptom | Likely cause |
| --- | --- |
| A manual deploy fails with "No image" | The image for that commit was not published: `PUBLISH_IMAGE` is unset, or the Docker workflow failed for it |
| The hook answers 400 | `imgURL` does not match the service's image (`ghcr.io/<owner>/<repo>`, lower case) |
| The hook answers 401 | The secret `RENDER_DEPLOY_HOOK_URL` is old: the hook was regenerated |
| `/healthz` never shows the new release | The new container does not start: read the service's logs (a missing variable, a bad `DATABASE_URL`, `users.E001` for the signing key) |
| Register or sign-in answers `503` | The Key Value instance is unreachable: check `REDIS_URL` and the region |
| The first call after a pause is very slow | The free instance was asleep, or Neon was suspended: expected; the monitor avoids the first |
| The admin sign-in page loops or answers 403 | `ALLOWED_HOSTS` or `CSRF_TRUSTED_ORIGINS` does not match the address |
