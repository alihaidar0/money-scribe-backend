# ADR 0016: Safe writes: idempotent creates and version checks

- Status: accepted
- Date: 2026-10-23

## Context

The app creates money records (transactions, repayments, debts, fixed items) over a mobile network. A request can be
sent, applied by the server and have its answer lost; the app then repeats it, and without protection the amount is
recorded twice. The same person can also edit one record from two devices, and the later save silently overwrites the
earlier one. Both problems are about the stage 3 resources, so the rules are fixed before the first of them is built.

This record follows RFC 9110 for conditional requests (`ETag`, `If-Match`, `412`) and the IETF HTTP API working group's
`Idempotency-Key` Internet-Draft (version 08 on 2026-10-22, not yet an RFC, so the details below are this project's own
published specification, as the draft asks).

## Decision

### Creating: `Idempotency-Key`

- **Where.** Every `POST` that creates a record in a stage 3 collection (accounts, categories, transactions, fixed items,
  debts, repayments) and every bulk import requires the header. `PUT`, `PATCH` and `DELETE` are idempotent by nature and do
  not take it. The authentication calls do not take it either: they have their own protections (ADR 0006, 0012, 0014).
- **Format.** The value is a quoted string (an RFC 9651 structured field string) of 16 to 64 characters from letters, digits,
  hyphen and underscore. The app sends a random UUID, once per user action, and reuses it only to repeat that same action:
  `Idempotency-Key: "8e03978e-40d5-43e8-bc93-6894a57f9324"`. A key is scoped to the signed-in user, so no one can collide
  with, or guess, another user's key.
- **Same key, same request.** The server answers with the status and body of the first success and adds
  `Idempotent-Replayed: true`. Nothing is created twice. The key is stored in the same database transaction as the
  record, so a request that failed or rolled back leaves no key behind, and repeating it runs it again.
- **Same key, different request.** A request is the same when its method, path and body are. Reusing a key with another
  one answers `422 idempotency_key_reused`.
- **Same key while the first is running.** The second request waits for the first for a short time and then answers its
  result; if the first is still running, `409 idempotency_in_progress` with `Retry-After`.
- **Missing or malformed.** `400 idempotency_key_required` or `400 idempotency_key_invalid` (`attr` `idempotency_key`).
- **Retention.** A key is honoured for 24 hours. An expired key is ignored at lookup, so correctness does not depend on the
  purge job (a daily purge joins the Celery Beat jobs when a worker runs). Stored answers are removed with the account.
- **Only a success is stored.** An error is not replayed: the app corrects the request and sends it again.

### Editing: `version`, `ETag` and `If-Match`

- Every record the user can edit has a read-only integer `version`, `1` on creation and increased by one with every
  change. The same value is the `ETag` of a single-record answer, as a strong tag: `ETag: "3"`. Lists carry `version`
  in each record, because the app edits from a list.
- `PUT`, `PATCH` and `DELETE` honour `If-Match: "3"`. If the stored version differs, nothing changes and the answer is
  `412 precondition_failed`; the app reloads the record and lets the person decide. The check and the update happen in
  one transaction under a row lock (`SELECT ... FOR UPDATE`), so two saves at once cannot both win: the second waits,
  then sees the new version. The version itself is raised in the `UPDATE` (`version = version + 1`), so even a save
  without the check counts.
- `If-Match` is optional: without it the last write wins. The app sends it on every edit and delete; the server does not
  force it (`428`) because a person's own two devices rarely collide, and a missing header must not block them. A
  `PUT` of a salary month that does not exist yet with an `If-Match` is a `412`.
- A `PUT` that creates a record under a key the client chose (a salary month) also honours `If-None-Match: *`
  (RFC 9110): if the record already exists, nothing changes and the answer is `412 precondition_failed`. The app sends
  it when its form is for a month it has not loaded, so a month created meanwhile on another device is not replaced
  silently. Without the header, a `PUT` on an existing month replaces it.
- The `ETag` identifies the stored row only. Derived values such as a debt's `status` change with the day, so no
  conditional `GET` (`If-None-Match`) is offered.
- Reordering (accounts, categories) is one operation on the whole list and does not take `If-Match`; a list that no
  longer matches the user's rows is refused with `409`, and the code is registered with the endpoint.

## Consequences

- ADR 0004 gains `412` and `422` as documented statuses, declared per operation like `409`, and the codes
  `idempotency_key_required`, `idempotency_key_invalid`, `idempotency_key_reused`, `idempotency_in_progress` and
  `precondition_failed`.
- ADR 0005 gains the `version` column in its model conventions and the `idempotency` app, which holds the stored
  answers (an installed app is needed for a table, and `common` is not one). A resource gets the rules by inheriting:
  the abstract `common.models.VersionedModel` for `version`, `config.api.IdempotentCreateMixin` on the view that
  creates, and `config.api.VersionedMixin` on the view that reads, edits and deletes one record. Each endpoint's test
  covers a repeated create and a stale edit, as `idempotency/tests` does for a throwaway resource.
- A second copy of a create that meets the first still running waits for it on the database's unique index (at most
  `IDEMPOTENCY_LOCK_WAIT_SECONDS`, 3), so the usual outcome of two copies is one create and one replay;
  `409 idempotency_in_progress` is the rare case where the first outlasts the wait.
- The CORS configuration exposes `ETag`, `Idempotent-Replayed` and `Retry-After`, and allows `Idempotency-Key` and
  `If-Match` as request headers, for the web build.
- Stored answers hold financial data, so the table is in the account's cascade and is never logged.
- Bulk import takes the header too, and its result per row (`docs/api-fields.md`, section CSV) is stored with it.
- Rejected: a `version` field in the request body (a header is the standard place and a body field is easy to forget),
  required `If-Match` (blocks a legitimate request on a missing header), and last write wins everywhere (silently loses an
  edit of a salary month, which is replaced as a whole).
