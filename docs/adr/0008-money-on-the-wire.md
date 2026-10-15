# ADR 0008: Money on the wire

- Status: accepted (the Flutter developer chose option A on 2026-10-15)
- Date: 2026-10-15

## Context

Money is stored as integer minor units plus a currency code, and arithmetic never uses floats. Amounts round half away
from zero, once, at the end of a formula, and mixing two currencies is an error. The JSON the API exchanges with the app
had to be agreed with the person who writes the client, because the generated client and every screen depend on it.

Currency digits: RUB, USD, EUR, GBP, TRY, SAR, AED and EGP have 2, JPY has 0 and KWD has 3. The largest amount is
999 999 999 999 999 minor units (15 digits), which is below JavaScript's largest safe integer (9 007 199 254 740 991).

## Options considered

| Option | JSON for 12.50 USD | Verdict |
| --- | --- | --- |
| A. Decimal string and currency | `"amount": "12.50", "currency": "USD"` | **chosen**: the app already reads and writes this form, and it is also the CSV form |
| B. Integer minor units and currency | `"amount": 1250, "currency": "USD"` | the scale is implicit and raw JSON is harder to read |
| C. An object | `"price": {"amount": "12.50", "currency": "USD"}` | adds a nested class to every generated model |

## Decision

Option A. An amount is a string in a field named `amount` (or a more specific name), sent as a **sibling** of a `currency`
field that holds the ISO 4217 code.

**Format.** The string has exactly the currency's number of fraction digits (`"12.50"`, `"12.500"` for KWD, `"1200"` for
JPY), `.` as the separator, no grouping, no exponent and no leading `+`. The whole amount has at most 15 digits counting
the fraction digits. The server rejects more digits than the currency allows with `400 invalid_amount`, and never sends
`-0`.

**Two schema types**, both `type: string, format: decimal`, each with a pattern. The exact digit count depends on the
currency, so the server validates that part:

| Type | Pattern | Used for |
| --- | --- | --- |
| `Amount` | `^(0\|[1-9][0-9]*)(\.[0-9]{1,3})?$` | stored amounts and their inputs; a transaction, repayment, debt or fixed item is greater than zero, a food allowance may be zero |
| `SignedAmount` | `^-?(0\|[1-9][0-9]*)(\.[0-9]{1,3})?$` | computed values that can be negative |

Stored resources stay positive and the kind or direction carries the sign. The signed fields are the balance after fixed
items, the overview balance, day totals and report differences; every other amount is an `Amount`. The contract names the
type of each field.

## Consequences

- `feat/money-core` provides the `Money` value type and the digits table; its decimal-string parsing and writing follow
  this format exactly, and the serializers of every endpoint use one shared field.
- The `docs/api-conventions.md` section "Money" states the rules above.
- The CSV export and import use the same string form, so a file round-trips without loss.
