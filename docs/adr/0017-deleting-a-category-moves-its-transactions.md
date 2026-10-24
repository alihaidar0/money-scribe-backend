# ADR 0017: Deleting a category moves its transactions to Other

- Status: accepted
- Date: 2026-10-23

## Context

`docs/api-fields.md` first said that deleting a category with transactions is refused (`409 category_has_transactions`).
The app's screen says the opposite: deleting a custom category moves its transactions to Other. A refusal would force the
person to edit every transaction by hand before a label could be removed, and a label carries no money.

## Decision

- Deleting a custom category succeeds with `204`. Its transactions are reassigned to the built-in category `other` in the
  same database transaction as the delete, so nothing is left without a category and a failure changes nothing.
- This is safe for the kind rule: `other` has the kind `both`, so it allows every transaction kind.
- The reassignment changes only `category`, bumps each moved transaction's `updated_at` and `version`, and never touches
  an amount, a date or an account.
- Built-in categories still cannot be changed or deleted (`403 category_is_builtin`).
- **Accounts are different and stay as decided:** deleting an account that has transactions is refused with
  `409 account_has_transactions`. There is no neutral account to move money to, and moving it silently would change
  balances.
- `category_has_transactions` stays in the registry, marked as reserved: the server no longer sends it. Removing a code is
  a breaking change (`docs/api-workflow.md`), and a client may already have a message for it.

## Consequences

- The delete is not reversible by the server. The app confirms with the person first and says how many transactions move
  (it knows from its own list; a count endpoint is not added).
- The endpoint test covers a category with transactions of both kinds, that `other` receives them, that the other user's
  rows are untouched, and the `403` for a built-in category.
