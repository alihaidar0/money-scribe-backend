# ADR 0004: Documented error statuses and error codes

- Status: accepted
- Date: 2026-10-15

## Context

Every error already has one body, `{type, errors[]}` (see `docs/api-conventions.md`). What had to be agreed is which HTTP
statuses the contract documents and which stable `code` values the app can tell apart: the client shows a different
message, or takes a different action, for each of them.

## Decision

The contract documents **400, 401, 403, 404, 409, 412, 422 and 429** for each operation that can return them. The package setting
`DRF_STANDARDIZED_ERRORS["ALLOWED_ERROR_STATUS_CODES"]` lists 400, 401, 403, 404 and 429, and each status added there gets
its enum name in `ENUM_NAME_OVERRIDES`. **`409` is declared per operation instead.** The package documents any extra
status listed in that setting on *every* operation, which would put a conflict on plain reads, so an operation that can
conflict says so with the shared response, `@extend_schema(responses={..., 409: CONFLICT_RESPONSE})`, from
`config/api_errors.py`. A conflict is raised with `config.exceptions.conflict(code)`, and a throttled answer with
`config.exceptions.throttled(code, wait)`, which sends `Retry-After`.

| Status | Meaning here | Notes |
| --- | --- | --- |
| 400 | invalid input or an unreadable body | one shared schema (`Error400Response`); a rule that depends on state but belongs to one field is also a `400` on that field |
| 401 | not authenticated, wrong credentials, or the token or session is no longer valid | sends `WWW-Authenticate: Bearer` (RFC 6750); the `code` says which |
| 403 | authenticated but not allowed | never used to hide another user's object |
| 404 | not found | also the answer for another user's object, so its existence is not revealed |
| 409 | a valid request that conflicts with the current state | not a field error; `attr` is `null` |
| 412 | an `If-Match` version no longer matches the stored record ([ADR 0016](0016-safe-writes.md)) | declared per operation, like `409`; nothing changed |
| 422 | an `Idempotency-Key` reused for a different request ([ADR 0016](0016-safe-writes.md)) | declared per operation, like `409` |
| 429 | rate limit, sign-in lock or a cooldown | always sends `Retry-After` with the remaining whole seconds (RFC 9110) |
| 503 | a safety control cannot run | sent with `Retry-After`, only by the calls that depend on a limit; those fourteen operations declare it (`config.api_errors.SERVICE_UNAVAILABLE_RESPONSE`), with `type` `server_error` |

Rules:

- **The client acts on `code`, not on the status alone.** It refreshes the access token only on a `401` with the code
  `token_expired`; every other `401` code is final for that request. This keeps a failed sign-in from looping.
- **Codes are a contract.** They are stable, snake_case and specific. Renaming or removing one is a breaking change
  (`docs/api-workflow.md`). The registry below grows with each endpoint and is mirrored in `docs/api-conventions.md`.
- **Sign-in names the field at fault** ([ADR 0012](0012-sign-in-names-the-field-at-fault.md)): an unknown email answers
  `email_not_registered` and a wrong password `wrong_password`; `invalid_credentials` stays for any other failure.
  Requesting a password reset and registering still answer the same way whether the address exists or not.
- Server errors keep the generic body without exception text and are not documented per operation.

Initial registry:

| Code | Status | Meaning |
| --- | --- | --- |
| `invalid_credentials` | 401 | any other failed sign-in (a deactivated account), not an unknown email or a wrong password |
| `email_not_registered` | 401 | no account has the email at sign-in (`attr`: `email`), whatever the password is |
| `wrong_password` | 401 | the email has an account and the password is wrong (`attr`: `password`) |
| `token_expired` | 401 | the access token has expired; refresh once and retry |
| `token_invalid` | 401 | the token is malformed or its signature is wrong |
| `session_signed_out` | 401 | the session was ended by sign-out |
| `session_revoked_by_user` | 401 | the user ended it from another device |
| `session_password_changed` | 401 | ended by a password change or reset |
| `session_email_changed` | 401 | ended by an email change |
| `session_token_reuse` | 401 | a used refresh token was replayed, so the session was ended |
| `session_expired` | 401 | the session reached its 90 day limit |
| `email_not_verified` | 403 | the credentials are right but the email is not verified |
| `captcha_required` | 400 | sign-in from an address that keeps failing needs a solved bot challenge (`attr`: `captcha_token`), [ADR 0013](0013-sign-in-bot-challenge.md) |
| `captcha_invalid` | 400 | the bot challenge token is wrong, expired or already used (`attr`: `captcha_token`) |
| `sign_in_locked` | 429 | too many failed sign-ins for the account or from the client address; `Retry-After` gives the remaining time, even for the right password |
| `throttled` | 429 | too many requests from one client address; `Retry-After` |
| `code_invalid` | 400 | the 6-digit code is wrong; the error item also carries `attempts_left` (see below) |
| `code_expired` | 400 | the 6-digit code has expired |
| `code_attempts_exceeded` | 429 | too many wrong codes, so the code is dead; `Retry-After` is the wait before a new one may be requested |
| `resend_too_soon` | 429 | the 60 second cooldown has not passed; `Retry-After` |
| `resend_limit_reached` | 429 | the limit of 5 codes per hour is used up; `Retry-After` is up to an hour |
| `email_already_registered` | 409 | when changing the email, to a signed-in caller who has proved the password (registration does not reveal it) |
| `email_unchanged` | 400 | the new email is the one the account already has (`attr`: `email`) |
| `invalid_password` | 400 | the current password is wrong: at a password change (`attr`: `current_password`) and at an account deletion (`attr`: `password`); a `400`, not a `401`, so the app's session handling is not triggered |
| `password_too_short` | 400 | a new password under 15 characters (`attr`: `password`) |
| `password_too_long` | 400 | a new password over 128 characters |
| `password_too_similar` | 400 | a new password that resembles the user's name or email |
| `password_too_common` | 400 | a new password on the common-password list |
| `password_compromised` | 400 | a new password that appears in a known data breach |
| `password_unchanged` | 400 | a new password equal to the current one, at a password change (`attr`: `password`); not applied to a reset ([ADR 0014](0014-password-reset-change-and-account-deletion.md)) |
| `password_attempts_exceeded` | 429 | 5 wrong current passwords at a password change or an account deletion lock both for 15 minutes; `Retry-After` is the remaining time |
| `reset_token_invalid` | 400 | the password reset token is unknown, used, expired or its account is deactivated (`attr`: `reset_token`); ask for a new code |
| `service_unavailable` | 503 | a safety control (the limit store) is down, so the request is refused; `Retry-After` |
| `invalid_amount` | 400 | not a valid amount for the currency (ADR 0008) |
| `currency_mismatch` | 400 | a fixed item carries a currency other than its month's, or a repayment one other than its debt's (`attr`: `currency`) |
| `exceeds_remaining` | 400 | a repayment is larger than the remaining debt (`attr`: `amount`) |
| `account_has_transactions` | 409 | deleting an account that still has transactions |
| `category_has_transactions` | 409 | reserved, no longer sent: deleting a category moves its transactions to Other ([ADR 0017](0017-deleting-a-category-moves-its-transactions.md)) |
| `category_is_builtin` | 403 | changing or deleting a built-in category |
| `device_sign_in_failed` | 401 | the one answer to any failure of sign-in with a device key, [ADR 0018](0018-sign-in-with-a-device-key.md) |
| `challenge_invalid` | 400 | a device-key challenge is unknown, used or expired, or the proof of possession is wrong (`attr`: `challenge_id`) |
| `public_key_invalid` | 400 | the device public key is not a valid P-256 key (`attr`: `public_key`) |
| `attestation_invalid` | 400 | the Android key attestation failed a check; no reason is given (`attr`: `attestation_chain`) |
| `platform_not_supported` | 400 | the feature is not available on this platform, such as a device key from a `web` session |
| `device_key_limit_reached` | 409 | the account already has 5 device keys |
| `idempotency_key_required` | 400 | a create without the `Idempotency-Key` header (`attr`: `idempotency_key`), [ADR 0016](0016-safe-writes.md) |
| `idempotency_key_invalid` | 400 | the key is not a quoted string of 16 to 64 letters, digits, hyphens or underscores (`attr`: `idempotency_key`) |
| `idempotency_key_reused` | 422 | the key was already used for a different request |
| `idempotency_in_progress` | 409 | the first request with this key is still running; `Retry-After` |
| `precondition_failed` | 412 | the `If-Match` version is not the stored one, or `If-None-Match: *` found the record; reload it |
| `fixed_item_limit_reached` | 409 | a salary month already has 100 fixed items |
| `month_has_fixed_items` | 409 | changing the currency of a salary month that still has fixed items ([ADR 0007](0007-salary-calculation-on-the-server.md)) |
| `invalid_characters` | 400 | a text value has a control character, a line separator, an unpaired surrogate or a bidirectional override, embedding or isolate character; `attr` is the field ([ADR 0021](0021-api-hardening-baseline.md)) |
| `request_too_large` | 400 | the request body is over the size limit; `attr` is `null` ([ADR 0021](0021-api-hardening-baseline.md)) |
| `name_taken` | 400 | the name is already used, case-insensitively after trimming, by another account or another custom category of the same user, or by a built-in category key or name; `attr` is `name` |
| `account_limit_reached` | 409 | the user already has 20 accounts |
| `category_limit_reached` | 409 | the user already has 50 custom categories; the built-in ones do not count |
| `does_not_exist` | 400 | an id in a body field (`category_id`, `account_id`) is unknown or belongs to another user, with the same answer for both; `attr` is the field. An id in the URL is `404` |
| `category_not_allowed` | 400 | the category does not allow the kind of the transaction; `attr` is `category_id` |
| `transaction_limit_reached` | 409 | the user already has 100 000 transactions |
| `date_out_of_range` | 400 | a calendar day outside 2000-01-01 to 2099-12-31; `attr` is the field |
| `invalid_date_range` | 400 | `date_from` is after `date_to` in a list or day totals filter; `attr` is `date_to` |
| `too_many_values` | 400 | more than 20 ids in a repeatable filter (`category_id`, `account_id`); `attr` is the parameter |

**Decided, not yet implemented.** Agreed with the Flutter developer on 2026-10-25. Each moves into the table above, with its
`ErrorCode` member, in the pull request that implements it; the issue of each endpoint can still refine the details.

- `amount_below_repaid` (400): lowering a debt below what was repaid; `attr` is `amount`.
- `due_before_start` (400): a due date before the start date; `attr` is `due_date`.
- `repayment_before_start` (400): a repayment dated before the debt starts; `attr` is `date`.
- `start_after_repayment` (400): moving a debt's start date past an existing repayment; `attr` is `start_date`.
- `debt_limit_reached` and `repayment_limit_reached` (409): the user has 500 debts, or the debt has 200 repayments.
- `session_limit_reached` (401): the session was ended because the account reached its limit of 10 active sessions.
- A new optional member `remaining` (an amount string in the debt's currency) on `exceeds_remaining`, so the app can show
  what is left.

**Extra members.** An error item is `{code, detail, attr}`. Three optional integer members exist, each sent with one code
only and documented in the shared error schema: `attempts_left` on `code_invalid` (the wrong tries the 6-digit code still
allows), `min_length` on `password_too_short` and `max_length` on `password_too_long` (the limits the server enforces,
read from the settings). With them a client words "at least {n} characters" from the server's own value, so a change of
password policy needs no app release, and it enforces no rule of its own. A client that meets a `password_*` code it does
not know shows the English `detail`. A new optional member is not a breaking change.

**The schema.** `code` is a plain string, not an enum, so a client survives a code it does not know yet. The registry
is written into the description of the `code` field, generated from `config.error_codes.ErrorCode`, so the contract and
the code cannot drift. Every documented error status shares the same two error schemas (`config/api_errors.py`). The
operations that need no token declare their own `401` and `403`, because the package documents those only for
operations that declare authentication.

## Consequences

- An operation that can conflict documents its `409`; the client maps each documented `code` to its own message and
  localization.
- A salary month is saved with `PUT` on its year-month key, which creates or replaces it, so saving never answers `409`.
- The status list is a setting, so adding a status later is a one-line change plus its enum override.
- Implemented in `feat/api-foundation` (setting, exception classes, `Retry-After`), then used by the endpoints.
