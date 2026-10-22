# ADR 0014: Password reset, password change and account deletion

- Status: accepted
- Date: 2026-10-22

## Context

The app has three account screens that wait for the server: a forgotten password, a password change and the deletion of
the account (issue #40). All three act on the credential that protects every financial record, so each is an attack
target: a reset can be used to learn who has an account or to take one over, a change or a deletion can be driven by
whoever holds a stolen access token, and a deletion cannot be undone. This record fixes their contract and the
protections. It follows the OWASP guidance for forgotten-password flows, NIST SP 800-63B-4 for the password rules and the
session rules of [ADR 0006](0006-authentication-tokens.md).

## Decision

### Password reset: three calls, no sign-in

| Call | Body | Answer |
| --- | --- | --- |
| `POST /api/v1/auth/password-reset/request/` | `email` | `200` `{code_expires_in, resend_available_in}` |
| `POST /api/v1/auth/password-reset/verify/` | `email`, `code` | `200` `{reset_token, expires_in}` |
| `POST /api/v1/auth/password-reset/confirm/` | `reset_token`, `password` | `204` |

- **The request reveals nothing.** The answer is the same for an unknown, a pending, a deactivated and a real address, and
  the limits are keyed by the address whether or not it has an account. Only a verified, active account is sent a code, in
  its own language. The reset code shares the hourly budget and the 60 second cooldown of every other email code
  (`resend_too_soon`, `resend_limit_reached`). The residual difference is the time to send the email, which disappears when
  delivery becomes asynchronous (ADR 0006).
- **The code** is 6 digits, lives 10 minutes, allows 5 wrong tries and is stored as a keyed hash, exactly like the
  registration code, and uses the same checking code. An address with nothing to verify is answered like a real one. A reset
  code is a different purpose: it cannot verify an email, and a registration code cannot reset a password. An account with
  no live reset code (none asked for, or already spent) is answered exactly like an address with nothing to verify, from the
  same cache state: anyone can make the server note a send for any address by trying to register it, so answering from the
  database for a real account and from the cache for the rest would tell them apart. A spent code therefore answers
  `code_invalid` with `attempts_left`, not `code_expired`.
- **The code is spent for a reset token.** The token is 256 random bits (`secrets.token_urlsafe(32)`), stored only as its
  SHA-256 hash, bound to one user, valid for 10 minutes and single use. At most one exists per user, and a new reset
  request deletes it. Verifying does not sign anyone in. A separate token, instead of sending the code with the new password,
  lets the app check the code before it asks for the password, and keeps the guessable 6 digits out of the last step.
- **Confirm** accepts a token that is unknown, used, expired or belongs to a deactivated account only as
  `400 reset_token_invalid` (`attr` `reset_token`), one code for all four so that it tells nothing. The password rules
  apply (`password_*`, `attr` `password`); a failed rule keeps the token so the user can try another password until it
  expires. Success ends **all** the user's sessions (`session_password_changed`), deletes the pending codes and token,
  and emails the owner. It answers `204` and signs nobody in.
- **A reset may reuse the current password.** `password_unchanged` is not applied here: the reset proves the mailbox, not the
  old password, and refusing a repeat would give whoever reads the mailbox a free, unlimited way to test guesses of the
  current password.

### Password change: signed in, with the current password

`POST /api/v1/auth/password/change/` with `current_password` and `password` (the new one; the field is named as at
registration so the `password_*` errors keep their `attr`).

- A wrong current password is `400 invalid_password` (`attr` `current_password`), **not** `401`: the app signs the user out
  on a `401`, and the session here is fine.
- The new password equal to the current one (after NFKC normalization) is `400 password_unchanged` (`attr` `password`). It
  is checked before the other rules, because the current password may be older than they are. The other rules are the
  registration rules. The new password is judged only after the current one is proved, so a wrong current password
  tells nothing about the new one.
- Success: every **other** session ends (`session_password_changed`); this one continues, its refresh token is rotated and
  the answer is the new token pair (the shape of a refresh, `Cache-Control: no-store`). Outstanding reset codes and
  tokens are deleted and the owner is emailed.

### Account deletion: signed in, with the password

`POST /api/v1/auth/account/delete/` with `password`, answer `204`. It is `POST`, not `DELETE`, because a `DELETE` body has no
defined meaning (RFC 9110) and proxies and clients may drop it, and the confirmation must not be dropped. A wrong password
is `400 invalid_password` (`attr` `password`).

- **What is deleted:** the user row, and through `on_delete=CASCADE` everything the user owns, in one transaction: sessions
  (so every token stops working at once; a request with one answers `401 token_invalid`), email codes, the reset token and,
  as later steps add them, accounts, categories, transactions, salary months, debts and settings (ADR 0001). The answer
  is `204` only after the commit.
- **What is kept:** nothing about the account. There is no soft delete, no anonymised copy and no grace period; deletion is
  immediate and cannot be undone, and the address can be registered again. The address is used once more, for the
  confirmation email, and then forgotten. Rate-limit counters are keyed by a hash and expire by themselves within the hour.
  Server and proxy access logs, and the hosting provider's database backups, follow the provider's own schedule and are an
  infrastructure matter decided with the hosting step; the application never restores a deleted account. On development
  and staging the email outbox keeps a readable copy of sent emails for 24 hours (ADR 0010); those environments hold
  invented data only.
- This is the statement for the privacy policy and for the Google Play Data safety form: the user can delete the account
  and all its data inside the app, with no request to a person.

### The lock on the current password

Changing the password and deleting the account both ask for the current password, so a stolen access token could be used to
guess it there. Wrong answers are therefore counted per **account**, shared by the two operations and independent of the
client address: 5 wrong answers lock both for 15 minutes (`429 password_attempts_exceeded`, `Retry-After` the remaining time,
even for the right password). The answer that reaches the limit is itself the `429`. A right password starts the count again.
Sign-in is not affected, so the lock cannot be used to keep the owner out. Like the other limits it is atomic and fails
closed: if the store is down, the call answers `503 service_unavailable`.

### Limits per client and fail closed

Each of the five calls has its own per-client rate (`password_reset_request` 20/hour, `password_reset_verify` 60/hour,
`password_reset_confirm` 30/hour, `password_change` 30/hour, `account_delete` 10/hour; `DEFAULT_THROTTLE_RATES`) and answers `429`
with `Retry-After` and the code `throttled`. All five fail closed with `503` when the limit store is down and declare
that in the schema.

### Emails

Three emails, in the user's language (`docs/api-fields.md`), with no link and no code except the reset code: the reset
code, "your password was changed" (after a reset or a change, so a change by a stranger is noticed) and "your account was
deleted". Delivery failures are logged without details and never fail the call.

### Housekeeping

A daily task, `users.tasks.purge_expired_secrets`, deletes expired email codes and reset tokens. An expired item is already
useless and is answered exactly like a missing one, so the user notices nothing.

## Not chosen

- **A link in the email instead of a code.** The web app has no domain yet (ADR 0006); a code works in the mobile app and
  the web form alike, and is the shape the registration flow already uses.
- **A reset token as a JWT.** It could not be single-use or revoked without server state, and it adds nothing a random,
  hashed, single-use token does not.
- **Signing the user in after a reset.** The agreed flow ends at the sign-in screen; a reset is the moment to prove the new
  password at sign-in, on the device.
- **`DELETE` with a body, a soft delete, a grace period.** See above; a grace period would keep financial data of someone
  who asked for its removal.

## Consequences

- `users` gains the `PasswordResetToken` model, the `password_reset` code purpose (one migration), the three emails, the
  password-confirmation lock in `users.limits`, five operations and the purge task. No new environment variable: the
  numbers are settings constants next to the other code and lock limits.
- The registry of ADR 0004 gains `password_unchanged`, `password_attempts_exceeded` and `reset_token_invalid`, and
  `invalid_password` is also raised by the account deletion (`attr` `password`).
- Nine operations can now answer `503` (eleven with the email change, ADR 0015); the documents that listed four are updated.
- The name edit and the email change of step 8.3 are in [ADR 0015](0015-profile-name-and-email-change.md).
