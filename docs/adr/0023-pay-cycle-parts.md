# ADR 0023: Pay-cycle parts: the Overview and the reports cut a month by the pay days

- Status: accepted
- Date: 2026-10-28

## Context

A month paid twice was cut into calendar halves (days 1 to 15 and 16 to the end). That does not match how the money
arrives: the first payment is received on the first pay day of the month and the second on the second pay day of the next
month, and what is spent between two pay days comes out of the payment received at the start of that stretch. The Flutter
developer asked for the cut to follow the pay days, and for an income (a scholarship, a gift) to join a payment.

## Decision

- **Cycle.** With the Settings pay days `first_pay_day` F (20 until set) and `second_pay_day` S (5 until set), the first
  part of month M runs from F of M to the day before S of M+1, and the second part from S of M+1 to the day before F of
  M+1. The month (`total`) is both together. A month paid once is the calendar month. Fixed items keep their own `part`.
- **Where.** The Overview and the month report, for expenses and repayments by their own `date`. The day totals stay
  per calendar day; the app asks for the range of a part.
- **Income.** An income may carry `part` and `part_month` (`YYYY-MM`, like every month key of the API), both set or both
  `null`, on an income only. Without them it joins the part whose pay day is nearest to its `date` by calendar distance in
  either direction, a tie going to the later pay day; the second pay day of a month pays the second part of the month
  before. Both fields are returned for every income (chosen or computed), `null` for an expense. The Overview and the report
  count the income in that part wherever its date is. A month paid once ignores both and counts income by date.
- **Computed, not stored.** The cut and the nearest part are worked out on every read from the current Settings, so
  changing the pay days re-cuts the history. Only an explicit choice is stored.
- **Pay day order.** The cycle needs S < F. Settings refuses any other pair with `400 invalid_pay_days` on
  `second_pay_day`, checked on the pair as it would be stored (a day never set counts as its default), so a change of one
  day alone can be refused. A pair stored before this rule and out of order falls back to the defaults in the reports, so a
  read never fails. The server does not fall back to calendar halves: a silent second behaviour would be hard to explain.
- **Mixed modes.** Each month is cut under its own payment mode. When a month paid twice is followed by one paid once their
  days overlap, and a record in the overlap counts in both, as it does for an expense today.
- **Errors.** `invalid_part` (the pair, or a part on an expense) and `invalid_pay_days`, in ADR 0004.

## Consequences

- The shared vectors of the Overview and the month report changed; the app runs the same files.
- The CSV export and import of transactions do not carry `part` and `part_month` yet; an imported income joins the
  nearest pay day.
- Reading a transaction reads the Settings once when an income has no chosen part.
