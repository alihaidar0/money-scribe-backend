# ADR 0025: A scheduled readiness check keeps the free staging services in use

- Status: accepted
- Date: 2026-11-17

## Context

Staging runs on free tiers ([ADR 0011](0011-staging-hosting.md)): a Render web service, a Neon database and a Render
Key Value instance. They can all sit unused for weeks between two test sessions, and the owner wants to be sure none of
them is lost for that reason, and to learn early when one stops answering.

`/healthz` does not help with the database or Redis: it answers before either is touched, and the platform calls it every
few seconds, so a check of the dependencies there would keep a scale-to-zero database awake all day.

Facts read at their source on 2026-11-17 (free tiers change often; check the page before relying on one):

- Render ([free tier](https://render.com/docs/free)): a free web service spins down after 15 minutes without inbound
  traffic and wakes on the next request in about a minute. The page names no rule that suspends or deletes a free web
  service or a free Key Value instance for going unused. A free Key Value instance may be restarted at any time, which
  deletes its data. A free Render Postgres database expires 30 days after creation, but staging does not use it (the
  database is on Neon). Free instance hours are 750 a month per workspace; using them all suspends the free web services
  until the next month.
- Neon ([plans](https://neon.com/docs/introduction/plans), [scale to zero](https://neon.com/docs/introduction/scale-to-zero)):
  on the free plan the compute suspends after 5 minutes without a query and a new query wakes it; the pages name no rule
  that deletes a project for inactivity.
- Neon ([branch archiving](https://neon.com/docs/guides/branch-archiving)): a branch older than 14 days that was not
  accessed in the last 24 hours is moved to archive storage; connecting or querying unarchives it. The data is not
  deleted, and the first query after that is slower.
- GitHub Actions: the limits of the `schedule` event are in [ADR 0020](0020-scheduled-calls-instead-of-a-free-worker.md).

So the pages read do not say that anything on staging is deleted for being unused. A regular request is cheap insurance
against a rule that is not written there, keeps the database and the data warm, and a failed run is an early warning.

## Decision

- **One internal view, `GET /readyz`** (`config/readiness.py`), beside `/healthz`. It runs `SELECT 1` on the database and
  writes and reads one value in the cache, and answers `200 ok`, or `503` with the names that did not answer
  (`database`, `cache`). It is not part of the API: it is outside `/api/v1/`, not in `openapi/schema.yaml`, and no
  client calls it.
- **It is protected by a token.** The view does real work, so an open one would let anyone keep the database awake and use
  its free compute hours. `READINESS_TOKEN` is a long random value sent as `Authorization: Bearer <token>`, compared in
  constant time. An empty value switches the view off (`404`, as if it did not exist, the same pattern as
  [ADR 0020](0020-scheduled-calls-instead-of-a-free-worker.md)); a missing or wrong token is `401`. A failure is logged by
  exception class only, never with the error text, which can hold a connection string.
- **A scheduled workflow calls it every three days** (`.github/workflows/staging-check.yml`, `scripts/check-staging.sh`),
  from the default branch, and by hand. The script retries, because the first call after a pause takes about a minute,
  and passes the token through a curl configuration on standard input, so it is in no process argument. The token is a
  secret of the GitHub environment `staging`, the same value as the variable of the Render service.
- **Render's own health check stays on `/healthz`.**

## Consequences

- The owner sets `READINESS_TOKEN` on the Render service and as a secret of the environment `staging` once
  (`docs/staging.md`, step 9). Until both exist the workflow fails with a message that says which one is missing.
- A run wakes the web service, which then stays awake for the 15 minutes before it spins down again, so about eleven runs
  a month cost under three hours of the 750 free instance hours (an estimate from the figures above, not a measurement).
  A check every five minutes would use nearly all of them, which is why the interval is three days.
- The first run after a long pause also wakes Neon and takes about a minute; that is what the retries are for.
- Not verified: whether Render or Neon delete a free resource for inactivity under a rule that is not on the pages read
  here, how Render counts "inbound traffic" for a request made by GitHub's runners, and how long GitHub's scheduler
  delays a run. A failed run is the sign to check them.

## Not chosen

- **Calling `/healthz` only.** It wakes the web service and nothing else.
- **A sign-in request with an invented email.** It reads the database and writes counters in the cache, but it also
  records a failed-sign-in security event ([ADR 0022](0022-security-events.md)) on every run.
- **An uptime monitor every five minutes.** Deferred by ADR 0011 for the instance hours, and it would still not touch the
  database.
- **An open `/readyz`.** Anyone could keep the database awake; the token costs one secret.
