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
| Celery worker and Beat | Not run | Deferred, see ADR 0011; scheduled jobs come from a timed call, see ADR 0020 |

## One-time setup (the owner, by hand)

Use the same region for all three services (for example Frankfurt), or every query pays for the distance.

1. **Neon.** Follow [`staging-neon.md`](staging-neon.md), which has every name and command: the project
   `money-scribe-staging` (AWS Europe Central 1, Frankfurt, PostgreSQL 18), the app role `money_scribe_app`, the database
   `money_scribe_staging` that it owns, the **direct** connection string (pooling off, no `-pooler` in the host), and the
   three commands that test the connection, apply the migrations and create the admin account. The string holds the
   database password: it goes into a password manager and is read silently by every command.
2. **Render Key Value.** Follow [`staging-render.md`](staging-render.md), which has every name and screen: the account
   and the workspace `money-scribe` (Hobby plan), then the free instance `money-scribe-staging-redis` in the chosen
   region. A card must be on file for the form to create it. Copy its **internal** address (`redis://...`); only services
   in the same Render workspace and region can reach it, and no inbound rule is added.
3. **GitHub, before the first deploy:**
   - Settings, Secrets and variables, Actions, **Variables**: `PUBLISH_IMAGE` = `ghcr`.
   - Merge a change into `develop` so the Docker workflow publishes the first image, then open the repository's
     **Packages**, select the package, and set its visibility to **Public**.
   - Settings, **Environments**: create `staging`, with deployment branches limited to `develop`
     ([`staging-render.md`](staging-render.md), step 6, has every setting).
4. **Render web service.** Create a new web service from an **existing image**: `ghcr.io/<owner>/<repo>` (lower case),
   free instance, the chosen region, health check path `/healthz`. Add the environment variables below.
5. **The deploy hook.** In the service's Settings copy the **Deploy Hook** URL; it contains a key and is a secret. In the
   GitHub environment `staging` add (details and screens in [`staging-render.md`](staging-render.md), step 6; copy the
   hook with its copy icon and never open the link, because that starts a deploy):
   - secret `RENDER_DEPLOY_HOOK_URL`: the hook URL
   - variable `STAGING_URL`: `https://<service>.onrender.com`
6. **Deploy once.** Actions, **Deploy staging**, **Run workflow**, with the commit of the image you want (the first 7
   characters are enough). The run ends when `/healthz` reports the release it deployed.
7. **Switch the automatic deploy on (optional).** Staging stays manual for now: it is deployed with step 6 when the
   endpoints agreed with the frontend are finished, and the app is tested then
   ([ADR 0019](adr/0019-first-release-after-the-last-endpoint.md)); the repository variable is therefore not set. To go automatic, add the repository variable `DEPLOY_STAGING` = `true`. From then on every merge
   into `develop` is deployed by the Docker workflow, in its job "Deploy to staging". Until the variable is set that job
   is skipped, so a merge never fails for lack of a service.
8. **The admin account** exists already if you did step 8 of [`staging-neon.md`](staging-neon.md); the tables are there
   once `migrate` has run (step 7 of that page, or the first deploy, because the container runs the migrations when it
   starts). Otherwise create it now with the command of that step. The password must pass the same policy as every
   user's: at least 15 characters, not similar to the email or the name, not a common or leaked one.
9. **An uptime monitor (optional, deferred on staging).** Any free one (UptimeRobot and StatusCake state a 5-minute
   interval on their free plans): call `https://<service>.onrender.com/healthz` every five minutes, so the free instance
   does not sleep between your tests. Without it, the service sleeps after 15 minutes without a request and the first
   request takes about a minute. With it, staging is awake all month and uses 720 to 744 of the 750 free hours.

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
| `NUM_PROXIES` | `3` on Render and Cloudflare (measured; start with `1` and measure it, below) |
| `WEB_CONCURRENCY` | `1` (the free instance has 512 MB) |
| `PWNED_PASSWORD_CHECK` | `True` |
| `TURNSTILE_SECRET_KEY` | the secret key of the Turnstile widget ([`sign-in-challenge.md`](sign-in-challenge.md)); a secret, never in the repository |
| `DEVICE_KEY_ANDROID_PACKAGE` | the package name of the staging app (fingerprint sign-in, [ADR 0018](adr/0018-sign-in-with-a-device-key.md)); `check --deploy` warns (`users.W002`) while it is empty |
| `DEVICE_KEY_ANDROID_CERT_SHA256` | the SHA-256 digests, hex and comma separated, of the certificates that sign the staging app; leave `DEVICE_KEY_REQUIRE_ATTESTATION` unset (it must stay on here, `users.E002`) |

Optional variables, all with a working default: `LOG_LEVEL` and `DJANGO_LOG_LEVEL` (`INFO`; raise to `DEBUG` only while
looking for a fault, and remember that the logs never hold request bodies), `SECURE_SSL_REDIRECT` (`True`) and
`SECURE_HSTS_SECONDS` (`0`; leave it at `0` on `onrender.com`, because HSTS belongs to the final domain). `APP_RELEASE`
is set by the image build; never set it by hand.

Render sets `PORT` itself and the image listens on it. `RUN_MIGRATIONS` stays at its default (migrate when the container
starts). No `CELERY_*` variable is needed while the worker is not run. Never copy a real value into the repository.

## Measure `NUM_PROXIES` (once, on the first deploy)

The limits and the session record use the client address. DRF takes it from `X-Forwarded-For`, counting `NUM_PROXIES`
proxies from the right. Too low shares one address between all clients; too high lets a client choose its own address.

1. Add to the service's environment, temporarily:
   `GUNICORN_CMD_ARGS=--threads=4 --timeout=120 --access-logfile=- --error-logfile=- --no-control-socket --access-logformat='%(h)s xff=%({x-forwarded-for}i)s %(r)s %(s)s'`
   (no `--bind`: an environment variable is not expanded, so `$PORT` would stay literal, and Gunicorn binds to
   `0.0.0.0:$PORT` by itself when `PORT` is set. This value replaces the entrypoint's defaults, hence the other flags)
2. Call `https://<service>.onrender.com/healthz` from your phone on mobile data, and read the line in the Render logs.
3. Find your phone's address in `xff=` (a list separated by commas). Count from the right end of the list up to and
   including your phone's address: that number is `NUM_PROXIES`. If `xff=203.0.113.9` the number is 1; if
   `xff=203.0.113.9, 10.0.0.4` it is 2.
4. Set `NUM_PROXIES`, and **delete** `GUNICORN_CMD_ARGS` again (the log line holds client addresses).

On staging the list held three entries (the phone, a Cloudflare server, Render's internal proxy), so the value is `3`.
[`staging-render.md`](staging-render.md), step 7, explains `GUNICORN_CMD_ARGS` flag by flag and records the check with a
forged header.

## Work in the dev container against the Neon database

Set `DATABASE_URL` in your git-ignored dotenv file to the Neon direct connection string and reopen the container. The `db`
service still starts but is not used. Run `pm migrate` only if you want to change the Neon schema from your machine;
Render applies the migrations on every deploy anyway.

Celery Beat does not start against an external database: it schedules the purge jobs on its own, and the local worker would
run them on the Neon data. The `celery-beat` container stays up and its log (`docker compose logs celery-beat`) says why.
To run Beat there on purpose, set `BEAT_ON_EXTERNAL_DATABASE=true` in the same file and run
`docker compose restart celery-beat`. With the local `db` service Beat needs no setting: it waits for the migrations and
starts after `pm migrate`.

## Check a deploy

- `https://<service>.onrender.com/healthz` answers `ok`, and its `X-Release` header is the short commit that was
  deployed (the workflow checks this itself).
- `https://<service>.onrender.com/<ADMIN_URL>/` opens the admin sign-in; sign in with the superuser. The saved emails
  (the codes) are under **Outgoing emails**.
- `https://<service>.onrender.com/api/docs/` shows Swagger UI once you are signed in to the admin panel in the same
  browser. Without a staff session it answers `404`, on purpose: a redirect to the sign-in would reveal the secret
  `ADMIN_URL` to any visitor. `/api/schema/` behaves the same and downloads the schema as a YAML file.
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
| The first call after a pause is very slow | The free instance was asleep, or Neon was suspended: expected; an uptime monitor (step 9) avoids the first |
| The admin sign-in page loops or answers 403 | `ALLOWED_HOSTS` or `CSRF_TRUSTED_ORIGINS` does not match the address |
