# ADR 0009: Sessions and device records

- Status: accepted
- Date: 2026-10-15

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
| `ended_at`, `end_reason` | `signed_out`, `revoked_by_user`, `password_changed`, `email_changed`, `token_reuse`, `expired`, `account_deleted` |

- Several sessions can share one `device_id` over time; each stays a separate session.
- There is no GPS and no location lookup. A coarse location from the address can be added later with a licensed offline
  database if the screen needs it, as its own decision.
- The push token of the device belongs to its session: ending the session removes it, so a signed-out device gets no
  more pushes (ADR 0005, `notifications`).

**Retention.** Ended sessions stay as history for **90 days after they end**, then a scheduled task (Celery Beat, daily)
deletes them. Sessions are deleted with the account. Access logs of the web server or proxy are an infrastructure
setting, decided with the hosting step.

**Operations** (shapes agreed in the endpoint request issues):

| Operation | What it does |
| --- | --- |
| list sessions | `state=active` (default) or `ended`, cursor pagination ordered `-last_active_at`, `-id`; each entry has `session_id`, `is_current`, `device_id` (so the app can group the sessions of one device), device name, platform, `os_version`, `app_version`, the truncated address, `last_active_at`, `created_at` and, for ended ones, `ended_at` and `end_reason`. The full address is never returned |
| end one session | ends it and its refresh token and removes its push token; another user's session answers `404` |
| end all other sessions | keeps the current one |

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
| the Firebase push token of the device (once the `notifications` feature exists) | send push notifications to the device | until the session ends or expires, at most 90 days; deleted at once when Firebase reports it invalid, and with the account |
| the first five characters of the SHA-1 hash of a new password | the server asks the Pwned Passwords breach service whether the password is known (k-anonymity: the password, the full hash and the user are never sent) | not stored; sent at registration and at later password changes |

Not collected: GPS or any device location, advertising ids, hardware ids, contacts. This data is for account security
only. It is not analytics, and no financial data is stored with it.

## Consequences

- `users` gains the `Session` model, a purge task, and the three session operations. The `device` object becomes part of the sign-in, verification and refresh contracts.
- The exact list above is the source for the privacy policy and the Data safety form; changing what is stored means
  changing this record first.
- The "Active sessions" screen needs no new data later.
