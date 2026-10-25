# ADR 0020: Scheduled calls from GitHub Actions replace a worker while hosting is free

- Status: accepted
- Date: 2026-10-25

## Context

Pay-day reminders (step 10.10) and the daily purges need something that runs on a schedule. The plan was a Celery worker
with Beat ([ADR 0011](0011-staging-hosting.md)). A Render free web service has no background worker and spins down after
15 minutes without inbound traffic, and a worker is a paid instance. So on free hosting nothing runs by itself, and a
sleeping service cannot send anything.

Facts read at their source on 2026-10-25 (free tiers change often; check the page before relying on a number):

- Render ([free tier](https://render.com/docs/free)): a free web service spins down after 15 minutes without inbound
  traffic and wakes on the next HTTP request in about one minute; instance hours are counted only while it runs (750 a
  month per workspace); outbound traffic on ports 25, 465 and 587 is blocked, port 443 is not listed as blocked; Render
  may suspend a free service that starts an uncommonly high volume of public traffic, without giving a number.
- GitHub Actions ([schedule event](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)):
  the shortest interval is five minutes; a run can be delayed under load and queued jobs may be dropped; in a public
  repository scheduled workflows are disabled after 60 days without repository activity; standard runners are free for
  public repositories ([billing](https://docs.github.com/en/billing/managing-billing-for-your-products/about-billing-for-github-actions)).
- Cloudflare Workers ([limits](https://developers.cloudflare.com/workers/platform/limits/)): five Cron Triggers per
  account on the free plan, 100,000 requests a day, 10 ms of CPU time per Cron Trigger invocation, 15 minutes of wall
  time per scheduled run. The pages do not state a shortest interval or whether a payment method is needed.
- Firebase Cloud Messaging is listed as no-cost on [Firebase's pricing page](https://firebase.google.com/pricing), with
  no limits stated there.

No free always-on worker host was found that could be verified (Fly.io has no free tier for new organizations and
the pricing page of Koyeb shows no free compute), so the schedule moves outside the worker: a free scheduler calls the
service, and the service does the work in the request.

## Decision

- **The trigger is a scheduled GitHub Actions workflow** that calls one internal endpoint of the backend about once an
  hour. The workflow retries with a generous timeout, because the first call after a sleep waits about a minute for the
  service to wake. It runs from the default branch, uses a minute that is not the start of an hour (GitHub's own advice
  against delays), and holds the address and the token as secrets of a GitHub environment, never in the repository.
- **Cloudflare Workers with a Cron Trigger is the alternative**, used if GitHub's delays, dropped runs or the 60-day
  pause get in the way. A small Worker with a `scheduled` handler makes the same call with the same token. Nothing of it
  is built until it is needed, and its script would hold no secret.
- **The work is a plain service function** (for example `send_due_reminders(now)`) that the endpoint and a later Celery
  task both call, so moving to a paid worker with Beat is a change of caller, not a rewrite.
- **The endpoint is internal.** It is not a user operation and has no user session:
  - it is authenticated by a long random token in a header, compared in constant time; the token is an environment
    variable that is empty by default, and an empty value switches the endpoint off (it answers as if it did not exist);
    a new variable goes into `.env.example` and `docker-compose.yml` with an empty value;
  - it is idempotent: a repeated, late or doubled call never sends a reminder twice (the due reminders are decided and
    recorded in the database, per user and per day), so a delayed or retried run is harmless;
  - it does a bounded amount of work per call and answers without a body; it never logs tokens, FIDs, amounts or
    emails;
  - whether it appears in `openapi/schema.yaml` is decided when it is built, through the `api-endpoint` skill, with
    its status codes and error codes registered in ADR 0004 first.
- **The tick runs about hourly**, not every few minutes. A reminder is due at a time of day in the user's own
  `time_zone` (`docs/api-fields.md`), and an hourly tick is enough for that. A short interval would also keep the
  service awake all month and use up the 750 free instance hours (an estimate from the figures above, not a
  measurement).
- **The same call can run the daily purges** (old outbox emails, unverified accounts, ended sessions), as separate
  bounded steps.
- **A real worker with exactly one Beat replaces this** when hosting is paid, and always in production
  (`docs/steps.md` section 12). Free hosting is not a platform for real users' financial data (ADR 0011).

## Consequences

- Step 10.10 reads "pay-day reminders through a scheduled call (this record), later through Celery Beat", and the
  `docs/staging.md` and `docs/steps.md` lines that say reminders and pushes wait for a paid worker point here.
- The owner creates the GitHub environment and its two secrets by hand, once, when the endpoint exists. The repository
  holds only the workflow file.
- Reminders can arrive late: up to the delay of GitHub's scheduler plus the wake-up of the service. They carry no
  amount or name, so a late one is an inconvenience, not an error.
- The scheduled workflow stops after 60 days without repository activity in a public repository. The repository is
  expected to stay active; if the reminders stop, the first thing to check is the Actions tab, and the workflow is
  re-enabled there.
- The first call after a sleep also wakes Neon, so it is slower than the rest.
- Not verified: whether outbound calls to Google's FCM endpoint from a free Render service are always accepted (the
  free-tier page lists no such restriction but does not name FCM), the exact meaning of "repository activity" for the
  60-day rule, and Cloudflare's shortest cron interval and payment-method requirement. Check these when the work starts.

## Not chosen

- **cron-job.org.** Free, every minute, with custom headers, but a request that runs longer than 30 seconds times out
  and a job is disabled after 25 failures in a row, which a sleeping service would cause.
- **Upstash QStash.** Free plan of 1,000 messages a day and 10 active schedules, with responses allowed up to 15
  minutes; it is a reasonable alternative, but it adds a third service and every schedule trigger counts as a
  message.
- **A paid Render background worker now.** The clean answer, and still the plan for production, but staging holds only
  invented data and the cost is not needed yet.
