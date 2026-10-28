# ADR 0007: The server owns the salary calculation

- Status: accepted
- Date: 2026-10-15

## Context

The client already knows the salary formulas, and its fake repositories compute them. The server, however, is the only
place financial data lives, and the reports (month comparison, period report) need the same numbers. Two implementations
that must agree to the minor unit will drift.

## Decision

The server is the source of truth. The client sends the **inputs** of a salary month; the server computes and returns the
results, and a client never sends computed values.

- The calculation is one pure, typed function in the `salary` app (ADR 0005), working on integer minor units, with no
  database access, tested without HTTP.
- Rounding is half away from zero (`decimal.ROUND_HALF_UP`), applied once per formula, last, with integers.
- A saved month stores its inputs, its results and a `calculation_version`, so a later change of the formulas never
  rewrites history. A month keeps the payment mode it was saved with.
- The client may compute a **preview** with the same formulas while the user types. The saved values are the server's, and on
  any difference the server wins.

The formulas, with `bp` for basis points:

```text
food_allowance    = food_per_day * working_days_in_month
extra_hours_pay   = round(base * 2 * extra_hours_hundredths / (working_hours_in_month * 100))
income_before_tax = base + food_allowance + extra_hours_pay
tax               = round(income_before_tax * tax_rate_bp / 10000)
total_after_tax   = income_before_tax - tax
one payment:      payments = [total_after_tax]
two payments:     first  = min(round(base * working_days_first_half * (10000 - tax_rate_bp) / (working_days * 10000)), total_after_tax)
                  second = total_after_tax - first
```

Worked example, invented values in minor units: `base` 300000, `food_per_day` 500, `working_days_in_month` 22,
`working_hours_in_month` 176, `extra_hours_hundredths` 750, `tax_rate_bp` 1500, `working_days_first_half` 11.

| Step | Result |
| --- | --- |
| `food_allowance` | 11000 |
| `extra_hours_pay` | 25568 |
| `income_before_tax` | 336568 |
| `tax` | 50485 |
| `total_after_tax` | 286083 |
| two payments | 127500 and 158583 |
| one payment | 286083 |

## The month, its fixed items and its parts

- In the formulas, `tax_rate_bp` stands for the field `tax_rate_basis_points`; the wire name is spelled out.
- A salary month is addressed by its year and month, `YYYY-MM`, one per user and month; the currency is a field of the month
  (decided 2026-10-24). `PUT` on that key creates the
  month or replaces its inputs, so saving is an upsert; the month also has a UUID, which appears in the body. The month
  stores the payment mode and the two pay days (1 to 28) it was saved with.
- A **fixed item** (name, amount greater than zero, `income` or `expense`, `part` of `first` or `second`) belongs to a
  month and is returned inline with it. Its `part` is stored as entered and ignored while the month has one payment, so
  switching the payment mode loses nothing; a new item defaults to `first`. It may also carry a `day` of the month
  (1 to 31, `null` for none), stored as entered; `part` is not derived from it.
- With two payments the **parts** follow the pay cycle of the Settings pay days (the first part starts on the first pay
  day, the second on the second pay day of the next month; [ADR 0023](0023-pay-cycle-parts.md), which replaced the
  calendar halves of days 1 to 15 and 16 to the end). The Overview and the month report carry both parts.
- The response carries the inputs used, the payment mode, the calculation version, the five results and the payments, the
  fixed-item totals per part, and the **balance after fixed items** per part (a `SignedAmount`, ADR 0008). The history list
  includes each month's results, so the app makes one call, not one per month.
- **The balance rule** (confirmed with the client's developer):
  - For a part, the balance is that part's payment plus its fixed income minus its fixed expenses.
  - With one payment there is one part, its payment is `total_after_tax`, and every fixed item counts whatever its stored
    `part`.
  - With two payments the response also carries a whole-month balance: `total_after_tax` plus all fixed income minus all
    fixed expenses. With one payment it equals the single part's balance.
  - Fixed items use the currency of the month; a request that carries another one is a `400 currency_mismatch`.

  Example with the figures above, in minor units. Fixed items: income 10000 (part first), expense 30000 (part first),
  expense 5000 (part second).

  | Result | Value |
  | --- | --- |
  | two payments, first part | 127500 + 10000 − 30000 = 107500 |
  | two payments, second part | 158583 − 5000 = 153583 |
  | two payments, whole month | 286083 + 10000 − 35000 = 261083 |
  | one payment | 286083 + 10000 − 35000 = 261083 |

## Consequences

- Shared test cases: the worked example above becomes a unit test of the function, and the `salary` feature adds a file of
  cases that the client can run to prove its preview agrees.
- The salary endpoints document the input and result fields; amounts follow the wire format of ADR 0008.
- A change of the formulas is a new `calculation_version`, announced like any contract change (`docs/api-workflow.md`).
