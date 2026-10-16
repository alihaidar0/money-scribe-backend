# ADR 0001: The user model

- Status: accepted
- Date: 2026-10-15

## Context

The mobile client signs users in with an email address and a password. It shows a single display name, verifies the
address with a 6-digit code, and deletes the whole account on request. Swapping `AUTH_USER_MODEL` after the first
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
| `language` | `en` or `ar`, default `en`: the language of emails and pushes. Set by the device the user signs in on |
| `time_zone` | an IANA name, default `UTC`: decides the user's "today". Set by the device the user signs in on |
| `date_joined` | UTC timestamp, set on creation |
| `is_active`, `is_staff`, `is_superuser` | All default to the safe value (`is_active` is true, the others false) |

**Passwords** follow NIST SP 800-63B-4. A password is the only factor, so it needs at least 15 characters, and up to 128 are
accepted. There are no composition rules: no digit, capital or symbol is required, and spaces and any Unicode text are
allowed and never trimmed. A password is refused when it resembles the user's own name or email, is on the common-password
list, or appears in a breach corpus (the Pwned Passwords range API, queried with the first five characters of the SHA-1
only; if the service cannot be reached the check is skipped, never the sign-up). Every password is normalized to NFKC
before it is checked, hashed or compared, so one password is one value on every keyboard. The rules are Django password
validators (`AUTH_PASSWORD_VALIDATORS`; the two custom ones are in `users/validators.py`), each failed rule with its own
code (ADR 0004).

An account that never verifies its email is deleted, with its codes, after seven days of inactivity (a daily task,
`users.tasks.purge_unverified_accounts`): it holds an address and a password hash for someone who never proved the address
is theirs. Staff and superuser accounts are never purged.

Not stored on the user: the email codes and the sign-in attempt counter (they expire), device tokens for notifications
(one row per device), the other user settings (the `preferences` app) and any profile data such as a photo or phone number.

`language` and `time_zone` are on the user, not in `preferences`, because the sign-in and registration code in `users` has
to write them and `users` depends on no other app (ADR 0005); the settings endpoint of `preferences` will read and change
them through `users`.

Every model that holds user data points at `User` with `on_delete=CASCADE`, so deleting the account deletes everything
the user owns in one transaction.

## Consequences

- Sign-in looks the address up after trimming and lower-casing, so `A@x.com` and `a@x.com` are one account.
- `email` carries `unique=True` in addition to the `Lower("email")` constraint: Django's system check for
  `USERNAME_FIELD` accepts only a plain unique field.
- Registration answers the same whether the address is new, pending or already verified, so it cannot be used to find out
  who has an account. A pending (unverified) address can be registered again, replacing the earlier name and password; a
  verified one is left untouched and its owner gets an email saying someone tried. That behaviour belongs to the endpoints,
  not to the model.
- Adding a field later is a normal migration. Changing the primary key type is not, which is why it is decided here.
