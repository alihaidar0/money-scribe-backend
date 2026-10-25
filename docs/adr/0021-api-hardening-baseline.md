# ADR 0021: API hardening baseline: request id, no-store, text rules, per-user limits and a size cap

- Status: accepted
- Date: 2026-10-25

## Context

The Flutter developer proposed security requirements for the ledger, debts, reports, CSV and session endpoints, based on
the OWASP API Security Top 10 2023 (broken object level authorization, broken object property level authorization and
unrestricted resource consumption). Most of them already hold: every query is scoped to the caller and every endpoint
gets an isolation test (ADR 0005), ids are UUIDs, only declared fields are writable and anything else is
`400 read_only`, creates are idempotent and edits are versioned (ADR 0016), and the password checks are locked (ADR 0014).

A check of the code on 2026-10-25 found five gaps that every endpoint of stage 3 would otherwise inherit:

- there is no request id, so a failure the app shows cannot be matched to a log line without personal data;
- only `/healthz` sends `Cache-Control: no-store`;
- only the authentication calls have rate limits, and the limits are per client address, not per signed-in user;
- there is no cap on the size of a request body;
- there is no shared rule for user-entered text.

This record closes them before the first ledger endpoint, so the later issues only use what is here.

## Decision

### Request id

- Every response under `/api/v1/` carries `X-Request-ID`, errors included. A client may send its own; it is kept only
  if it is 8 to 64 characters of letters, digits and hyphens. Anything else, or nothing, is replaced by a generated
  random UUID, so a client cannot put its own text into a log line.
- The id is written to every log line of the request (a logging filter adds it; a line outside a request shows `-`) and
  is listed in the CORS exposed and allowed headers.
- The id carries no personal data. The app may show it on a "Something went wrong" screen.

### No-store

- Every response under `/api/v1/` sends `Cache-Control: no-store`: success, error, `429` and `503`, and later the CSV
  export. It is a middleware, so a response that does not come from a view (a rejected request body, for example)
  has it too.
- `X-Content-Type-Options: nosniff` is already on in the production settings and stays.
- HSTS is implemented and set by `SECURE_HSTS_SECONDS`; it stays at `0` until the owner confirms HTTPS on the final
  domain (`docs/steps.md`, section 12). The minimum TLS version is a hosting setting that is **not verified** yet and
  is checked when production hosting is chosen.

### Text rules

One shared field and validator in `common` for every user-entered text field (names, notes, a person's name):

- trim, then normalize to Unicode NFC, then count the length in code points;
- reject control characters, including newline and tab;
- reject the bidirectional override, embedding and isolate characters (U+202A to U+202E and U+2066 to U+2069), which can
  make text display as something it is not;
- keep the direction marks (U+200E, U+200F, U+061C) and the joiners (U+200C, U+200D), because Arabic text needs them;
- a rejected value is `400 invalid_characters` on the field. The server never edits a value beyond trimming and NFC.

It applies to the profile `name` and the fixed item `name` now, and to every text field of the later issues. It does not
apply to passwords (NFKC and no trimming, ADR 0001 and 0014), emails, the 6-digit codes, enum fields or the device
label of ADR 0009, which is a label the app builds.

### Per-user rate limits

A limit per signed-in user and route group, in addition to the limits the authentication calls already have. Each
operation belongs to exactly one group, and a call is counted only there: a list call with a `search` parameter counts
in the search group, not also in the reads. A call over its limit is `429` with the existing code `throttled` and
`Retry-After` in whole seconds.

| Group | Limit | Why this number |
| --- | --- | --- |
| Reads | 300 per minute | a screen makes a list call and a day totals call for each scroll |
| Writes | 60 per minute | a person adds a record every few seconds at most |
| Reports, Overview, day totals | 60 per minute | changing the month calls a report a few times |
| Lists with `search` | 30 per minute | a slower query; the app waits 500 ms after the last keystroke |
| CSV export | 5 per hour | a full copy of the data, behind a password step-up |
| CSV import, dry runs included | 120 calls per hour | 10,000 rows are 20 calls of 500, doubled by the dry run, so about three files an hour |
| Session actions | 30 per hour | ending sessions; the password lock covers "end all other sessions" |

- Counters are atomic fixed windows in Redis, as the authentication limits are (ADR 0006), keyed by the user id, so
  all devices of one person share them. A fixed window allows a short burst at a boundary.
- **If the limit store is down:** reads and writes keep working, because a cache outage must not take the app down.
  CSV export, CSV import and "end all other sessions" answer `503 service_unavailable` with `Retry-After`, because they
  are the operations a limit protects most. Ending one session keeps working, because it only reduces access.
- The numbers are a starting point set in the settings. Changing one is a settings change recorded here, not a new
  record. The production checklist item "rate limiting by address and by account" (`docs/steps.md`, section 11) stays
  open for the limits by client address and the proxy count.

### Request size

- A request body over **256 KB** is `400 request_too_large` (`attr` `null`), checked with and without a
  `Content-Length`. The CSV import has its own larger limit of **1 MB**, set with that endpoint: 500 rows of text can
  reach about 0.6 KB each in the worst case, which is about 300 KB.
- Django's body size setting may not cover the stream that DRF's parsers read. That is **not verified**; the
  implementation checks the header, bounds the read of a body that has none, and a test sends both.

### Not chosen: encryption of free-text fields in the application

Notes, a debt's person and names are not encrypted by the application. The server needs the plain text to search notes,
to build reports and to write emails, so a key held by the same server would sit next to the data it protects. It would
help against a stolen database dump, not against a compromised server, and it would end the search of notes. What
protects them instead: encryption at rest and encrypted backups at the hosting provider, no financial text in logs, and
access control. The provider settings are **not verified** for the staging or the production plans; the owner checks
them before real financial data is stored (`docs/steps.md`, section 12). If the threat model later includes provider
level access, envelope encryption with a managed key is the next step, and the search of notes would go.

## Consequences

- A new middleware (request id, no-store), a logging filter, a text field and validator in `common`, per-user throttle
  classes with a group on each view, and a body size check. Each is tested; the limits are documented as `429` in the
  schema, which already declares it on every operation.
- ADR 0004 gains `invalid_characters` and `request_too_large` (decided, not yet implemented until the code lands).
- `X-Request-ID` is documented as a response header of every operation in the schema.
- The client treats every enum in a response as open, so adding a value is not a break (agreed with the Flutter developer
  on 2026-10-25). The sentence goes into `docs/api-workflow.md` when this record is accepted.
- `docs/api-conventions.md` describes these rules in the pull request that implements them.
- Rejected: one global limit for all routes (a search or an export would share a budget with a plain read), a limit per
  address only (users behind one carrier address would share it), and a "recent sign-in" window for the export
  (extra state; the password in the request, ADR 0014, is simpler).
