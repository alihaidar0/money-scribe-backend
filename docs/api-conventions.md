# API conventions (defaults)

Short defaults for a project's HTTP API. They keep the contract (`openapi/schema.yaml`)
consistent from the first endpoint; a project may change any of them, but should decide once and
apply it everywhere. Rules that depend on the project (money and currency formats, identifiers,
authentication, domain naming) are **not** here: each project adds its own to this file.

This project's decisions are recorded in [`docs/adr/`](adr/README.md); the sections below say which one applies.

## Versioning

Version in the path: `/api/v1/...`. A breaking change gets a new version (`/api/v2/...`) next to
the old one (see `docs/api-workflow.md`). The `SCHEMA_PATH_PREFIX` setting already keeps the
version out of operation ids and tags.

## One error body

Every error raised inside a DRF view has the same body, so a client has a single error parser. The
template ships it with [drf-standardized-errors](https://github.com/ghazi-git/drf-standardized-errors)
(`EXCEPTION_HANDLER` and the schema class are already set by `scripts/init-django.sh`):

```json
{
  "type": "validation_error",
  "errors": [
    { "code": "required", "detail": "This field is required.", "attr": "price" }
  ]
}
```

- `type` is `validation_error` (invalid input), `client_error` (any other 4xx, including an unreadable body)
  or `server_error`.
- `errors` is a list. `code` is stable and meant for programs (`required`, `blank`, `not_found`,
  `permission_denied`, `throttled`, …); `detail` is human text; `attr` is the field the error belongs to
  (dotted for nested data) or `null` when it is about the request as a whole.
- This is **not** RFC 9457 problem details. The format is the package's own; what the contract
  gives a client is that it is documented and typed.

What the contract documents: the 400, 401, 403, 404, 409 and 429 responses of each operation that can return
them ([ADR 0004](adr/0004-error-status-codes.md)). The 400 body is one shared schema (`Error400Response`, from
`config/api_errors.py`), because the package's default, one schema per operation and field, grows a generated client by
dozens of classes per endpoint. Add a status code to `DRF_STANDARDIZED_ERRORS["ALLOWED_ERROR_STATUS_CODES"]` (and its
enum to `ENUM_NAME_OVERRIDES`) when a project wants it documented too; `409` is added with `feat/api-foundation`.

- **A client acts on the `code`.** It refreshes the access token only on a `401` with the code `token_expired`; every other
  `401` code is final for that request, so a failed sign-in cannot loop.
- `401` sends `WWW-Authenticate: Bearer`. Its codes tell the app what happened: `invalid_credentials`, `token_expired`,
  `token_invalid`, and one `session_*` code for each reason a session ended (see [Authentication](#authentication)).
- `404` is also the answer for another user's object, so its existence is not revealed.
- `403` is for a request that is not allowed, such as changing a built-in category (`category_is_builtin`).
- `409` is a valid request that conflicts with the current state (for example deleting an account that has
  transactions). It is not a field error: `attr` is `null` and `code` is stable and specific. A rule on one field, such
  as a repayment above the remaining debt, is a `400` on that field.
- `429` always sends `Retry-After` with the remaining time in whole seconds, for throttling, the sign-in lock and the
  code resend cooldown.
- Error `code` values are a contract. Renaming or removing one is a breaking change. The initial list is in
  [ADR 0004](adr/0004-error-status-codes.md) and grows here with each endpoint.

Limits to know:

- Only errors raised inside DRF views are covered. Django's own responses (a disallowed host, a URL outside
  the API, a middleware rejection) keep their normal HTML or plain-text body; a client treats a non-JSON
  error as a generic failure.
- An unhandled exception returns a generic message, never the exception text.
- Errors raised straight from a view (`raise ValidationError(...)` with a custom code) keep that code at runtime;
  the shared 400 schema describes `code` as a string, so nothing needs registering.

## Pagination

Every list endpoint is paginated by one class, `ApiCursorPagination` (`DEFAULT_PAGINATION_CLASS`), a keyset cursor
([ADR 0003](adr/0003-cursor-pagination.md)):

- Query parameters: `limit` (default 50, maximum 100) and `cursor`, an opaque value taken from `next` or `previous`.
- Envelope: `next` and `previous` (full URLs or `null`) and `results`. There is no total count.
- A client follows `next`; it never builds a cursor and cannot jump to a page number.
- Each list has one deterministic ordering that ends in the unique `id`, so a row is never repeated or skipped by paging.
  Transactions are ordered `-date`, `-created_at`, `-id`, so within a day the entry added last comes first.
- Filters are explicit query parameters documented in the schema; transactions take `date_from`, `date_to`, `account`,
  `category` and `kind`.
- A total that spans pages, such as the total per day, comes from a report endpoint
  (`GET /api/v1/reports/day-totals/`), never from adding up pages.
- Read models (overview, reports), settings and single objects are not lists and are not paginated. A salary month
  returns its fixed items inline.

## Timestamps

UTC, ISO 8601, with an explicit offset: `2026-01-31T14:05:09Z`. Keep `USE_TZ = True`; a client
converts to the user's time zone for display only. Dates without a time are `YYYY-MM-DD`, and a transaction date, a
debt's start and due dates and a repayment date are such calendar days, never instants.

## Identifiers

Ids are UUIDs assigned by the server and sent as lowercase hyphenated strings. A user id is a random UUIDv4; other
resources use time-ordered UUIDv7 ([ADR 0001](adr/0001-user-model.md), [ADR 0005](adr/0005-apps-and-model-conventions.md)).
A client treats every id as an opaque string.

## Authentication

Two JSON Web Tokens ([ADR 0006](adr/0006-authentication-tokens.md)): a short-lived **access token** and a long-lived **refresh
token** that rotates.

- Every call except sign-in, registration and the other public operations carries `Authorization: Bearer <access token>`.
  The access token lives 10 minutes, and a request whose session has ended is refused at once.
- `POST /api/v1/auth/refresh` returns a new access token and a **new refresh token**. A refresh token works once. If the
  answer is lost, retrying the same refresh token within 30 seconds of the server's rotation returns the same new token; a
  used token presented later ends the session (`session_token_reuse`).
- On `401` with the code `token_expired`, refresh once and retry; every other `401` code is final. If the refresh fails,
  sign the user out and show the reason from the `session_*` code. Send one refresh at a time inside a process.
- **Mobile:** the refresh token is in the JSON body and is kept in the platform's secure storage; the access token stays in
  memory.
- **Web:** the refresh token is an `HttpOnly`, `Secure`, `SameSite=Strict` cookie that never appears in a body. Send
  `X-Client: web` and credentials on the auth calls. A refresh token that arrives in the cookie is answered with a cookie.
  The app and the API need one registrable domain.
- Sign-in, the email-code verification that signs a new account in, and every refresh send a `device` object
  ([ADR 0009](adr/0009-sessions-and-devices.md)); registration and password reset send none.
- Sign-in answers `429` with `Retry-After` while the account is locked.
- Which events end which sessions is a table in [ADR 0006](adr/0006-authentication-tokens.md).

## Money

An amount is a decimal string next to a `currency` code ([ADR 0008](adr/0008-money-on-the-wire.md)):
`"amount": "12.50", "currency": "USD"`. It has exactly the currency's number of fraction digits (2, JPY 0, KWD 3), `.` as
the separator, no grouping, no exponent and no leading `+`, and at most 15 digits in all. Stored amounts are positive and
the kind or direction carries the sign; the schema type `Amount` is unsigned and `SignedAmount` allows a leading `-` for
computed values such as balances and day totals. Too many digits answer `400 invalid_amount`.

## Salary

The server computes salary months from the inputs the client sends and returns the results; the client never sends
computed values ([ADR 0007](adr/0007-salary-calculation-on-the-server.md)). A month is addressed by `YYYY-MM`, and saving
it with `PUT` creates or replaces it. The fields and their limits for every resource are in
[`api-fields.md`](api-fields.md).

## Also

- Field and query-parameter names are `snake_case`.
- Use HTTP status codes for the outcome (`201` created, `204` no content, `400` invalid, `401`/`403`
  not authenticated/allowed, `404` not found, `409` conflict) and keep them in the schema.
- An empty list is `200` with an empty `results`, never `404`.
