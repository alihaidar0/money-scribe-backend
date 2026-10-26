# ADR 0022: Security events: an append-only record of account security actions

- Status: accepted
- Date: 2026-10-25

## Context

The app needs a "Security activity" screen, and the Flutter developer asked for an audit trail of who did what and when,
without amounts or notes. Today the only trace of such an action is the `end_reason` of an ended session
([ADR 0009](0009-sessions-and-devices.md)). A password change, a new fingerprint key or a data export leaves nothing the
person can see, and a session record disappears 90 days after it ends. The data cannot be collected afterwards, so the
record is decided before the first action that needs it. It is personal data, so it stays small and is deleted on a
schedule, like the sessions.

## Decision

### The record

One row per event, in `users`, written only by one service function and never edited:

| Field | Notes |
| --- | --- |
| `id` | UUIDv7 |
| `user` | owner, `on_delete=CASCADE` |
| `type` | one of the types below |
| `occurred_at` | UTC instant |
| `session_id` | the session the event belongs to, or `null`. A plain id, not a foreign key: the session may be gone |
| `device_name`, `platform`, `app_version` | the device of the event, copied at that moment (which device: see below); `null` when there is none or it is not known |
| `sign_in_method` | `password`, `email_code` or `device_key`, on `signed_in` only |
| `kinds`, `count` | on `data_exported` and `data_imported` only: which data (`transactions`, `debts`, `salary_months`; an open list) and how many rows were written to the file or written by the import (the total over the kinds; skipped rows are not counted). Never a value |

There is no IP address, no user agent, no amount, no note and no name of a person or a category in an event.

**Which device an event carries.** For `signed_in`, `signed_out`, `password_changed`, `email_changed`, `data_exported` and
`data_imported` it is the device where the action was done. For `session_revoked`, `session_limit_reached` and
`token_reuse_detected` it is the device of the **session that was ended**, and `session_id` is that session, because that
is the device the person recognises or does not. For `device_key_added` and `device_key_removed` it is the device of the
key (`app_version` is `null` when the acting session is on another device). `password_reset` has no session, so its
device fields are `null`.

### Types and when they are written

| Type | Written when |
| --- | --- |
| `signed_in` | a sign-in, an email verification that signs a new account in, a device key sign-in |
| `signed_out` | a sign-out |
| `session_revoked` | the person ends another session or all others (one event per session) |
| `session_limit_reached` | a session is ended because the account reached its limit of active sessions and a new sign-in took its place (the event carries the device of the ended session) |
| `password_changed` | a password change |
| `password_reset` | a password reset is confirmed |
| `email_changed` | an email change is confirmed |
| `device_key_added` | a device key is registered |
| `device_key_removed` | a device key is removed by the person, by ending its session, or by a password or email change (one event per key) |
| `token_reuse_detected` | a used refresh token is replayed and the session is ended (ADR 0006) |
| `data_exported` | a CSV export is made |
| `data_imported` | a CSV import call that writes (a dry run writes nothing and records nothing) |

Not recorded: failed sign-ins (a count per day may follow as its own decision, so attacker input is never stored),
session expiry (no job writes it on time; an expired session simply leaves the list of active sessions) and the deletion
of the account. A deletion removes everything and keeps nothing ([ADR 0014](0014-password-reset-change-and-account-deletion.md)),
so it leaves only a log line with the user id and the request id, and the confirmation email.

### Writing

- The event is written in the same database transaction as the action. If the event cannot be written, the action does not
  happen.
- One service function writes them. Every action in the table has a test that asserts its event, and a test that lists the
  places that end a session or change credentials fails when one does not write it (the same guard as the push token
  removal planned in `docs/steps.md`, 10.10).
- An event is never edited or merged. A CSV import call that writes at least one row records one `data_imported`
  event, so a file of 10 000 rows is up to 20 events of at most 500 rows; a dry run, a failed call and a call that skips
  every row record none. A client may show consecutive `data_imported` events of one session, a few minutes apart, as
  one.

### Retention

- An event is kept **90 days**, then deleted by the scheduled purge (ADR 0020, or Celery Beat once a worker runs). Reads
  ignore an older row even before the purge has run, so what the person sees never depends on the job.
- Events are deleted with the account.

### The list for the privacy policy

The row added to the table of ADR 0009 (to be declared in the policy and in the Google Play Data safety form):

| Item | Purpose | Kept |
| --- | --- | --- |
| the type and time of an account security action, the device name, platform and app version of the session, how the person signed in, and for a CSV export or import which data and how many rows | show the person their security activity | 90 days, and deleted with the account |

## Consequences

- `users` gains the `SecurityEvent` model, the writing service and a daily purge. The read endpoint
  (`GET /api/v1/auth/session-history/`, cursor-paginated, newest first, the last 90 days only) came with the sessions
  step (8.4), together with the `session_revoked` and `session_limit_reached` entries.
- ADR 0009 gains the row above in the same change as this record, because changing what is stored changes that list first.
- The history screen shows the types; a client treats an unknown type as an open enum value (ADR 0021).
- Rejected: storing the truncated IP in an event (the session record already holds it while it lives, and an event needs
  none), one row per failed sign-in (noise, and it stores attacker input), and reading the history from the sessions
  table alone (it holds only how a session ended and disappears 90 days later).
