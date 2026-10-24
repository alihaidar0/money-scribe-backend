# Architecture decision records

One short file per decision that is costly to change later. Each states the context, the decision and its consequences.
A decision that changes gets a new record that supersedes the old one; the old file stays and its status says so.

| ADR | Decision | Status |
| --- | --- | --- |
| [0001](0001-user-model.md) | The user model: UUID key, email sign-in, one display name | accepted |
| [0002](0002-draft-releases-and-environments.md) | Draft releases for `develop` (staging) and `main` (production) | accepted |
| [0003](0003-cursor-pagination.md) | One keyset cursor class for every list, with filters and server-side day totals | accepted |
| [0004](0004-error-status-codes.md) | Documented error statuses and the initial error-code registry | accepted |
| [0005](0005-apps-and-model-conventions.md) | Django app split and model conventions | accepted |
| [0006](0006-authentication-tokens.md) | Access and refresh tokens: replay window, web cookie, ending sessions | accepted |
| [0007](0007-salary-calculation-on-the-server.md) | The server owns the salary calculation, its months and fixed items | accepted |
| [0008](0008-money-on-the-wire.md) | Money on the wire: decimal string and currency | accepted |
| [0009](0009-sessions-and-devices.md) | Session and device records, retention and privacy | accepted |
| [0010](0010-admin-panel-and-email-outbox.md) | The admin panel and a database email outbox for development and staging | accepted |
| [0011](0011-staging-hosting.md) | Staging on free tiers: Neon, Render and the GitHub Container Registry; what is deferred | accepted |
| [0012](0012-sign-in-names-the-field-at-fault.md) | Sign-in names the field at fault: unknown email or wrong password, with an address limit | accepted |
| [0013](0013-sign-in-bot-challenge.md) | A Cloudflare Turnstile challenge for sign-in after repeated failures | accepted |
| [0014](0014-password-reset-change-and-account-deletion.md) | Password reset with a single-use token, password change, account deletion and the lock on the current password | accepted |
| [0015](0015-profile-name-and-email-change.md) | Editing the name, and changing the sign-in email with a code to the new address | accepted |
| [0016](0016-safe-writes.md) | Safe writes: an `Idempotency-Key` on creates, a `version` with `ETag` and `If-Match` on edits | accepted |
| [0017](0017-deleting-a-category-moves-its-transactions.md) | Deleting a category moves its transactions to Other; an account with transactions still refuses | accepted |
| [0018](0018-sign-in-with-a-device-key.md) | Sign-in with a device key pair and a signed challenge (fingerprint), moving to passkeys when a domain exists | accepted |

The fields and limits of each resource are in [`../api-fields.md`](../api-fields.md).

Still open: push notification wiring, production hosting, and the shared domain for the web app and the API. All three
belong to later steps (`docs/steps.md`). The setup and operation of staging are in [`../staging.md`](../staging.md).
