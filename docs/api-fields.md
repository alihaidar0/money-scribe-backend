# API fields and limits

The fields the user can enter or change, with the limits the server validates. They were agreed with the client's
developer on 2026-10-15 and are the source for serializers and tests until `openapi/schema.yaml` exists; once an
endpoint is in the schema, the schema is authoritative and this page keeps the reasons.

Common rules:

- Amounts follow [ADR 0008](adr/0008-money-on-the-wire.md): a decimal string next to a `currency` code, positive unless a
  field is a `SignedAmount`.
- A calendar day is `YYYY-MM-DD` with no time zone and no instant. This holds for a transaction `date`, a debt's start and
  due dates and a repayment's `date`; pay periods split at day 15, and a time zone shift would move an entry into the wrong
  half.
- Ids are UUID strings ([ADR 0005](adr/0005-apps-and-model-conventions.md)). Text follows the
  [text rules](api-conventions.md#text): trimmed, NFC, and `400 invalid_characters` for a character that has no place in a name
  or a note.
- A rule that fails answers `400` with the codes of [ADR 0004](adr/0004-error-status-codes.md).
- Every record has `id`, `created_at`, `updated_at` and, when it can be edited, a read-only `version`
  ([ADR 0016](adr/0016-safe-writes.md)). Every `POST` that creates a record needs an `Idempotency-Key` header, and an
  edit or delete should carry `If-Match` with the record's `version`.
- Every list is paginated by the one cursor class ([ADR 0003](adr/0003-cursor-pagination.md)), including accounts and
  categories, which are short: the app follows `next`.

## Salary month

Addressed by `YYYY-MM`; one per user and month, with the currency as a field of the month; `PUT` creates or replaces it,
`GET` reads it, `DELETE` removes it.

| Field | Rule |
| --- | --- |
| year and month | the key of the month |
| `currency` | one currency for the whole month; it cannot change while the month has fixed items (`409 month_has_fixed_items`), because their amounts are stored in the currency's minor units |
| `base_salary` | amount, greater than 0 |
| `food_per_day` | amount, 0 or more |
| `working_days_in_month` | whole number, 1 to 31 |
| `working_hours_in_month` | whole number, 1 to 744 (a month has at most 744 hours) |
| `extra_hours_hundredths` | whole number, 0 to 74400 (744 hours; the user types hours with up to 2 decimals) |
| `tax_rate_basis_points` | whole number, 0 to 10000 (the user types a percent with up to 2 decimals; the app defaults to 13%) |
| `payment_mode` | `one_payment` or `two_payments`, kept with the saved month |
| `working_days_first_half` | whole number, 0 up to `working_days_in_month`; used only with two payments |
| `first_pay_day`, `second_pay_day` | day of month, 1 to 28, kept with the saved month |

A saved month can be corrected later, including switching between one and two payments. Its fixed items keep their `part`
(ADR 0007). The results, the fixed-item totals, the balance after fixed items per part, and, with two payments, the
balance of the whole month are computed by the server; the app never sends them, and it computes the same figures
only for its live preview. The two sides are kept in step by a shared file of invented test cases (ADR 0007).

The currencies are RUB, USD, EUR, GBP, TRY, SAR, AED and EGP (2 digits), JPY (0) and KWD (3), the same list as the app.
`PUT` answers `201` when it creates the month and `200` when it replaces it; it accepts `If-Match` (a loaded month) or
`If-None-Match: *` (a new one), both optional ([ADR 0016](adr/0016-safe-writes.md)). The month key stays within years
2000 to 2099. With one payment the single entry of `payments` has the `part` `first`, and `working_days_first_half` is
`null` or left out.

Operations: the history list (newest first, each month with its results), read, `PUT` (create or replace), `DELETE`
(removes the month and its fixed items), the **defaults** read, and the **copy** of fixed items.

- **Defaults** (`GET .../defaults/`) give the inputs to pre-fill the form of a month. Carried from the most recent saved
  month: `working_hours_in_month` and `tax_rate_basis_points`, and the amounts `base_salary` and `food_per_day` under the
  currency rule below. Never carried: `working_days_in_month` and `working_days_first_half` (`null`) and
  `extra_hours_hundredths` (`0`). The pay schedule (`payment_mode`, `first_pay_day`, `second_pay_day`) comes from the
  Settings when the person has set the value there, then from the most recent saved month, then from the app's own
  defaults (one payment, days 20 and 5). A Settings value that was never set does not count, so an untouched Settings
  page does not hide the schedule of the last month.
  - **Currency.** The Settings default currency when the person has set one, else the currency of the most recent saved
    month, else `RUB`. The carried amounts are returned only when that currency equals the most recent saved month's;
    otherwise they are `null`, because a RUB amount is not a USD amount. The other carried fields still carry.
  - The Settings (`GET` and `PATCH /api/v1/settings/`) are live: what the person set there is what the first source reads.
- **Copy** (`POST /api/v1/salary/months/{month}/fixed-items/copy/`, `Idempotency-Key` required) copies the fixed items of
  another month into this one and answers `200` with the target month. The optional body field `from` is a `YYYY-MM`
  (default: the most recent saved month before this one). Both months must be saved (`404` otherwise, also when there is
  no earlier month), `from` cannot be the month itself (`400` on `from`) and must have the same currency
  (`400 currency_mismatch` on `from`). The items are added to the ones already there, with new ids, version 1 and the
  copied `name`, `amount`, `kind`, `part` and `day`. If the result would pass 100 items, nothing is copied
  (`409 fixed_item_limit_reached`). A source with no items copies nothing and is not an error.

## Fixed item

Belongs to a month and is returned inline with it, so there is no separate list. Create, edit and delete.

| Field | Rule |
| --- | --- |
| `name` | text, trimmed, then 1 to 50 characters |
| `amount` | amount, greater than 0 |
| `kind` | `income` or `expense` |
| `part` | `first` or `second`, ignored for a month with one payment (then every item counts); defaults to `first` |
| `day` | optional whole number 1 to 31, or `null` for none (the default, and what an item saved before the field existed has); the day of the month the item falls on |

The `day` is stored and returned as sent: a day past the end of the month (31 in September) counts as the last day of that
month, and the reader applies that, so the item keeps its day when it is copied to a longer month. The server does not
derive `part` from `day` and never checks one against the other; the app sends both (days 1 to 15 are the first part, day 16
to the end the second). A `PATCH` that leaves `day` out keeps it, and `"day": null` clears it.

A fixed item has no currency of its own: it uses the month's. A request may leave `currency` out; one that carries
another currency answers `400 currency_mismatch`. Adding an item to a month that is not saved answers `404`. A month holds at most 100 items; the 101st answers `409 fixed_item_limit_reached`. A month can
be saved with no items, and deleting a month deletes its items. Each item has its own `version`, so two devices editing
different items do not conflict; an item change does not change the month's `version` or its results, only the
fixed-item totals and the balances.

## Transaction

| Field | Rule |
| --- | --- |
| `kind` | `expense` or `income` |
| `amount` | amount, greater than 0 |
| `currency` | ISO code, required on create and fixed for good (`400 read_only` on a change, even with the stored value); the account has no currency, the transaction carries it |
| `date` | calendar day, 2000-01-01 to 2099-12-31 (`400 date_out_of_range`); no limit relative to today |
| `category_id` | id of a built-in category or of the person's own, required; it must allow the kind (`400 category_not_allowed`) |
| `account_id` | id of one of the person's accounts, required |
| `note` | text, optional, up to 100 characters, empty by default |

An unknown id and an id that belongs to someone else give the same answer, `400 does_not_exist` on that field. A person has
at most 100 000 transactions (`409 transaction_limit_reached`).

Create, edit and delete (a hard delete: no soft delete, no tombstone). The list is ordered `-date`, `-created_at`, `-id`, so
within a day the entry added last comes first, and takes the filters `date_from`, `date_to` (inclusive),
`category_id` and `account_id` (each repeatable, at most 20 values: `400 too_many_values`), `kind` and `search`
([ADR 0003](adr/0003-cursor-pagination.md)). `search` matches the note only, as a case-insensitive substring of 2 to 100
characters (`%` and `_` are ordinary characters); the category and account names are not searched, because the app shows
the built-in names in its own languages. `date_from` after `date_to` is `400 invalid_date_range`. The two pay periods of a
month are asked for with the date range (days 1 to 15 and day 16 to the end), so the list has no `part` filter; the reports
do (see Reports). Day totals take the same filters except `search`.

When a custom category is deleted its transactions move to `other` and each gets a new `version`, so a form that was open on
one gets `412` when saved ([ADR 0017](adr/0017-deleting-a-category-moves-its-transactions.md)).

## Account

| Field | Rule |
| --- | --- |
| `name` | text, 1 to 30 characters; unique per person, in any case (`400 name_taken`) |
| `kind` | `card`, `cash` or `other` |

Create, edit and delete; there is no reorder (the app has none). The list is ordered by creation (`created_at`, `id`).
Deleting an account that has transactions answers `409 account_has_transactions` and changes nothing. A person has at most
20 accounts (`409 account_limit_reached`).

**The first two accounts.** When an email is verified the server makes "Card" (`card`) and "Cash" (`cash`) in the
language of the account (Arabic: "بطاقة" and "نقدًا", the app's own labels of the two kinds). They are made in one
transaction with creation times a millisecond apart, so the card is always first, and they are ordinary accounts:
editable, deletable and counted towards the limit. A data migration gave them to the people who were already verified. The
users app announces the verification with a signal and never imports the ledger ([ADR 0005](adr/0005-apps-and-model-conventions.md)).

## Category

| Field | Rule |
| --- | --- |
| `name` | text, 1 to 30 characters; of a custom category, unique among the person's custom ones in any case and not a built-in key or English name (`400 name_taken`) |
| `icon` | a slug in the response, so the app falls back to `other` for one it does not know; on create and edit one of `gifts`, `health`, `entertainment`, `shopping`, `education`, `travel`, `subscriptions`, `other` (`400 invalid_choice`) |
| `kind` | `expense`, `income` or `both`; read-only: a custom category is always `both` |
| `is_custom` | read-only |
| `key` | read-only; a stable key on built-in categories, `null` on custom ones |

Create, edit and delete for custom categories only, at most 50 per person (`409 category_limit_reached`); there is no
reorder. Built-in categories are read-only: changing or deleting one answers `403 category_is_builtin`, before the
version is checked. Deleting a custom category that has transactions succeeds (`204`) and moves them to the built-in
`other` in the same database transaction ([ADR 0017](adr/0017-deleting-a-category-moves-its-transactions.md)). The list returns the built-in categories first, in a fixed order, then the custom ones in the order
they were made.

**Built-in categories** are shared rows created by a data migration with fixed ids, and returned with the person's own.
Each carries its `id`, a stable `key` that the app localizes (English and Arabic), an `icon` equal to the key, a `kind`
and an English `name` as a fallback for a key the app does not know yet:

| Key | Kind |
| --- | --- |
| `groceries`, `dining`, `transport`, `housing`, `utilities` | `expense` |
| `scholarship` | `income` |
| `other` | `both` |

`gifts` is not a built-in category: it is an icon a custom category can use.

## Debt and repayment

`/api/v1/debts/` (list, create), `/api/v1/debts/{id}/` (read, change, delete) and, under a debt,
`/api/v1/debts/{id}/repayments/` (create) and `/api/v1/debts/{id}/repayments/{repayment_id}/` (change, delete). A
repayment has no read and no list of its own: every debt carries its repayments inline, by `date`, then `id`.

| Field | Rule |
| --- | --- |
| `direction` | `lent` or `borrowed`; fixed for good (`400 read_only` on a change, even with the stored value) |
| `person` | text, 1 to 50 characters; the name of a third party, never written to a log |
| `amount` | amount, greater than 0; not below what was repaid (`400 amount_below_repaid`) |
| `currency` | ISO code, required on create and fixed for good, like the `direction` |
| `start_date` | calendar day, 2000-01-01 to 2099-12-31 (`400 date_out_of_range`); not after a repayment (`400 start_after_repayment`) |
| `due_date` | calendar day, optional, not before `start_date` (`400 due_before_start` on `due_date`); `null` clears it |
| `note` | text, up to 100 characters, default empty; `""` clears it and `null` is `400` |
| `repaid`, `remaining`, `status`, `repayments` | derived and read only (`400 read_only` if sent) |

A person has at most 500 debts (`409 debt_limit_reached`) and a debt has at most 200 repayments
(`409 repayment_limit_reached`).

A **repayment** has an `amount` greater than 0 and not more than what remains (`400 exceeds_remaining`, whose error item
also carries `remaining`) and a `date` that is not before the debt's `start_date` (`400 repayment_before_start`). It is in
the debt's currency and carries none: a `currency` in the request is `400 read_only`. When a repayment is edited, its own
previous amount does not count: the room for it is the debt minus the **other** repayments. A repayment may be dated in
the future.

The derived values come from the server, because the overview and the debts report use them too: `repaid` is the sum of the
repayments, `remaining` is the amount minus `repaid` and never below 0, and `status` is `settled` when nothing remains,
`overdue` when a `due_date` has passed and the debt is not settled, otherwise `open`. A settled debt that is raised above
what was repaid is open again. "Passed" is counted in the person's day, from the Settings `time_zone`, not in UTC. The reads
(the list and one debt) take an optional `today` (`YYYY-MM-DD`) that replaces the person's current day, for the `status` and
for the `status` filter; the app leaves it out, and the write calls use the person's own day. The list filters are
`direction`, `status` (repeatable) and `search` (a case-insensitive substring of the person, 2 to 50 characters, counted in
the search rate group), newest first.

**Every repayment call answers with the whole debt** (`201` for the create, `200` for the change and the delete), so the app
has the new `repaid`, `remaining` and `status` at once. The `ETag` of every answer whose body is the debt is the debt's
`version`, and a repayment write does not change it, so two devices adding different repayments never give each other a
`412`. A repayment is changed or deleted with `If-Match` carrying **its own** `version` (`repayments[].version`). Every
repayment write, and the change and the delete of a debt, takes a row lock on the debt and works out the remaining amount
inside it, so two devices cannot overpay a debt together.

## Settings

`GET /api/v1/settings/` and `PATCH /api/v1/settings/`, one row per user, made the first time it is read. The names are
the ones of the salary month (`payment_mode`, not `mode`).

| Field | Rule |
| --- | --- |
| `default_currency` | one of the supported currencies; `null` on a change goes back to "not set"; shows `RUB` until set |
| `payment_mode` | `one_payment` or `two_payments`; `null` goes back to "not set"; shows `one_payment` until set |
| `first_pay_day`, `second_pay_day` | day of month, 1 to 28, each on its own; `null` goes back to "not set"; show 20 and 5 until set |
| `remind_on_pay_days` | true or false (a JSON boolean); defaults to `false` |
| `time_zone` | an IANA name such as `Europe/Moscow`, validated; lives on the user; defaults to `UTC` |
| `language` | `en` or `ar`; lives on the user; defaults to `en` |
| `version` | read only; the `ETag` of the settings; a `PATCH` may send `If-Match` (`412` when stale, nothing changes) |

The answer always carries every field. A `PATCH` changes only the fields sent; a field that is not a setting is
`400 read_only`, and nothing sent changes nothing (not even the version). `language`, `time_zone` and
`remind_on_pay_days` cannot be `null`.

The defaults are the app's own, used when a field has never been set; a field never set is told apart from one set to
the same value, because the salary form's defaults use only what the person has set (docs above, Salary month).
Theme, the PIN app lock and the notification permission stay on the device and have no field.

- `time_zone` lets the server send a pay-day reminder at a sensible local time and work out the user's "today". The app
  reads the name from the browser on the web and from the operating system on Android and iOS, and leaves the field out
  when it cannot read it; the server then keeps the stored value, or `UTC`.
- `language` is used for the push text and for the emails (codes and the new-device notice), so they read correctly in
  English and Arabic. The app sends the resulting `en` or `ar`; a device language that is neither falls back to `en`.
- **Who sets them.** Registration, the email-code verification and every sign-in send `language` and `time_zone`, and the
  server stores them on the user ([ADR 0001](adr/0001-user-model.md)): the device the user signs in on decides, so a new
  device never leaves a stale value. A request that leaves one out keeps the stored value. Afterwards the app sends only changes, with a
  `PATCH` of the settings when the user changes the language or when the time zone differs from the stored one.

## Push notifications

The device sends its Firebase installation ID (FID) and the user turns the permission on or off on the device. A notification never
contains an amount or a name. The server builds the text in the user's `language`, so it also shows when the app is
closed, and adds a data key so the app can open the right screen. The FID belongs to the session and is removed when
the session ends ([ADR 0009](adr/0009-sessions-and-devices.md)). The endpoints come with the `notifications` feature.

**Why the FID and not the registration token** (decided 2026-10-24, with the Flutter developer). The Firebase Admin SDK
names the installation ID as the recommended target of a message and deprecates the registration token. The Python
`firebase-admin` package supports it from 7.5.0 (`Message(fid=...)`), and the server pins that version or later. The wire
field is `fid`, 1 to 255 characters from `A-Za-z0-9_-` (a Firebase FID is 22 today; the server does not require exactly
that, in case Firebase changes the length); the register request carries nothing else, because the platform and the app version already come from the
session's device record. The app excludes Firebase installation data from Android backups, because Firebase keeps one
token per FID and a restored backup can invalidate the live one.

The rules, agreed with the Flutter developer (the request and response shapes and the error codes are agreed in the
endpoint request issue, then published in the schema):

- **Ownership.** A FID is stored under the user and the session that registered it, with one FID per `device_id`:
  a new FID from the same device replaces the old one. A FID is never shared between accounts; when another account
  registers the same FID, it moves to that account. The platform comes from the session's device record, so the
  register request does not carry it. Registering from a `web` session is refused with a documented error, because web
  push is not planned. Push is Android only for now; iOS comes later through the same endpoints.
- **Operations.** Two authenticated operations, both idempotent and both using the standard error body and the existing
  rate limits: register or replace the FID, and remove it (it succeeds when there is nothing to remove). Repeating a
  register with the same FID changes nothing, so the app can retry it safely. The app registers only while the person
  allows notifications and again whenever Firebase changes the FID. It removes the FID when it notices that
  notifications are off while the person stays signed in. The switch lives in the system settings, so the app notices it
  at start or on return to the app and the removal can be delayed; the retention below covers the gap.
- **Removal on the server.** The FID of a session is deleted when the session ends for any reason: sign-out, revoked
  by the user, password changed or reset, email changed, refresh-token reuse or expiry. Every FID of an account is
  deleted in the same transaction as the rest of its data when the account is deleted.
- **Invalid FIDs.** A FID that Firebase reports as unregistered or invalid is deleted at once.
- **No leakage.** A FID is never written to a log, never returned by any endpoint, and sent only to Firebase.
- **Content.** A notification has no amounts, names or other financial data. It is worded in the `language` stored on
  the account (see Settings), not in a language kept per device. The app changes that language with the settings
  `PATCH`; a push FID request carries no language.
- **Data key.** The data of a push has one key, `screen`, whose value is one of a fixed list of screen identifiers. The
  first list holds only what pay-day reminders need (`salary`). Every new value is added to the schema and these docs
  before the server sends it. The data never holds amounts, names or record contents. There is no record id for now; if a
  later notification must point at one record, an opaque id is added as its own agreed change. The app treats the data as
  untrusted: it maps a value from its own list, ignores an unknown one and never opens an address or route taken from
  the message.
- **Retention.** A push FID is kept until its session ends or expires, and at most 90 days. This is the sentence the
  privacy policy and the store data forms quote.
- **App side.** The app registers after sign-in (and on a new FID, as above). On sign-out it calls remove
  first with a short timeout and continues on any failure, then unregisters the local FID. On account deletion
  it unregisters the local FID after the server confirms. The app treats every `401` other than `token_expired` as a session the server ended, deletes
  the local FID and does not call remove. This classification stays stable
  ([API conventions](api-conventions.md)).

## Profile and account

| Field | Rule |
| --- | --- |
| `name` | text, 1 to 100 characters, trimmed ([ADR 0001](adr/0001-user-model.md)) |
| `email` | the sign-in identity; changed only through the confirmed flow below |
| register | `name`, `email`, `password`, `language`, `time_zone`; no `device`, because no session exists yet. The answer is the same for a new, a pending and an already registered address (see below). The password has 15 to 128 characters and no other composition rule (no digit or symbol is required); it must not resemble the name or email, be a very common password or appear in a known data breach; each failed rule is its own error code (`password_too_short`, `password_too_long`, `password_too_similar`, `password_too_common`, `password_compromised`, `attr` `password`) |
| verify email | `email`, the 6-digit code and `device`; this call creates the session and returns the tokens, so the app goes straight into the app. A wrong code carries `attempts_left` |
| sign in | `email`, `password`, `device`, `language`, `time_zone` |
| refresh | the refresh token and `device`, so `app_version`, `os_version` and `last_active_at` stay current |
| sign out | the bearer access token, or the `refresh_token` in the body when the access token has expired |
| profile (read) | `GET /api/v1/auth/profile/`: `id`, `name`, `email`, and the stored `language` and `time_zone` (read-only here) |
| profile (edit) | `PATCH /api/v1/auth/profile/` with `name` only; `200` with the profile. Another field is `400 read_only` on that field, never ignored |
| change email | `POST /auth/email-change/` with `email` (the new one) and `current_password`: a code goes to the **new** address. `POST /auth/email-change/confirm/` with the `code`: the address switches and the updated profile is returned. `DELETE /auth/email-change/` cancels |
| email code | 6 digits, to verify the email or to reset the password; resend allowed with a cooldown |
| reset password | three calls, no `device`, and it ends at the sign-in screen: `email` (the same answer for every address), then `email` and the `code` (answers a single-use `reset_token` that lives 10 minutes), then the `reset_token` and the new `password`. It ends **all** sessions. The new password may equal the old one here |
| change password | `current_password` and the new `password` (named as at registration). A wrong current password is `400 invalid_password` on `current_password`; a new password equal to the current one is `400 password_unchanged` on `password`. The other sessions end, this one gets a new token pair |
| delete account | `POST` with the `password`; `204`. A wrong password is `400 invalid_password` on `password`. Everything the account owns is deleted at once and for good, with nothing kept (see below) |

**Passwords** follow NIST SP 800-63B-4: at least 15 characters because a password is the only factor, up to 128 (counted
in Unicode code points, after normalization), no composition rules, spaces and any Unicode text allowed and never trimmed,
normalized to NFKC on the server before it is checked, hashed or compared (so visually identical Arabic typed on different
keyboards is one password), and checked
against the user's own name and email, a list of very common passwords and a breach corpus (only a five-character hash
prefix leaves the server; if that service is unreachable the check is skipped). Sign-in does not apply these rules, only
registration and the later password changes. The policy lives only on the server: a client enforces no password rule,
words each `password_*` code itself, takes the numbers from `min_length` (on `password_too_short`) and `max_length` (on
`password_too_long`), and shows the English `detail` for a `password_*` code it does not know. Every email code (verify,
reset, email change) has 6 digits and is kept as text, so a leading zero survives.

**Registration reveals nothing.** The answer is `201` with the same body whether the address is new, pending or already
registered. A pending address is registered again (the new name, password, `language` and `time_zone` replace the old
ones) and gets a new code. A verified address is left untouched and its owner gets an email saying someone tried, with no
code. The app shows its "enter the code" screen either way; if no code arrives, it suggests signing in or resetting the
password. `resend_too_soon` and `resend_limit_reached` apply to all three cases alike.

**Password reset, change and deletion** ([ADR 0014](adr/0014-password-reset-change-and-account-deletion.md)). Requesting a
reset reveals nothing: the same `200 {code_expires_in, resend_available_in}` for every address, and a reset code is sent only
to a verified, active account. The waits come from the server (`Retry-After` on every `429`, `resend_available_in`,
`code_expires_in`, `expires_in` of the reset token), never from numbers in the app. A `reset_token_invalid` (unknown,
used or expired) sends the user back to the first step. Five wrong current passwords at a change or a deletion lock both for
15 minutes (`429 password_attempts_exceeded`, `Retry-After`); sign-in is unaffected. Each of the three operations also emails
the owner ("password changed", "account deleted") in the user's language.

**What deleting an account removes** (for the privacy policy and the Google Play Data safety form): the profile, the
sessions and device records, the email codes and, as the features arrive, all the accounts, categories, transactions,
salary months, debts and settings of the user, in one step with no recovery. Nothing is anonymised or kept. The address is
used once more for the confirmation email. Server logs and the hosting provider's backups expire on the provider's schedule.

**Name and email change** ([ADR 0015](adr/0015-profile-name-and-email-change.md)): the name is a plain update of the profile
(`PATCH`, 1 to 100 characters, trimmed). Changing the email is `POST` of the new email and the current password, which sends a
6-digit code to the **new** address while the old one stays active, then `POST` of the code, which switches the email, ends
all other sessions, keeps the current one, sends a notice to the old address, and returns the updated profile. A new request
replaces the earlier pending change, and the pending change can be cancelled (`DELETE`, `204` even when none is pending).
Codes expire after 10 minutes with 5 wrong tries each; asking for a change is limited to 5 an hour per account and the new
address has the 60 second cooldown, all with `429` and `Retry-After`. A taken address answers `409 email_already_registered`
(only here: the caller is signed in and has proved the password); the same address as the account's own is
`400 email_unchanged`. The wrong password is `400 invalid_password` on `current_password`, with the lock shared with the
password change and the account deletion.

## Reports and read models

Overview and report endpoints are read models computed on the server from the saved records, in integer minor units and
decimal strings, grouped by currency and never added across currencies. All of them count in the reports rate group,
are never paginated and never cached, and take no write. Which screen uses which one: the Overview uses the Overview; month
against month and part against part use the Overview too (two calls, one per month or part); the Categories view uses the
month report plus the Overview's `fixed_costs` as one more slice; the Debts view uses the debts report.

**Parts.** A month paid twice is cut into two parts by the calendar `date` of each transaction and repayment: days 1 to 15
and day 16 to the end of the month. Whether the month has parts comes from the saved salary month (it keeps the mode it was
saved with), and from the Settings payment mode for a month never saved. `first_part` and `second_part` are `null` for a month
paid once.

**Overview** (`GET /api/v1/overview/months/{month}/?currency=`). `{month}` is `YYYY-MM`, 2000-01 to 2099-12; any other key is
not a route (a plain `404`). `currency` defaults to the Settings default currency (`RUB` until set); an unsupported one is
`400 invalid_choice`. The answer has `month`, `currency`, `has_data`, `currencies`, `total`, `first_part` and `second_part`;
each of the three blocks has `salary`, `recurring_income`, `fixed_costs`, `income_transactions`, `purchases`,
`debt_repayments_received`, `debt_repayments_made` (all positive) and `balance`, signed:

```text
balance = salary + recurring_income + income_transactions - fixed_costs - purchases
          + debt_repayments_received - debt_repayments_made
```

`salary` is the saved month's `total_after_tax` in `total` and each payment in its part; `recurring_income` and `fixed_costs` are
the saved month's fixed items of that kind and part (with one payment every item is in `total`). They count only when the
saved month's currency is `currency`; otherwise those lines are 0 and `currencies` lists the salary month's currency.
`income_transactions` and `purchases` are the income and expense transactions dated in the period, and
`debt_repayments_received` and `debt_repayments_made` are the repayments dated in it on debts lent and borrowed; creating a debt
is not a movement. `salary + recurring_income - fixed_costs` equals the `balances` of the salary month for the same part and for
the whole month: both are worked out by `salary.presentation.part_figures`, and a test enforces it. `has_data` is true when the
month has a saved salary month, or any transaction or repayment dated in it, in any currency; `currencies` lists the currencies
of those, ordered by code.

**Month report** (`GET /api/v1/reports/months/{month}/?currency=`). It reads transactions only: `income_transactions` is the
income transactions of the month (the salary and the recurring income are not in it) and `purchases` the expense transactions
(the fixed costs are not in it); they equal the Overview's lines of the same name for the same month, part and currency.
`categories` is the spending by category, expense transactions only, one item per category that has any, the biggest `amount`
first (ties by `category_id`), each `{category_id, category_key, name, icon, amount}`; the amounts add up to `purchases`.
`category_key` is `null` for a custom category and `name` is the custom name or the English fallback of a built-in. There are no
income categories. The parts have the same three fields. `has_data` is true when the month has any transaction in any currency,
and `currencies` lists them; a month with nothing is `200` with zero totals and no categories. The two calls of the Categories
view are not one transaction, so a record saved between them can make the figures differ until the next read.

**Debts report** (`GET /api/v1/reports/debts/?today=`). `has_data` is true when the person has any debt, settled or not.
`by_currency` has one block per currency that has unsettled debts, ordered by code: `currency`, `owed_to_you` (the `remaining`
of unsettled debts lent), `you_owe` (the same for debts borrowed), and `overdue` and `open`, each item `{id, person, direction,
remaining, status, due_date}`. Settled debts are not listed. `overdue` is ordered by `due_date`, the oldest first; `open` by
`due_date` with the debts that have none last; ties by `id`. The `status` is counted in the person's day (Settings `time_zone`),
and `today` replaces it (`400 invalid`, or `400 date_out_of_range` outside 2000-01-01 to 2099-12-31).

**Day totals** are `GET /api/v1/reports/day-totals/` with the transaction filters except `search`, `date_from` and `date_to`
optional and no limit on the span. A page is `limit` days (50 by default, 100 at most), newest day first like the list; each item
is one day with its currencies inside, `{date, totals: [{currency, total}]}`, where `total` is a signed amount (income minus
expense) of that day in that currency, so a day always arrives whole ([ADR 0003](adr/0003-cursor-pagination.md)).

**Test vectors.** `reports/tests/overview_vectors.json` and `reports/tests/report_vectors.json` hold invented cases with
figures worked out by hand (the worked example of ADR 0007, one and two payments, no saved month, another currency, only
repayments, an empty month, the 15th, the 16th and the last day, JPY and KWD, a negative balance, a deleted category, debts in
two currencies). The server and the app run the same files.

## CSV

The export and the import live in the `transfer` app. The data is `accounts`, `categories` (the person's custom ones only),
`transactions`, `debts` (with their repayments) and `salary_months` (with their fixed items), always in this order: it is
the order of a restore, because a transaction names an account and a category. Settings are not exported. Accounts and
categories are in the file so that it can be restored into an empty account (see "Restoring into an empty account" below).

**Export** (`POST /api/v1/exports/`, a `POST` so the password is never in a URL). Body: `kinds` (1 to 5 different values),
`from` and `to` (optional inclusive days; both left out is all time) and `current_password`. The period applies to a
transaction's `date`, a debt's `start_date` and, counted in months, to a salary month's month; accounts and categories have no
date, so it does not apply to them (all of them are exported). One kind is answered as
`text/csv; charset=utf-8`, several as `application/zip` with `accounts.csv`, `categories.csv`, `transactions.csv`, `debts.csv`
and `salary_months.csv`, always in this order whatever the order of `kinds`, with a
fixed `Content-Disposition` file name (`money-scribe-transactions.csv`, `money-scribe-export.zip`: no personal data and no
date), `Cache-Control: no-store` and `X-Content-Type-Options: nosniff`. The answer ignores the `Accept` header.

Each file is UTF-8 with a byte order mark, a fixed header row, `,`, `"` quoting and CRLF line ends, oldest first (date, then
the order the records were added), so a re-import keeps the order within a day. An empty cell means none.

| File | Columns, in this order |
| --- | --- |
| `accounts` | `name`, `kind` (`card`, `cash` or `other`); all of the person's accounts, oldest first |
| `categories` | `name`, `icon` (one of the eight icons of the picker: `gifts`, `health`, `entertainment`, `shopping`, `education`, `travel`, `subscriptions`, `other`); the custom categories only, oldest first. A custom category is always of the kind `both`, so there is no `kind` column, and a built-in category is the same for everyone, so it is not in the file |
| `transactions` | `date`, `kind`, `amount`, `currency`, `category_key`, `category_name`, `account_name`, `note` (a built-in category has its key and an empty name, a custom one the reverse) |
| `debts` | `direction`, `person`, `amount`, `currency`, `start_date`, `due_date`, `note`, `repayments` (a JSON array of `{date, amount}`, `[]` when none) |
| `salary_months` | `month`, `currency`, `base_salary`, `food_per_day`, `working_days_in_month`, `working_hours_in_month`, `extra_hours`, `tax_rate`, `payment_mode`, `working_days_first_half`, `first_pay_day`, `second_pay_day`, `fixed_items` (a JSON array of `{name, amount, kind, part}`, and `day` when the item has one) |

The computed results of a salary month are not exported: the server recomputes them. `extra_hours` is hours with exactly two
decimals (`7.50`, 0.00 to 744.00) and `tax_rate` a percent with exactly two decimals (`15.00`, 0.00 to 100.00); they convert
exactly to `extra_hours_hundredths` and `tax_rate_basis_points` (whole numbers of hundredths, no floating point), in both
directions. The salary month API keeps its own names and units. **Formula injection:** a text cell (`name` of an account
or a category, `note`, `person`, `category_name`, `account_name`) that starts with `=`, `+`, `-`, `@`, a tab, a carriage return or a line feed gets one `'` in
front, and so does one that starts with any number of `'` followed by one of those (otherwise removing one on the way back
would lose a quote that was part of the text). The app removes exactly one leading `'` when the rest of the cell starts with
any number of `'` followed by one of those characters. Amounts, dates, numbers and the JSON cells are never changed. The rule
is `transfer/cells.py`; `transfer/tests/csv_vectors.json` holds invented cells and a round trip of each dataset, and the app
runs the same file. How current Excel, Google Sheets and LibreOffice open the file is not verified.

**Security of the export.** The password is checked like at a password change (`400 invalid_password`, the shared lock
`429 password_attempts_exceeded`); the export group is limited to 5 an hour per person (`429 throttled`); while the limit
store is down it answers `503 service_unavailable`. A `data_exported` security event is written in the same transaction (the
kinds and the number of rows, never a value) and the owner gets an email, in the account's language, that an export was made
(the device name and the time in the person's `time_zone`, no content and no link). Nothing of the content is logged.

**Import** (`POST /api/v1/imports/`). The app reads the CSV and sends rows as JSON; the server never parses an uploaded file.
Body: `dataset` (`accounts`, `categories`, `transactions`, `debts` or `salary_months`), `dry_run` (a JSON boolean, default `false`) and `rows` (1 to 500).
In the schema the request is a `oneOf` with the discriminator `dataset`: one typed row per dataset (`AccountImportRow`,
`CategoryImportRow`, `TransactionImportRow`, `DebtImportRow`, `SalaryMonthImportRow`), whose properties are the CSV columns.
The call itself only checks the frame (a known dataset, 1 to 500 rows, a unique `row` each); a wrong value in a row is a
problem of that row, never a `400` for the call.
A row is an object with `row` (the line in the file: a positive whole number, unique in the call; anything else is
`400 invalid` on `rows`) and the fields of the dataset named like the CSV columns. An empty cell is a missing field, the JSON
cells are arrays, the whole-number columns of the salary months (`working_days_in_month`, `working_hours_in_month`,
`working_days_first_half`, `first_pay_day`, `second_pay_day`) are JSON numbers and every amount is a string with exactly the
currency's digits (a client may pad a cell with trailing zeros, never round it). A field that is not a column (an `id`, say)
is a problem `read_only` on it.

| Dataset | Row fields |
| --- | --- |
| `accounts` | `name`, `kind` |
| `categories` | `name`, `icon` |
| `transactions` | `date`, `kind`, `amount`, `currency`, exactly one of `category_key` (a built-in category) and `category_name` (one of the person's own, in any case), `account_name` (in any case), `note` |
| `debts` | `direction`, `person`, `amount`, `currency`, `start_date`, `due_date`, `note`, `repayments` |
| `salary_months` | the columns of the file, with `fixed_items` as an array |

Every row goes through the rules of a single create (a `PUT` for a salary month), so it gets the same codes; a problem inside
`repayments` or `fixed_items` has a dotted `attr` such as `repayments.1.amount`. **The call is one database transaction**; each
row has its own savepoint: a row that passes is written, a row that fails is skipped and reported with its first problem, and a
crash leaves nothing half-written. The answer is always `200`: `dataset`, `dry_run`, `imported`, `skipped` and `problems[]`
(one `{row, code, attr}` per skipped row, ordered by `row`).

- **Unknown category or account:** `unknown_category` (a `category_key` or `category_name` that matches nothing, or a
  `category_name` that is a built-in category: a built-in is named by its key) or `unknown_account`; a transaction row never
  creates an account or a category, only the datasets `accounts` and `categories` do. Both columns together are the row
  problem `invalid` on `category_name`; neither is `required` on `category_key`.
- **Accounts and categories:** a row gets the codes of a single create: an unknown `kind` or `icon` is `invalid_choice`, a
  missing `icon` is `required` (there is no default icon; the app tells the person), a `kind` column on a category is
  `read_only` (a custom category is always `both`), a name that is a built-in key or name is `name_taken`, and the caps are
  `account_limit_reached` (20) and `category_limit_reached` (50) with `attr` `null`. A name that the person already has,
  compared after trimming and Unicode NFC normalization and without regard to case, is `duplicate` (`attr` `name`): the
  existing record is kept as it is (its kind or icon is not changed), also inside one file and also at the cap. The server
  changes nothing else of a name (no NFKC, no removal of diacritics or tatweel). The two accounts made at sign-up are
  duplicates only when the names are equal.
- **Duplicates:** ids are never read. A row equal to an existing record (transactions: date, kind, amount, currency, category,
  account, note; debts: direction, person, amount, currency, start date, due date, note, whatever the repayments) is
  `duplicate`, counted by multiplicity: with 2 identical records in the account and 3 in the file, one is written. A salary
  month that exists is `duplicate` (attr `month`) and is never overwritten. Identical rows split across calls are compared
  with the database only, so a repeat of an existing record in a later call is `duplicate`, never silently lost.
- **Record caps apply:** rows beyond the cap are skipped with `account_limit_reached`, `category_limit_reached`,
  `transaction_limit_reached`, `debt_limit_reached`, `repayment_limit_reached` or `fixed_item_limit_reached` (`attr` `null`),
  and the rest are written up to the cap.
- **Idempotency:** a real import needs an `Idempotency-Key` ([ADR 0016](adr/0016-safe-writes.md)); a dry run needs none,
  writes nothing and stores nothing. A dry run is the same work rolled back, so it is exact for the call, but it cannot see rows
  of the earlier calls of the same file. Two consequences for the app: never split one day between two calls (group
  transactions by `date` and debts by `start_date` before cutting calls of 500), because identical rows split across calls
  are compared with the database only; and a dry run of `transactions` cannot see accounts or categories that were not really
  imported yet, so import those two for real first.
- **Limits:** 500 rows and 1 MB a call (`400 request_too_large`), 120 calls an hour, dry runs included; while the limit store
  is down it answers `503 service_unavailable`.
- **Security activity:** a `data_imported` event (the dataset and the rows written) is written for each real call that writes at
  least one row; a dry run, a failed call and a call that skips every row write none. Events are never merged on the server, so
  a file of 10 000 rows is up to 20 events of at most 500 rows; the app shows consecutive entries of the same session, less
  than 15 minutes apart, as one ([ADR 0022](adr/0022-security-events.md)).

An export must import back without loss; amounts use the form of ADR 0008, and the round trip is tested on the shared vectors
and on seeded random data with risky text.

**Restoring into an empty account.** Import in this order, each dataset in calls of at most 500 rows: `accounts`,
`categories`, `transactions`, `debts`, `salary_months`. The first two are small (at most 20 accounts and 50 custom
categories), so do their real imports first; the transactions can then be sent as real calls, which report the same
problems as a dry run. Nested problems carry the index, which starts at 0: `repayments.0.date`, `fixed_items.1.amount`.

## Sessions

The `device` object, the session fields, the session operations and their retention are in
[ADR 0009](adr/0009-sessions-and-devices.md); the shapes are in the schema (tag `sessions`).

- `GET /api/v1/auth/sessions/?status=active|ended`: `active` (the default) is the live sessions, the current one first,
  then `-last_active_at`, `-id`, at most 10, always the whole list in the cursor envelope (`next` and `previous` `null`;
  `limit` and `cursor` are ignored). `ended` is the sessions that ended in the last 90 days, `-created_at`, `-id`, with the
  usual cursor (`limit` 50, at most 100). A session is live when it has not been ended and its refresh token has not run
  out; one nobody used for 30 days is `ended` with `end_reason` `expired` and `ended_at` the moment its token ran out (worked
  out on read, nothing is written). Each entry has `id`, `device_id`, `device_name`, `platform`, `os_version`, `app_version`
  (shown, never parsed), `signed_in_at`, `last_active_at` (updated at most every 5 minutes), `expires_at`, `is_current`,
  `sign_in_method`, `network` (the stored address cut to a `/24`, or a `/48` for IPv6, or `null`) and, for an ended session,
  `ended_at` and `end_reason` (`signed_out`, `revoked_by_user`, `expired`, `session_limit`, `password_changed`,
  `password_reset`, `email_changed`, `token_reuse`, `replaced`; a client treats an unknown value as a generic "ended").
  A device that signed in five times has five rows with the same `device_id`; the app groups them.
- `DELETE /api/v1/auth/sessions/{id}/` ends one of the person's own sessions (`204`; one already over is also `204`; another
  person's, or an unknown id, is `404`). The current session is a sign-out and the fingerprint key stays. Another session
  ends as `revoked_by_user` and the fingerprint key of its device is removed in the same transaction, unless it is the
  current device's; there is no opt-out.
- `POST /api/v1/auth/sessions/revoke-others/` with `current_password` ends every other session and removes every device key
  but the current device's, including keys of signed-out phones; it answers `{ended, device_keys_removed}`. A wrong password
  is `400 invalid_password` with the lock shared with the password change; a field that is not `current_password` is
  `400 read_only`.
- **Limits.** At most 10 live sessions per account: a sign-in at the limit ends the least recently active one as
  `session_limit` (that device gets `401 session_limit_reached`, and a `session_limit_reached` entry is written). A sign-in
  from a `device_id` that has a live session ends the older one as `replaced` (that device gets `401 session_signed_out`).
  Both rules run under a lock on the user's row.
- `GET /api/v1/auth/session-history/` is the security activity ([ADR 0022](adr/0022-security-events.md)): `-occurred_at`,
  `-id`, cursor-paginated, entries of the last 90 days only. A client treats an unknown `type`, `platform` or
  `sign_in_method` as an open value.
- **The new-device email.** A sign-in from a `device_id` that has no session for the account within the retention period
  sends the owner an email with the device name, the network and the time, in the account's language, with no link; the
  account's very first sign-in is not announced, and at most 5 such emails go out an hour per account.

## Device keys (fingerprint sign-in)

The rules, calls and error codes are in [ADR 0018](adr/0018-sign-in-with-a-device-key.md); the shapes are in the schema
once the operations exist.

| Field | Rule |
| --- | --- |
| `device_id` | the install id of ADR 0009, a UUID |
| `challenge_id`, `challenge` | the answer of `challenge/`; the challenge is 32 random bytes in base64url and works once for 2 minutes |
| `public_key` | the device's P-256 public key, SPKI in standard base64 with padding (a raw 65-byte point is also read) |
| `algorithm` | `ES256` only |
| `signature` | ECDSA P-256 over SHA-256 of the signed text, DER, standard base64 with padding |
| `attestation_chain` | the Android certificate chain, a list of base64 DER certificates; required for an Android session, `null` or left out on iOS |
| `current_password` | the account password; a wrong one is `400 invalid_password` and shares the lock of the password change |

Up to 5 keys per account, one per `device_id`, valid 30 days from the last use. The list of keys holds at most 5, so
it is one page in the envelope of every list (`next` and `previous` are `null`). The privacy sentence is the row of
ADR 0009's table.
