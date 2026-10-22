# ADR 0015: Editing the name and changing the sign-in email

- Status: accepted
- Date: 2026-10-22

## Context

The profile screen lets the user edit the display name and change the email address. The name is harmless; the address is
the identity of the account, the place every code is sent and the way back in after a forgotten password, so changing
it is as sensitive as changing the password. [ADR 0014](0014-password-reset-change-and-account-deletion.md) covers the
password side; this record follows the same rules (OWASP guidance for changing an email, NIST SP 800-63B-4 for
verifying a new address). The shapes were agreed in `docs/api-fields.md`; this record fixes them and the protections.

## Decision

### The name

`PATCH /api/v1/auth/profile/` with `{"name": "..."}`, answer `200` with the profile. The name is 1 to 100 characters,
trimmed, any Unicode text (ADR 0001); an empty or whitespace-only name is `400 blank`. **Only `name` is accepted.** Any other
field (`email`, `language`, `time_zone`, `id`, ...) is refused with `400` and the code `read_only` on that field, instead
of being silently ignored, so a client that believes it changed the email or the language finds out at once. The email
changes only through the flow below; `language` and `time_zone` come from the device at sign-in and from the settings
endpoint of a later step. `PUT`, `POST` and `DELETE` on the profile stay `405`.

### The email: a code to the new address, then the switch

| Call | Body | Answer |
| --- | --- | --- |
| `POST /api/v1/auth/email-change/` | `email` (the new one), `current_password` | `200` `{email, code_expires_in, resend_available_in}` |
| `POST /api/v1/auth/email-change/confirm/` | `code` | `200` with the updated profile |
| `DELETE /api/v1/auth/email-change/` | none | `204` |

- **The request** needs the bearer token **and** the current password. The password check is the one of the password change and
  the account deletion, with the same per-account lock: five wrong answers lock all three for 15 minutes
  (`429 password_attempts_exceeded`), so a stolen access token cannot guess the password here either. A wrong password is
  `400 invalid_password` (`attr` `current_password`).
- **The code** goes to the **new** address, never the old one: it proves the user can read that mailbox. It is 6 digits, lives
  10 minutes, allows 5 wrong tries and is stored as a keyed hash, exactly like the other codes, and it is checked by the same
  code. The address it was issued for is stored with it (`EmailCode.payload`), so the confirmation takes the address from the
  server's record, never from the request. The old address stays the sign-in identity until the code is accepted.
- **One pending change** per account: a new request replaces the earlier one (its code stops working), and `DELETE` cancels
  it (idempotent, `204` also when nothing is pending).
- **A taken address is a `409 email_already_registered`** (`attr` null). Here, and only here, the API says an address is
  registered: the caller is signed in and has just proved the password, and the alternative would be to accept the code and
  fail later, which is worse for the user. A pending (unverified) registration counts as taken. The same check runs again at
  the confirmation, because the address may have been registered in between; that `409` keeps the code. The address the
  account already has is `400 email_unchanged` (`attr` `email`).
- **Limits.** The code is mailed to an address the user chooses, so the call could be used to send mail to strangers or to
  probe which addresses are registered. Every attempt therefore counts against a cap of **5 changes per account per hour**
  (`429 resend_limit_reached`, with `Retry-After`) *before* anything else is judged, including the attempts refused with
  `409`; the new address has the 60 second cooldown and hourly budget shared with every other code (`resend_too_soon`); and the
  per-client rate limit applies. A dead code (too many wrong tries) is `429 code_attempts_exceeded`, and asking again is paced by the
  new address's budget. All of it fails closed with `503` when the limit store is down, and both calls declare that.
- **Confirmation** switches the address and marks it verified, ends every **other** session (`session_email_changed`; this one
  continues with the same tokens, which carry no email), deletes outstanding password reset codes and tokens (they were mailed
  to the old address, whose owner must not be able to use them against the new one) and emails the **old** address. That notice
  does not name the new address and cannot offer a recovery link; it says someone who knows the password may have taken over the account.
- With no pending change, a code is `400 code_expired`: the caller is signed in, so there is nobody to hide it from (the
  reset flow answers differently on purpose, ADR 0014).

### What is not decided here

Recovery after a takeover by someone who knows the password and controls a mailbox is out of reach of any email flow: that
person already holds the account. Two-factor sign-in would change that and is a separate decision.

## Not chosen

- **Confirming through both mailboxes.** A code to the old address as well doubles the friction; the password plus a code from
  the new mailbox is the standard, and the old address is told afterwards.
- **Changing the address at once and verifying later.** It could leave the account on an address nobody can read.
- **A second path for the name**, or a language/time zone edit on the profile. They belong to the settings of a later step.

## Consequences

- `users` gains the `email_change` code purpose and `EmailCode.payload` (in the same migration as ADR 0014), two emails, the
  per-account cap in `users.limits`, and three operations plus the `PATCH` of the profile.
- The registry of ADR 0004 gains `email_unchanged`; `read_only` is a framework-style code like `blank` and `required`.
- Eleven operations can now answer `503`.
