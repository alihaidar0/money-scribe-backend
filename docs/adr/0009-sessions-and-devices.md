# ADR 0009: Sessions and device records

- Status: accepted
- Date: 2026-10-15
- Amended: 2026-10-25, the privacy list gains the security events of [ADR 0022](0022-security-events.md).
- Amended: 2026-10-26, the sessions step: the list keeps its ended view under the name `status`, at most 10 live
  sessions, one live session per device, ending a session removes the fingerprint key of its device, a session whose
  refresh token ran out counts as ended, and the new end reasons `session_limit`, `replaced` and `password_reset`.

## Context

The client will get an "Active sessions" screen where the user sees where they are signed in and ends any session. The
screen comes later, but its data cannot be collected afterwards, so every session is recorded from the first sign-in. A
device name, an address and a user agent are personal data, so the record is kept small and is deleted on a schedule.

## Decision

**What the app sends.** At sign-in, at the email-code verification that signs a new account in, and at every refresh the
app sends a `device` object. Registration itself creates no session, so it sends none. The object is a label for the
user's own screen, not a security check, and the server never trusts it for access. Each refresh keeps `app_version`,
`os_version` and `last_active_at` current.

| Field | Rule |
| --- | --- |
| `device_id` | a random UUID that the app creates once at install and keeps in secure storage (in browser storage on the web, where it is not a secret). Never a hardware, Android or advertising id |
| `device_name` | readable name such as "Pixel 8" or "Chrome on Windows", at most 100 characters |
| `platform` | `android`, `ios` or `web` |
| `os_version` | text, optional |
| `app_version` | text, such as the version and build number |

**What the server records.** One `Session` row per sign-in, in `users`:

| Field | Notes |
| --- | --- |
| `id` | the session id, also the `sid` claim of the tokens (ADR 0006) |
| `user` | owner, `on_delete=CASCADE` |
| `device_id`, `device_name`, `platform`, `os_version`, `app_version` | from the app |
| `created_at`, `expires_at` | sign-in time and the 90 day limit |
| `last_active_at` | set on every refresh, and on normal calls at most once every 5 minutes |
| `ip` | the client address **truncated** to the network (IPv4 to the /24, IPv6 to the /48), at sign-in and at the last activity; the full address is never stored |
| `user_agent` | the web only, at most 255 characters |
| `refresh_hash`, `generation`, `rotated_at` | the refresh state of ADR 0006 |
| `sign_in_method` | `password`, `email_code` or `device_key`; the sessions that existed before it was stored are `password` |
| `ended_at`, `end_reason` | `signed_out`, `revoked_by_user`, `password_changed`, `password_reset`, `email_changed`, `token_reuse`, `expired`, `session_limit`, `replaced`, `account_deleted` (never returned: the session is deleted with the account) |

- Several sessions can share one `device_id` over time; each stays a separate session. **One live session per
  device:** a sign-in from a `device_id` that has a live session ends the older one with `replaced` (no event: the
  person signed in again on the same device), and if the old one is used again it gets `session_signed_out`.
- **At most 10 live sessions per account.** A new sign-in at the limit ends the least recently active live session
  with `session_limit`; that device gets `401 session_limit_reached` on its next call and a `session_limit_reached`
  entry is written (ADR 0022). The count and the ending happen under a lock on the user's row, so two sign-ins at once
  cannot pass the limit.
- **Live** means not ended and with a refresh token that has not run out. The refresh token lives 30 days from the last
  rotation and never past the 90 day limit, so a session nobody used for 30 days can no longer refresh: it counts as
  ended with the reason `expired` and `ended_at` the moment its token ran out. This is worked out when the list is read
  and nothing is written for it.
- There is no GPS and no location lookup. A coarse location from the address can be added later with a licensed offline
  database if the screen needs it, as its own decision.
- The push FID of the device belongs to its session: ending the session removes it, so a signed-out device gets no
  more pushes (ADR 0005, `notifications`).

**Retention.** Ended sessions stay as history for **90 days after they end or expire**, then a scheduled task (Celery
Beat, daily; later the scheduled call of [ADR 0020](0020-scheduled-calls-instead-of-a-free-worker.md)) deletes them. The
list never shows an older one, even before the task has run. Sessions are deleted with the account. A live session is kept
at most 90 days, so the worst case is 180 days. Access logs of the web server or proxy are an infrastructure
setting, decided with the hosting step.

**Operations** (shapes agreed in the endpoint request issues):

| Operation | What it does |
| --- | --- |
| list sessions (`GET /auth/sessions/?status=`) | `active` (default): the live sessions, the current one first, then `-last_active_at`, `-id`, always the whole list (at most 10) in the cursor envelope; `ended`: the sessions that ended in the last 90 days, `-created_at`, `-id`, in pages. Each entry has `id`, `device_id` (so the app can group the sessions of one device), device name, platform, `os_version`, `app_version`, `signed_in_at`, `last_active_at`, `expires_at`, `is_current`, `sign_in_method`, the network (the address cut to its /24 or /48), and `ended_at` and `end_reason` (null while active). The full address and the user agent are never returned |
| end one session (`DELETE /auth/sessions/{id}/`) | ends it (`204`; one already over is also `204`; another user's, or an unknown id, is `404`). The current session is a sign-out and keeps the device key. Another session ends with `revoked_by_user`, a `session_revoked` entry is written and the fingerprint key of that session's device is removed in the same transaction (not when it is the current device), and its push FID goes with it once the notifications exist |
| end all other sessions (`POST /auth/sessions/revoke-others/`) | needs the password (the lock shared with the password change); ends every session but the current one and removes every device key but the current device's, those of signed-out phones included; answers `{ended, device_keys_removed}` |
| the security activity (`GET /auth/session-history/`) | the events of [ADR 0022](0022-security-events.md), newest first, in pages |

**New-device email.** When a sign-in comes from a `device_id` that has no session for that user within the retention
period, the server emails the user, except for the very first sign-in of an account (registration). The email shows the
device name, the truncated address and the time, and tells the user to change the password if it was not them. It has no
link until the web app has a domain, no financial data, and is written in the user's language (`docs/api-fields.md`).
These emails are throttled.

**The list stored for the privacy policy** (to be declared in the policy and in the Google Play Data safety form):

| Item | Purpose | Kept |
| --- | --- | --- |
| device name, platform, OS version, app version, random device id | show the user their sessions | until the session ends, then 90 days |
| truncated IP address | show and investigate sign-ins | same |
| user agent (web) | show the user their sessions | same |
| sign-in, last-active and end times and the end reason | show the user their sessions | same |
| the Firebase installation ID (FID) of the device (once the `notifications` feature exists) | send push notifications to the device | until the session ends or expires, at most 90 days; deleted at once when Firebase reports it invalid, and with the account |
| the public key of the device's sign-in key, its `device_id`, device name, platform, whether it was attested, and its times ([ADR 0018](0018-sign-in-with-a-device-key.md)) | let the person sign in with a fingerprint after a sign-out | until the key is removed, at most 30 days after its last use; deleted when the password or email changes, and with the account |
| the type and time of an account security action, the device name, platform and app version of its session, how the person signed in, and for a CSV export or import which data and how many rows ([ADR 0022](0022-security-events.md)) | show the person their security activity | 90 days, and deleted with the account |
| the first five characters of the SHA-1 hash of a new password | the server asks the Pwned Passwords breach service whether the password is known (k-anonymity: the password, the full hash and the user are never sent) | not stored; sent at registration and at later password changes |

Not collected: GPS or any device location, advertising ids, hardware ids, contacts. This data is for account security
only. It is not analytics, and no financial data is stored with it.

## Consequences

- `users` gains the `Session` model, a purge task, and the three session operations. The `device` object becomes part of the sign-in, verification and refresh contracts.
- The exact list above is the source for the privacy policy and the Data safety form; changing what is stored means
  changing this record first.
- The "Active sessions" screen needs no new data later.
