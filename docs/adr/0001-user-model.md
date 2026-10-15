# ADR 0001: The user model

- Status: accepted
- Date: 2026-10-15

## Context

The mobile client signs users in with an email address and a password. It shows a single display name, verifies the
address with a 4-digit code, and deletes the whole account on request. Swapping `AUTH_USER_MODEL` after the first
migration is costly, so the shape of `users.User` is fixed before the first `migrate`.

## Decision

`users.User` extends `AbstractBaseUser` and `PermissionsMixin` and uses a custom manager.

| Field | Decision |
| --- | --- |
| `id` | `UUIDField` primary key, never an integer, so ids reveal nothing about the number of users |
| `email` | The only sign-in identifier. Trimmed and lower-cased on save. Unique, with a second constraint on `Lower("email")` that also rejects case variants written without `save()` |
| `name` | One required display name, at most 100 characters, trimmed, any Unicode letters. An empty name is rejected by a database constraint |
| `email_verified_at` | Empty until the email code is accepted; an account cannot sign in before then |
| `password`, `last_login` | From `AbstractBaseUser` |
| `date_joined` | UTC timestamp, set on creation |
| `is_active`, `is_staff`, `is_superuser` | All default to the safe value (`is_active` is true, the others false) |

Passwords need at least 8 characters and at least one digit, and must not be in the common-password list. The digit rule
is a small validator in `users/validators.py`.

Not stored on the user: the email codes and the sign-in attempt counter (they expire), device tokens for notifications
(one row per device), user settings (the `preferences` app) and any profile data such as a photo or phone number.

Every model that holds user data points at `User` with `on_delete=CASCADE`, so deleting the account deletes everything
the user owns in one transaction.

## Consequences

- Sign-in looks the address up after trimming and lower-casing, so `A@x.com` and `a@x.com` are one account.
- `email` carries `unique=True` in addition to the `Lower("email")` constraint: Django's system check for
  `USERNAME_FIELD` accepts only a plain unique field.
- Registering an address that is already verified must be refused by the API; an unverified address can be registered
  again, replacing the earlier name and password. That behaviour belongs to the endpoints, not to the model.
- Adding a field later is a normal migration. Changing the primary key type is not, which is why it is decided here.
