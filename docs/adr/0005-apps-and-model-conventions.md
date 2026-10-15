# ADR 0005: Django apps and model conventions

- Status: accepted
- Date: 2026-10-15

## Context

Moving a model between apps later means new table names and migration work, so the split is fixed before the second
app exists. Several rules also apply to every model and are cheaper to state once.

## Decision

Apps follow the entity groups of the client's domain. An app is created when its first feature starts, not up front.

| App | Holds | Depends on |
| --- | --- | --- |
| `users` | the user, email codes, sessions with their device records (ADR 0001, 0006, 0009) | none |
| `outbox` | the database email backend, the stored emails, their admin and purge task (ADR 0010) | none |
| `ledger` | accounts, categories, transactions | `users`, `common` |
| `salary` | salary months and the calculation (ADR 0007) | `users`, `common` |
| `debts` | debts and repayments | `users`, `common` |
| `preferences` | default currency, pay schedule, notification preferences | `users`, `common` |
| `notifications` | push device tokens, each tied to a session, and reminders (Celery Beat) | `users`, `preferences` |
| `reports` | read models only (overview, day totals, category spend, comparisons, debts and period reports) | `ledger`, `salary`, `debts`, `users` |

- `common/` is a plain Python package at the repository root, not an installed app: no models and no migrations. It holds
  code that several apps share, such as the `Money` value type.
- `config/` keeps settings and project-wide API plumbing (error schema, pagination, health, Celery).
- Dependencies point one way: `users` and `common` depend on nothing in the project, and nothing imports `reports`. An app
  never imports another app's models for writing; it calls that app's service functions.
- Business rules live in plain, typed `services.py` modules that are tested without HTTP.

Model conventions for every new model:

- **Primary key:** `UUIDField(primary_key=True, default=uuid.uuid7, editable=False)`. UUIDv7 (available in Python 3.14) is
  time-ordered, so it indexes well and `-id` is a creation-order tie-break for cursor pagination (ADR 0003). `users.User`
  keeps its random UUIDv4 (ADR 0001): an account id should not reveal when the account was created.
- **Owner:** user-owned rows have `owner = ForeignKey(settings.AUTH_USER_MODEL, on_delete=CASCADE, related_name=...)`, and
  every query starts from the signed-in user. The one exception is shared reference data: the built-in categories are rows
  with no owner and a stable `key`, created by a data migration, read-only for everyone, and listed together with the
  signed-in user's own categories.
- **Timestamps:** `created_at` and `updated_at`, UTC.
- **Money:** `BigIntegerField` minor units plus a `CharField(max_length=3)` currency code, with a `CheckConstraint` where
  the amount must be positive. The wire format is ADR 0008.
- **Tests:** `<app>/tests/`, with factories in `tests/factories.py`.

## Consequences

- Adding a model is a decision about which app owns it; this table is the place to check.
- `reports` cannot grow its own tables without a new decision, which keeps read models derived from the source rows.
- The first model outside `users` is the point to apply these conventions and, if one proves wrong, to amend this ADR
  before it spreads.
