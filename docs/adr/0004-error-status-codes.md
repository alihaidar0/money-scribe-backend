# ADR 0004: Documented error statuses and error codes

- Status: accepted
- Date: 2026-10-15

## Context

Every error already has one body, `{type, errors[]}` (see `docs/api-conventions.md`). What had to be agreed is which HTTP
statuses the contract documents and which stable `code` values the app can tell apart: the client shows a different
message, or takes a different action, for each of them.

## Decision

The contract documents **400, 401, 403, 404, 409 and 429** for each operation that can return them. The package setting
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
| 429 | rate limit, sign-in lock or a cooldown | always sends `Retry-After` with the remaining whole seconds (RFC 9110) |

Rules:

- **The client acts on `code`, not on the status alone.** It refreshes the access token only on a `401` with the code
  `token_expired`; every other `401` code is final for that request. This keeps a failed sign-in from looping.
- **Codes are a contract.** They are stable, snake_case and specific. Renaming or removing one is a breaking change
  (`docs/api-workflow.md`). The registry below grows with each endpoint and is mirrored in `docs/api-conventions.md`.
- **Sign-in never says which part was wrong:** an unknown email and a wrong password both answer `invalid_credentials`.
  Requesting a password reset always answers the same way, whether the address exists or not.
- Server errors keep the generic body without exception text and are not documented per operation.

Initial registry:

| Code | Status | Meaning |
| --- | --- | --- |
| `invalid_credentials` | 401 | wrong email or password at sign-in |
| `token_expired` | 401 | the access token has expired; refresh once and retry |
| `token_invalid` | 401 | the token is malformed or its signature is wrong |
| `session_signed_out` | 401 | the session was ended by sign-out |
| `session_revoked_by_user` | 401 | the user ended it from another device |
| `session_password_changed` | 401 | ended by a password change or reset |
| `session_email_changed` | 401 | ended by an email change |
| `session_token_reuse` | 401 | a used refresh token was replayed, so the session was ended |
| `session_expired` | 401 | the session reached its 90 day limit |
| `email_not_verified` | 403 | the credentials are right but the email is not verified |
| `sign_in_locked` | 429 | too many failed sign-ins; `Retry-After` gives the remaining time |
| `code_invalid` | 400 | the 4-digit code is wrong |
| `code_expired` | 400 | the 4-digit code has expired |
| `code_attempts_exceeded` | 429 | too many wrong codes; `Retry-After` |
| `resend_too_soon` | 429 | the code cooldown has not passed; `Retry-After` |
| `email_already_registered` | 409 | at registration or when changing the email |
| `invalid_password` | 400 | the current password is wrong (`attr`: `current_password`) |
| `invalid_amount` | 400 | not a valid amount for the currency (ADR 0008) |
| `currency_mismatch` | 400 | a fixed item carries a currency other than its month's |
| `exceeds_remaining` | 400 | a repayment is larger than the remaining debt (`attr`: `amount`) |
| `account_has_transactions` | 409 | deleting an account that still has transactions |
| `category_has_transactions` | 409 | deleting a category that still has transactions |
| `category_is_builtin` | 403 | changing or deleting a built-in category |

## Consequences

- An operation that can conflict documents its `409`; the client maps each documented `code` to its own message and
  localization.
- A salary month is saved with `PUT` on its year-month key, which creates or replaces it, so saving never answers `409`.
- The status list is a setting, so adding a status later is a one-line change plus its enum override.
- Implemented in `feat/api-foundation` (setting, exception classes, `Retry-After`), then used by the endpoints.
