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
- Ids are UUID strings ([ADR 0005](adr/0005-apps-and-model-conventions.md)). Text is trimmed before it is validated.
- A rule that fails answers `400` with the codes of [ADR 0004](adr/0004-error-status-codes.md).

## Salary month

Addressed by `YYYY-MM`; one per user and currency; `PUT` creates or replaces it, `GET` reads it, `DELETE` removes it.

| Field | Rule |
| --- | --- |
| year and month | the key of the month |
| `currency` | one currency for the whole month |
| `base_salary` | amount, greater than 0 |
| `food_per_day` | amount, 0 or more |
| `working_days_in_month` | whole number, 1 to 31 |
| `working_hours_in_month` | whole number, 1 or more |
| `extra_hours_hundredths` | whole number, 0 or more (the user types hours with up to 2 decimals) |
| `tax_rate_bp` | whole number, 0 to 10000 (the user types a percent with up to 2 decimals; the app defaults to 13%) |
| `payment_mode` | `one_payment` or `two_payments`, kept with the saved month |
| `working_days_first_half` | whole number, 0 up to `working_days_in_month`; used only with two payments |
| `first_pay_day`, `second_pay_day` | day of month, 1 to 28, kept with the saved month |

A saved month can be corrected later, including switching between one and two payments. Its fixed items keep their `part`
(ADR 0007). The results, the fixed-item totals, the balance after fixed items per part, and, with two payments, the
balance of the whole month are computed by the server.

## Fixed item

Belongs to a month and is returned inline with it. Create, edit and delete.

| Field | Rule |
| --- | --- |
| `name` | text, 1 to 50 characters |
| `amount` | amount, greater than 0 |
| `kind` | `income` or `expense` |
| `part` | `first` or `second`, ignored for a month with one payment (then every item counts); defaults to `first` |

A fixed item has no currency of its own: it uses the month's. A request that carries another currency answers
`400 currency_mismatch`.

## Transaction

| Field | Rule |
| --- | --- |
| `amount` | amount, greater than 0 |
| `currency` | ISO code; the account has no currency, the transaction carries it |
| `kind` | `expense` or `income` |
| `date` | calendar day |
| `category` | id of a category, required; it must allow the kind |
| `account` | id of an account, required |
| `note` | text, optional, up to 100 characters |

Create, edit and delete. The list is ordered `-date`, `-created_at`, `-id`, so within a day the entry added last comes
first, and takes the filters `date_from`, `date_to`, `account`, `category` and `kind`
([ADR 0003](adr/0003-cursor-pagination.md)). Day totals take the same filters.

## Account

| Field | Rule |
| --- | --- |
| `name` | text, 1 to 30 characters |
| `kind` | `card`, `cash` or `other` |

Create, edit and delete. Deleting an account that has transactions answers `409 account_has_transactions`.

## Category

| Field | Rule |
| --- | --- |
| `name` | text, 1 to 30 characters |
| `icon` | a slug, `^[a-z0-9_]{1,40}$`; the app maps known names to its icons and falls back to a default for an unknown one |
| `kind` | `expense`, `income` or `both`; the server sets `both` for a custom category |
| `is_custom` | read-only |
| `key` | read-only; a stable key on built-in categories, `null` on custom ones |

Create, edit and delete for custom categories only. Built-in categories are read-only: changing or deleting one answers
`403 category_is_builtin`. Deleting a category that has transactions answers `409 category_has_transactions`.

**Built-in categories** are shared rows created by a data migration and returned with the user's own. Each carries its
`id`, a stable `key` that the app localizes (English and Arabic), an `icon`, a `kind` and an English `name` as a fallback
for a key the app does not know yet. The first list, which the owner may still change:

| Key | Icon | Kind |
| --- | --- | --- |
| `groceries`, `dining`, `transport`, `housing`, `utilities` | the same slug | `expense` |
| `scholarship` | `scholarship` | `income` |
| `gifts`, `other` | the same slug | `both` |

The icon slugs the app knows today are `groceries`, `dining`, `transport`, `housing`, `utilities`, `health`,
`entertainment`, `shopping`, `education`, `travel`, `gifts`, `subscriptions`, `scholarship` and `other`. The server does
not enumerate them; it validates the slug shape only.

## Debt and repayment

| Field | Rule |
| --- | --- |
| `direction` | `lent` or `borrowed` |
| `person` | text, 1 to 50 characters |
| `amount` | amount, greater than 0 |
| `currency` | ISO code |
| `start_date` | calendar day |
| `due_date` | calendar day, optional, not before `start_date` |
| `note` | text, optional, up to 100 characters |

A **repayment** has an `amount` greater than 0 and not more than the remaining debt (`400 exceeds_remaining`) and a
`date`. When a repayment is edited, its own previous amount does not count: the remaining amount for the check is the
debt minus the **other** repayments. Both are created, edited and deleted. The server returns the derived `repaid`,
`remaining` (never below 0), `is_settled`, `progress` and `status` (`settled`, `overdue` or `open`), because the overview
and the debts report use them too. "Overdue" needs the user's day, so a read takes an optional `today` calendar day; it
defaults to the current day in the user's `time_zone` (see Settings), and it also drives any filter or order by `status`.
The app sends explicit dates for "this month" and "this year" in reports and in the CSV export period.

## Settings

| Field | Rule |
| --- | --- |
| default currency | one of the supported currencies; defaults to `RUB` |
| default payment mode | `one_payment` or `two_payments`; defaults to `one_payment` |
| `first_pay_day`, `second_pay_day` | day of month, 1 to 28; default 20 and 5 |
| `remind_on_pay_days` | true or false; defaults to `false` |
| `time_zone` | an IANA name such as `Europe/Moscow`, validated; defaults to `UTC` |
| `language` | `en` or `ar`; defaults to `en` |

The defaults are the app's own, used when a field has never been set. Theme, the PIN app lock and the notification
permission stay on the device and have no field.

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

The device sends its Firebase token and the user turns the permission on or off on the device. A notification never
contains an amount or a name. The server builds the text in the user's `language`, so it also shows when the app is
closed, and adds a data key so the app can open the right screen. The token belongs to the session and is removed when
the session ends ([ADR 0009](adr/0009-sessions-and-devices.md)). The endpoints come with the `notifications` feature.

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

Overview and report endpoints are read models computed on the server. They take `part=first|second|all`, where the parts
are calendar halves (days 1 to 15 and day 16 to the end of the month). Day totals are
`GET /api/v1/reports/day-totals/` with the transaction filters, `date_from` and `date_to` required and at most 366 days
apart: one entry per day that has entries and per currency, newest day first like the list, with one net `SignedAmount`
total (income minus expense).

## CSV

| Field | Rule |
| --- | --- |
| export | which data (`transactions`, `debts` or `salary_months`) and a period: `all_time`, `this_year`, or `custom` with `from` and `to` |
| import | a file; the answer is how many rows were imported and which rows were skipped, each with its row number and a reason, `invalid_amount` or `missing_date` |

An export must import back without loss; amounts use the form of ADR 0008, and the round trip is tested.

## Sessions

The `device` object, the session fields, the three session operations and their retention are in
[ADR 0009](adr/0009-sessions-and-devices.md).
