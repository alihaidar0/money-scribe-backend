# Render for staging, step by step

The hosting of staging: how the Render workspace, the Key Value instance and the web service were set up, how the deploy
hook and the GitHub environment `staging` were created, what every name is, what each screen should show, and what the
free plan means. It covers steps 2 to 6 of the one-time setup in
[`staging.md`](staging.md); the decision is in [ADR 0011](adr/0011-staging-hosting.md). The Neon database comes first
([`staging-neon.md`](staging-neon.md)). Render's console wording changes from time to time, so follow the purpose of each
step if a label differs.

## Rules for the secrets

- Secrets on this page: the values of the service's environment (`SECRET_KEY`, `JWT_SIGNING_KEY`, `DATABASE_URL`,
  `ADMIN_URL`), the internal address of the Key Value instance, and the deploy hook URL. Keep all of them in a password
  manager. The hook URL is copied with its copy icon straight into the GitHub secret and nowhere else.
- Never put a secret in a file, a chat, a screenshot, an issue or a pull request. The environment values are generated on
  your machine and typed into Render's form; they are never pasted anywhere else.
- The internal address of the Key Value instance has no password and cannot be reached from the internet. It is still a
  setting of the service, so it is kept in the password manager and not posted.
- The sign-in of this account is Google and GitHub, so the security of the Render account is the security of those two
  accounts: keep two-factor sign-in on in both.

## What you create

| What | Name or value | Why |
| --- | --- | --- |
| Account | Sign-in with Google and GitHub; two-factor sign-in on | The account controls the services and the deploy hook |
| Workspace | `money-scribe`, plan **Hobby** | One workspace for this project; the free-instance allowances are counted per workspace |
| Project and environment | Project `money-scribe`, environment `staging` (renamed from `My project` and `Production`) | Everything of staging lives there; production gets its own environment later |
| Key Value instance | `money-scribe-staging-redis`, region Frankfurt (EU Central), plan **Free** | The Redis of the sign-in lock and the code limits (the limits fail closed, so without it register and sign-in answer `503`) |
| Memory policy | `allkeys-lru` | When the 25 MB are full the oldest keys go; every key of ours expires anyway |
| Persistence | Off (the Free plan has none) | The counters are short-lived |
| Access from the internet | None: no inbound rule | Only services in the same workspace and region reach it, by the internal address |
| Web service | `money-scribe-staging`, from the image `ghcr.io/alihaidar0/money-scribe-backend:staging`, region Frankfurt, plan **Free** (0.1 CPU, 512 MB RAM), health check `/healthz` | The API, the admin panel and the API docs, at `https://money-scribe-staging.onrender.com` |
| Deploy hook | The Deploy Hook URL of the web service (secret) | Lets the workflow start a deploy pinned to an image digest |
| GitHub environment | `staging`, deployment branch `develop` only, no reviewers, no timer | Holds the hook and the address; only runs from `develop` can read them |
| GitHub secret | `RENDER_DEPLOY_HOOK_URL` (in the environment `staging`) | The hook URL |
| GitHub variable | `STAGING_URL` = `https://money-scribe-staging.onrender.com` (in the environment `staging`) | Where the workflow waits for `/healthz` |
| Automatic deploy | Off: the repository variable `DEPLOY_STAGING` is not set | Staging is deployed by hand until the owner switches it on |
| Environment variables | 13, on the service (step 5) | Each service has its own; an Environment Group is only worth it when a second service needs the same values |

## The Hobby plan

What the plan picker showed on 2026-10-16. Prices and allowances change, so check the page before relying on a number.

| Item | Hobby |
| --- | --- |
| Base price | $0 per month, plus the cost of any paid compute |
| Workspace | One workspace for you |
| Bandwidth | 5 GB included, $0.15 per GB after |
| Custom domains | 2 included, $0.25 per domain after |
| Build minutes | 500 |
| Projects | Unlimited, with 2 environments each |
| Services | Up to 25 |

## Step 1: the account and the workspace

1. Sign in at render.com. This account uses **Google** and **GitHub** as login methods (Account Settings, Account
   Security, Login Methods); there is no Render password.
2. Open **Account Settings**, **Account Security**, and check that **Two-Factor Authentication** is **Enabled**, with an
   authenticator app as the primary method. Keep the backup codes in the password manager. I could not verify whether
   Render asks for this code on a Google or GitHub sign-in; turn on two-factor sign-in in those two accounts as well.
3. Open the workspace menu (top left) and choose **New Workspace**. Name it `money-scribe` and choose the plan
   **Hobby**. Switch to it with the same menu. The default workspace of the account (named after you, also Hobby) stays
   as it is and is not used.
4. Do not click **Upgrade**. Every instance of staging stays on the **Free** type.

## Step 2: the Key Value instance

The Redis of staging. Render runs it on Valkey (the page shows `Valkey 8.1.10`), a Redis-compatible server; Django's Redis
cache talks to it unchanged.

1. A **card must be on file** before the form lets you create the instance, even for the Free type (found on
   2026-10-16; Render's documentation does not say so). Add it under **Billing** of the workspace. With a payment method,
   usage over the free allowances (for example bandwidth) is billed; without one, Render suspends the free services for
   the rest of the month instead. Keep every instance on **Free** and look at the Billing page after each change.
2. In the workspace choose **New**, then **Key Value** (the tile "New Key Value Instance"). Fill in:

   | Field | Value |
   | --- | --- |
   | Name | `money-scribe-staging-redis` |
   | Region | Frankfurt (EU Central). It cannot be changed later, and it must be the region of the web service |
   | Maxmemory Policy | `allkeys-lru (recommended for caches)` |
   | Persistence Mode | Off (greyed out on the Free plan) |
   | Compute | **$0 / month**, 25 MB RAM, 50 connections, marked "Free" and "No persistence" |

   The form has no network field. Do not pick a paid compute plan: Render does not support moving to a smaller one.
3. Click **Create Key Value**. The instance page opens and, after a short wait, shows:

   | Field | Expected |
   | --- | --- |
   | Status | **Available** |
   | Plan badge | **Free** |
   | Region | Frankfurt |
   | Runtime | Valkey 8.1.x |
   | Compute | $0 / month, 25 MB RAM, 50 connections |

4. Scroll to **Connections**. It shows two addresses:

   | Address | What it shows | Use |
   | --- | --- | --- |
   | Internal Key Value URL | `redis://red-example0123456789:6379` (invented example) | The only one we use: `REDIS_URL` of the web service |
   | External Key Value URL | "External traffic not allowed. Add IP addresses in the Networking section." | Stays unused |

   Copy the internal address into the password manager as "Render Key Value internal address (staging)". Check the scheme
   and port against what your page shows.
5. Scroll to **Networking**. The table **Valkey Inbound IP Rules** has no row, and the message says **All internet traffic
   is blocked by Valkey inbound IP rules**. That is the state we want: **do not click Add source**. The two tables above
   it (the workspace and the environment, source `0.0.0.0/0`, "everywhere") are Render's general rules for web
   services and do not open the Key Value instance.

## Step 3: name the project and the environment

`develop` is staging and `main` is production, so every name says which one it is. Render creates a first project called
`My project` with an environment called `Production`; the Key Value instance of step 2 lands in it. Rename both, so that a
staging service never sits in something called production:

1. Open the project from the breadcrumb (or the dashboard) and click its **Settings**. Under **Project Name** click
   **Edit** and enter `money-scribe`.
2. On the project overview, open the three dots at the right of the environment heading, then the environment's settings.
   Under **Name** click **Edit** and enter `staging`.
3. The overview now shows the heading **staging** with `money-scribe-staging-redis` in it.

Leave the other settings of the environment as they are:

| Setting | Value | Note |
| --- | --- | --- |
| Permissions | **Default** ("For staging and development") | **Protected** (only admins can delete or suspend) is a Pro feature |
| Cross-Environment Connections | **Allowed** | **Blocked** is a Pro feature; the services of the environment reach each other either way |
| Environment Groups | None | Shared variable sets; we put the variables on the service |
| Delete Environment | Disabled | A project needs at least one environment |

A **production** environment is not created now. The Hobby plan allows two environments per project, so it fits later, but
the hosting of production is still an open decision (`docs/steps.md` section 12) and, on Hobby, it could not be protected.

The web service, the Key Value instance and any later service of staging go into the project `money-scribe`, environment
`staging`.

## Step 4: the web service

The API, the admin panel and the API docs. Render starts a deploy as soon as the form is submitted, so fill in the
environment variables (step 5) **before** the first click on **Deploy web service**.

1. In the project `money-scribe`, environment `staging`, choose **New**, then **Web Service**. The first tab is a Git
   provider that lists your repositories (do not use it); choose the third tab, **Existing Image**.
2. Fill in the first part and click **Connect**. Nothing is created yet; the next page is the configuration.

   | Field | Value |
   | --- | --- |
   | Image URL | `ghcr.io/alihaidar0/money-scribe-backend:staging` (lower case; Render shows the GitHub registry icon) |
   | Credential (Optional) | **No credential**: the package is public |

3. On the configuration page:

   | Field | Value |
   | --- | --- |
   | Name | `money-scribe-staging`. **Render pre-fills it from the image** (`money-scribe-backend:staging`): replace it. The name becomes the address `https://money-scribe-staging.onrender.com` |
   | Project / environment | `money-scribe` / `staging` |
   | Region | Frankfurt (EU Central) (the page says "1 existing service": the Key Value instance) |
   | Compute | **$0 / month, 0.1 CPU, 512 MB RAM, Free**. **Render pre-selects a paid plan** ($7 / month, 0.5 CPU): select the Free row and check that the bar at the bottom says $0 / month |
   | Health Check Path (under **Advanced**) | `/healthz`. The grey text in the field is only a hint, **not a value**: click the field and type it. The first deploy of staging went out with the field empty, and the service's Settings showed it empty until it was typed |
   | Docker Command | Empty: the image starts itself |
   | Pre-Deploy Command | Empty and locked on the Free plan; the container runs the migrations when it starts |
   | Secret Files | Not used |

   There is no auto-deploy switch for an image: deploys start from the deploy hook (step 6 of the runbook).
4. Add the environment variables of step 5, then click **Deploy web service**.

## Step 5: the environment variables

Added on the same form, with **Add Environment Variable** (do not use **Add from .env** or **Generate**: the values are
made on your own machine and kept in the password manager). Generate the three random values in the terminal of the
development container and run `clear` afterwards:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"   # SECRET_KEY
openssl rand -hex 32                                           # JWT_SIGNING_KEY (a different value)
openssl rand -hex 6                                            # ADMIN_URL
```

| Key | Value | Source |
| --- | --- | --- |
| `APP_ENVIRONMENT` | `staging` | Typed. Turns on the API docs and allows the outbox; never `production` here |
| `SECRET_KEY` | the first generated value | Generated, password manager |
| `JWT_SIGNING_KEY` | the second generated value | Generated, password manager; different from `SECRET_KEY` |
| `JWT_KEY_ID` | `s1` | Typed |
| `DATABASE_URL` | the Neon direct connection string | Password manager ([`staging-neon.md`](staging-neon.md), step 5) |
| `REDIS_URL` | the Key Value internal address | Password manager (step 2 above) |
| `ALLOWED_HOSTS` | `money-scribe-staging.onrender.com` | Typed: the service name plus `.onrender.com` |
| `CSRF_TRUSTED_ORIGINS` | `https://money-scribe-staging.onrender.com` | Typed |
| `ADMIN_URL` | the third generated value | Generated, password manager; the secret path of the admin panel |
| `EMAIL_BACKEND` | `outbox.backends.DatabaseEmailBackend` | Typed |
| `NUM_PROXIES` | `1` at first, then **`3`** | Typed; `1` was a guess, `3` is the measured value of step 7 |
| `WEB_CONCURRENCY` | `1` | Typed; the free instance has 512 MB |
| `PWNED_PASSWORD_CHECK` | `True` | Typed |
| `TURNSTILE_SECRET_KEY` | the widget's secret key | Password manager ([`sign-in-challenge.md`](sign-in-challenge.md)); without it sign-in has no bot challenge and `check --deploy` warns (`users.W001`) |
| `DEVICE_KEY_ANDROID_PACKAGE` | the staging app's package name | Typed; the app's side sends it ([ADR 0018](adr/0018-sign-in-with-a-device-key.md)); without it no Android fingerprint key can be registered (`users.W002`) |
| `DEVICE_KEY_ANDROID_CERT_SHA256` | the SHA-256 digests of the certificates that sign the staging app, comma separated | Typed; digests are public, but kept in the environment like the rest |

Render masks the values of the keys it recognises as secrets (`SECRET_KEY`, `DATABASE_URL`, ...) and shows the others;
the eye icon reveals a value. Check the masked ones by eye before deploying: `DATABASE_URL` starts with `postgresql://`,
its host has no `-pooler`, and it ends with `sslmode=require`; `REDIS_URL` starts with `redis://red-` and ends with
`:6379`. Render sets `PORT` itself. Do not add `PORT`, `RUN_MIGRATIONS`, `DJANGO_SETTINGS_MODULE` or any `CELERY_*`
variable.

### What the first deploy shows

The **Logs** tab shows, in this order (times from the first deploy, on the 0.1 CPU instance):

| Log line | Meaning |
| --- | --- |
| `Starting service...` | Render started the container |
| `Applying database migrations…` then `No migrations to apply.` | The entrypoint ran `migrate` against Neon (about 15 seconds; Neon was already migrated) |
| `Starting Gunicorn on :10000 (1 workers)`, `Listening at: http://0.0.0.0:10000`, `Using worker: gthread` | The app is up on the port Render gave it, with the one worker of `WEB_CONCURRENCY=1` |
| `Your service is live` and `Available at your primary URL https://money-scribe-staging.onrender.com` | The deploy is finished. The address has no suffix, so `ALLOWED_HOSTS` matches |

Lines after that are requests, and the warnings among them are normal:

| Log line | Meaning |
| --- | --- |
| `HEAD / ... 301` from `127.0.0.1`, agent `Go-http-client` | Render probing the port. The 301 is the redirect to HTTPS |
| `GET / ... 404` and `WARNING django.request: Not Found: /` | Someone opened the bare address (Render's probe, or your browser). The API has no page at `/`; the paths are `/api/v1/...`, `/api/docs/` and the admin |
| `GET /favicon.ico ... 404` | The browser asks for an icon. There is none |
| `GET /healthz ... 200`, agent `Render/1.0`, from a private `10.x` address, every 5 seconds | Render's own health check. It appears only once the Health Check Path is saved; before that the log has no such line |

Saving a setting such as the Health Check Path **redeploys** the service. The log of that restart (about 35 seconds from
`Starting service...` to `Your service is live`) showed the migrations, the Gunicorn start, the two probes of `/` and then
the health checks every 5 seconds. A redeploy pulls the image tag `staging` again, so it can change the running version to
the newest build of `develop`: check the `x-release` header afterwards. (Whether a redeploy after a deploy by the workflow
keeps the pinned digest is not verified.)

Before the Gunicorn fix, the start-up also showed one **error** line, harmless:

```text
[ERROR] Control server error: [Errno 13] Permission denied: '/home/app'
```

Gunicorn 26 opens a control socket for its `gunicornc` tool, by default under the home directory of the user
(`$HOME/.gunicorn/gunicorn.ctl`, or `$XDG_RUNTIME_DIR`). The image runs as the user `app` that was created without a
home directory, so the socket cannot be created. Serving is not affected (`/healthz` answers `200`). The entrypoint
now starts Gunicorn with `--no-control-socket`, so images built after that change no longer show the line.

```bash
curl -sS -D - https://money-scribe-staging.onrender.com/healthz
```

Expected: `HTTP/2 200`, the header `x-release` with the full commit of the image, and the body `ok`. The answer comes
through Cloudflare (`server: cloudflare`), which is one of the proxies that `NUM_PROXIES` has to count.

## Step 6: the deploy hook and the GitHub environment `staging`

An image-backed service does not redeploy when a new image is pushed. A **deploy hook** (a URL of the service) starts a
deploy, and our workflow `deploy-staging.yml` calls it, pinned to the digest of the image of a given commit. The hook and
the address of the service live in a GitHub **environment** called `staging`, so that only runs from the branch
`develop` can read them.

### The Render side

1. Open the service `money-scribe-staging`, then **Settings**.
2. Under **Health Checks**, the **Health Check Path** must read `/healthz`. If the field is empty, click **Edit**, type
   `/healthz` and save (Render may redeploy). Without a path Render only checks that the port opens, so a deploy can be
   called successful before the app answers; with it, a new version goes live only once `/healthz` answers `200`.
3. **Deploy Hook** has a copy icon. Use only the icon. **Never open the link:** opening it starts a deploy, and the URL
   holds a key, so it is a secret ("Your deploy hook URL is a secret!", Render's documentation). If it ever leaks, use
   **Regenerate Hook** and update the GitHub secret.

What the hook answers (Render's documentation; `scripts/deploy-staging.sh` handles each one):

| Status | Meaning |
| --- | --- |
| 200 | The deploy started |
| 202 | Another deploy is running; this one starts after it (the default of workspaces created after 2025-07-14: one deploy at a time, the ones in between are skipped) |
| 400 | `imgURL` is invalid, or does not match the service's image (`ghcr.io/alihaidar0/money-scribe-backend`, lower case) |
| 401 | The key is wrong or old: the hook was regenerated |
| 404 | No service matches the hook |
| 409 | The service is suspended |

Render documents no limit on the number of deploys. `imgURL` accepts "a tag or a digest", but its page shows no
`ghcr.io` example: the first deploy by the workflow is what proves a digest reference works.

### The GitHub side

Repository `alihaidar0/money-scribe-backend`, **Settings**, **Environments**.

1. **New environment**, name `staging`, then **Configure environment**.
2. **Deployment protection rules**, left as they are:

   | Setting | Value | Why |
   | --- | --- | --- |
   | Required reviewers | Off | A reviewer would have to approve every deploy; the owner is the only one |
   | Wait timer | Off | No delay |
   | Custom rules with GitHub Apps | Not used | |
   | Allow administrators to bypass configured protection rules | On (the default) | |

3. **Deployment branches and tags**: the button reads **No restriction** at first. Change it to **Selected branches and
   tags**, choose **Add deployment branch or tag rule**, the type **Branch**, the name pattern `develop`, and save. The
   list then shows `1 branch and 0 tags allowed` and the rule `develop` ("Currently applies to 1 branch"). A run from any
   other branch is refused, so the manual deploy below must use `develop`.
4. **Environment secrets**, **Add environment secret**:

   | Name | Value |
   | --- | --- |
   | `RENDER_DEPLOY_HOOK_URL` | The Deploy Hook URL, pasted from the copy icon. GitHub never shows it again, only the name and "Last updated" |

5. **Environment variables**, **Add environment variable**:

   | Name | Value |
   | --- | --- |
   | `STAGING_URL` | `https://money-scribe-staging.onrender.com` (the address of the service, with `https://`, no trailing slash) |

   A variable is not secret: its value stays visible in the list.
6. Do **not** add the repository variable `DEPLOY_STAGING`. Until it is `true`, the job "Deploy to staging" of
   `docker.yml` is skipped and nothing deploys by itself. The repository variable `PUBLISH_IMAGE` = `ghcr` stays as it was
   (it makes every merge into `develop` build and publish the image).

### Deploy by hand (the setting for now)

Staging is deployed when you decide, because `develop` changes often, the free instance restarts on every deploy, and the
app developer should test against a stable contract. Every merge still publishes an image, so any commit can be deployed.

1. GitHub, **Actions**, **Deploy staging**, **Run workflow**.
2. **Use workflow from**: `develop` (the environment's branch rule refuses another branch).
3. **Commit**: empty deploys the latest commit of `develop`; or the first 7 or more characters of an earlier one (a
   rollback). The image of that commit must exist: it is found by its tag `sha-<short>`.
4. **Run workflow**, then open the run. The job "Deploy to staging" prints the digest, calls the hook (the URL is never
   printed), and waits up to 900 seconds until `https://money-scribe-staging.onrender.com/healthz` reports the same commit
   in its `X-Release` header. The run summary then shows the address, the commit, the image, the digest and the time it
   took.
5. Check it yourself:

   ```bash
   curl -sS -D - https://money-scribe-staging.onrender.com/healthz
   ```

   Expected: `HTTP/2 200`, `x-release` with the commit you deployed, the body `ok`.

To switch the automatic deploy on later, add the repository variable `DEPLOY_STAGING` = `true` (Settings, Secrets and
variables, Actions, **Variables**). Every merge into `develop` is then deployed by `docker.yml`. Delete the variable to
go back to manual deploys.

## Step 7: measure `NUM_PROXIES` with a temporary `GUNICORN_CMD_ARGS`

The app protects sign-in and registration with limits per client address, and the sessions list records a truncated
address. The request does not reach the app directly: it passes servers that each append the address they received it
from to the header `X-Forwarded-For`. `NUM_PROXIES` is the number of those servers that we trust. DRF counts that many
entries **from the right** and takes that entry as the client. The entries on the left can be written by the caller, so
they are never believed.

| `NUM_PROXIES` | Effect |
| --- | --- |
| Too low | The app reads a proxy's address: all users look like one client, so one tester's failures lock everyone and the sessions list shows the proxy |
| Too high | The app reads an entry the caller wrote: a cheater picks a new address each time and is never limited |
| Right | Each phone is its own client, whatever its country; phones that really share a public address (one Wi-Fi, some mobile carriers) still share that address's limits |

The number depends on the hosting, so it is measured on the real chain, once, and measured again if the hosting
changes. Staging started with the guess `1`.

### What `GUNICORN_CMD_ARGS` is

Gunicorn reads extra command-line options from the environment variable `GUNICORN_CMD_ARGS`. Our entrypoint
(`scripts/entrypoint.prod.sh`) sets it to the defaults below **only when it is not set**, so a value set on Render
**replaces all of the defaults**: the temporary value has to repeat the flags that must stay.

The default value (when nothing is set on Render):

```text
--bind=0.0.0.0:$PORT --threads=4 --timeout=120 --access-logfile=- --error-logfile=- --no-control-socket
```

The temporary value used for the measurement:

```text
--threads=4 --timeout=120 --access-logfile=- --error-logfile=- --no-control-socket --access-logformat='%(h)s xff=%({x-forwarded-for}i)s %(r)s %(s)s'
```

| Part | What it does | Why it is in the temporary value |
| --- | --- | --- |
| `--threads=4` | Four request threads per worker | Same as the default |
| `--timeout=120` | A worker silent for 120 seconds is restarted | Same as the default; the free instance is slow |
| `--access-logfile=-` | Writes one line per request to the log | Same as the default |
| `--error-logfile=-` | Writes Gunicorn's own messages to the log | Same as the default |
| `--no-control-socket` | No control socket under the home directory (the image's user has none) | Same as the default; without it the start logs an error |
| `--access-logformat='…'` | Changes what a request line contains | **The point of the measurement.** `%(h)s` is the address of the direct connection, `%({x-forwarded-for}i)s` is the whole `X-Forwarded-For` header, `%(r)s` is the request (`GET /healthz HTTP/1.1`), `%(s)s` is the status. The line reads `<peer> xff=<list> GET /healthz HTTP/1.1 200` |
| (no `--bind`) | | An environment variable is not expanded, so `--bind=0.0.0.0:$PORT` would bind to the literal text `$PORT` and the service would not start. Gunicorn binds to `0.0.0.0:$PORT` by itself when `PORT` is set |

### The steps

1. Service, **Environment**: add `GUNICORN_CMD_ARGS` with the temporary value, then save and deploy and wait for
   **Live**. A redeploy pulls the image tag `staging` again, so the running release may become the newest build of
   `develop`.
2. On a phone with **Wi-Fi off** (mobile data), note the phone's public address (any "what is my IP" page) and open
   `https://money-scribe-staging.onrender.com/healthz`. It shows `ok`.
3. **Logs**: find the `GET /healthz` line that holds the phone's address. Lines from Render's own health check come
   from a `10.x` address and show `xff=-`.
4. Count the entries of the list after `xff=` from the **right end** up to and including the phone's address. That
   count is `NUM_PROXIES`.
5. Check that a fake entry cannot move the count: send one request with a forged header and read its line.

   ```bash
   curl -sS -H 'X-Forwarded-For: 203.0.113.7' https://money-scribe-staging.onrender.com/healthz
   ```

   The forged address stays the **first** entry and the servers' entries stay on the right, so counting from the right
   still finds the real caller.
6. In one save: set `NUM_PROXIES` to the number and **delete** `GUNICORN_CMD_ARGS` (the log line holds client
   addresses, and the variable replaces the defaults). Save, deploy, wait for **Live**.

### What staging showed

| Entry of `xff=` (from the left) | What it was |
| --- | --- |
| 1st | The phone |
| 2nd | A Cloudflare server (a public address in a Cloudflare range such as `172.64.0.0/13`, for example `172.68.x.x` or `172.69.x.x`; the exact server changes between requests) |
| 3rd | Render's internal proxy (a private `10.x.x.x` address, which also changes) |

So there are two trusted servers on the right of the client, and the phone is the **3rd from the right**:
**`NUM_PROXIES = 3`**. The request with a forged address showed four entries (`203.0.113.7`, the caller, a Cloudflare
server, Render's proxy), so the forged entry stayed at the far left and the third from the right was still the caller.
`172.16.x.x` to `172.31.x.x` are the private ranges; `172.68.x.x` is not one of them.

Measure again after any change of hosting (a custom domain behind another proxy, another Cloudflare setting, a move
off Render) and for production, which has its own chain. The value is a variable on the service, so it is a one-minute
change.

## Step 8: check a deploy

What was run on staging after the first deploy, and what it showed. All values are invented examples; the real
`ADMIN_URL`, passwords, codes and tokens never go in a screenshot or a chat.

### The health check

```bash
curl -sS -D - https://money-scribe-staging.onrender.com/healthz
```

`HTTP/2 200`, the body `ok`, and `x-release` with the full commit of the image. It must equal the commit that was
deployed. `GET` and `HEAD` both answer `200`, so any monitor works.

### The admin panel

Open `https://money-scribe-staging.onrender.com/<ADMIN_URL>/` and sign in with the superuser created on Neon. It shows
a styled page ("Money Scribe administration", so static files are served by the image) with these apps: Groups, **Outbox**
(Outgoing emails, view only: the 6-digit codes are read here), the Periodic tasks of `django_celery_beat` (nothing runs
them on staging) and **Users**. Users lists one account, the superuser, active and staff, with an empty "Email verified
at" (a superuser made on the command line is not verified through the API). The default path `/admin/` answers `404`.

### The docs and the schema: staff only, and a 404 for everyone else

`/api/docs/` (Swagger UI) opens in a browser that is signed in to the admin. `/api/schema/` downloads the OpenAPI
document as a YAML file (that is its content type, so a browser saves it). Without a staff session both answer a plain
`404` with no `Location` header. They used to redirect to the admin sign-in, and that redirect revealed the secret
`ADMIN_URL` to any visitor; the redirect was removed for that reason.

### A full round trip through Swagger

Use invented data and a throwaway password of 15 to 128 characters that is not common and not in a known breach (four
unrelated words and a number work). The tokens in a response are secrets: blur them in any screenshot.

1. **`POST /api/v1/auth/register/`**, **Try it out**, replace the example body:

   ```json
   {
     "language": "en",
     "time_zone": "Europe/Berlin",
     "name": "Staging Tester",
     "email": "staging.test1@example.com",
     "password": "<your throwaway password>"
   }
   ```

   `201` with `email`, `code_expires_in` (600) and `resend_available_in` (60). The answer holds no code.
2. In the admin, **Outbox**, **Outgoing emails**: the newest email has the 6-digit code.
3. **`POST /api/v1/auth/verify-email/`**. Make a UUID for `device_id` (`python -c "import uuid; print(uuid.uuid4())"`)
   and keep it for the later calls:

   ```json
   {
     "language": "en",
     "time_zone": "Europe/Berlin",
     "email": "staging.test1@example.com",
     "code": "<the 6 digits>",
     "device": {
       "device_id": "<the UUID>",
       "device_name": "Swagger test",
       "platform": "android",
       "app_version": "0.0.1"
     }
   }
   ```

   `200` with `access_token`, `refresh_token`, `token_type` `Bearer`, `expires_in` 600 and `user`. Use the platform
   `android` so the tokens come in the body; `web` would carry the refresh token in a cookie.
4. **Authorize** (top right): paste the access token only, without the word `Bearer`, which Swagger adds. Swagger does
   not check the token; it only stores it, so a damaged token still shows "Authorized" and fails on the first call.
5. **`GET /api/v1/auth/profile/`**: `200` with `id`, `name`, `email`, `language` and `time_zone`.
6. **`POST /api/v1/auth/refresh/`** with `refresh_token` and the same `device` block: `200` with a **new** pair. The
   refresh token is single use; the old access token keeps working until it expires, because a refresh does not end the
   session. The old refresh token sent again within 30 seconds returns the same new pair (a lost answer is recoverable);
   later it ends the whole session with `session_token_reuse`.
7. **`POST /api/v1/auth/sign-out/`** with the body `{}` and the access token: `204`, no body.
8. After the sign-out, the profile call with the same access token and a refresh with the newest refresh token both
   answer `401`, with the code `session_signed_out`. The server checks the session on every request, so ending it
   cuts access at once, whatever the token's expiry. Signing out an already ended session answers the same `401`; a client
   treats it as "already signed out".

**The trap that cost a round trip: copy tokens with the copy icon.** A token contains `-` and `_` characters, so a
double-click selects only part of it, and a selection from the JSON line brings the quotes and the comma. A damaged
token answers `401 token_invalid` ("The token is not valid."). An expired access token (10 minutes) answers a different
code, `token_expired`, and a header with more than one `Bearer` answers `token_invalid` with the text "The access token
is not valid.".

### Changing `ADMIN_URL`

The path is a secret that hides the admin panel, so change it if it was ever shown or sent. Order matters, because a
change of a variable redeploys the service and a redeploy pulls the image tag `staging` again:

1. Deploy the version you want by hand first (Step 6), so the redeploy does not surprise you.
2. Generate the new value in the development container (`openssl rand -hex 6`), copy it into the password manager and
   run `clear`.
3. Service, **Environment**: replace the value of `ADMIN_URL` (12 characters, no `/`), save and deploy, wait for **Live**.
4. Check: the new address signs in; the **old** address and `/admin/` answer `404`; `/api/docs/` in a private window
   answers `404`.

## What the free plan does

Taken from Render's documentation on 2026-10-16 and from the console; check both before relying on a number.

- **Instance hours**: 750 free instance hours per workspace per calendar month. A service that is asleep does not use
  hours. One service that is always awake uses 720 hours in a 30-day month and 744 in a 31-day month (24 hours times the
  days), so the 750 hours cover **one** always-awake free web service and nothing more. Two such services would need
  about 1,440 to 1,488 hours; when the hours run out, Render suspends **all** free web services of the workspace until the
  next month, and the hours do not roll over. The documentation counts only free web services; it does not say whether
  the free Key Value instance uses hours. An uptime monitor would keep staging awake and use almost all of them; none is set up (deferred), so staging sleeps
  when idle and uses few hours.
- **Where to see the usage:** the web service's **Settings**, block **General**, the purple banner under **Instance
  Type** with the link "See remaining free usage". It opens the workspace's **Monthly Included Usage**: Free Instance
  Hours (x / 750), Included Bandwidth (x / 5 GB), Included Pipeline Minutes (x / 500), Included Services (x / 25) and
  Included Custom Domains (x / 2), followed by **Unbilled Charges** with the projected total of the month. The numbers
  seem to update with a delay (on the first day it showed 0.78 hours and 0 MB of bandwidth after many requests), so
  compare two days apart. The page warns that usage beyond the included limits is charged.
- **One free Key Value instance** and one free Postgres database per workspace. We use only the first.
- **No persistence**: a restart of the Key Value instance empties it. The effect is that an active sign-in lock or code
  cooldown is forgotten, which is acceptable on staging.
- **Free web services** sleep after 15 minutes without a request and need about a minute to wake. They cannot receive
  traffic from the private network (the Key Value instance is reached by the service calling it, which should be allowed;
  the first deploy shows whether it is).
- **Over the allowances**: with a card on file, bandwidth beyond 5 GB and build minutes beyond 500 are billed.

## If something fails

| Symptom | Likely cause |
| --- | --- |
| The Key Value form will not create the instance | No payment method on file in the workspace (Billing) |
| The instance is created in the wrong region | The region cannot be changed: delete the instance and create it again |
| The web service cannot reach `REDIS_URL` | A different region or workspace, or the External URL was copied instead of the Internal one |
| A shown price is not $0 | A paid compute plan was selected: delete the instance (staging holds nothing) and create it again on **Free** |
| The service's Settings show an empty Health Check Path | The path was never saved (the form's grey text is only a hint): **Settings**, **Health Checks**, **Edit**, `/healthz` |
| The web service form shows $7 / month | Render pre-selects a paid plan: select the **$0 / month** row before deploying |
| The log stops with a missing variable (`KeyError`) | A key is misspelled or missing: compare with the table of step 5 and edit the variable (a change redeploys) |
| The admin sign-in answers 400 | `ALLOWED_HOSTS` does not match the service's address: use the address shown at the top of the service page |
| `Control server error ... Permission denied: '/home/app'` in the log | Gunicorn's control socket has no writable home directory; harmless, see "What the first deploy shows" |
| The workflow fails with "The hook key is wrong or old (401)" | The hook was regenerated: copy the current Deploy Hook into the secret `RENDER_DEPLOY_HOOK_URL` |
| The run is refused or waits for approval | It was started from a branch other than `develop`, or a reviewer rule was added to the environment: use `develop` and leave the reviewers off |
| The workflow fails with "No image ..." | The image of that commit was not published: `PUBLISH_IMAGE` is unset, or the Docker workflow failed for it |
| The workflow fails with "The hook refused the image (400)" | `imgURL` does not match the service's image, or Render does not accept a digest reference: read the message and the Render service's **Events** |
| The run waits and ends with "did not report" | The new container did not start (a bad variable, `DATABASE_URL`, `REDIS_URL`), or the free instance took longer than 900 seconds: read the service's logs |
| No SSH or shell on the service | Not available on the Free plan: run `manage.py` commands from the development container against Neon |

## Where the values go next

The internal Key Value URL goes into the environment of the Render web service as `REDIS_URL`
([`staging.md`](staging.md), step 4), and nowhere else.
