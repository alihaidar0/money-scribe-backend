# ADR 0003: Cursor pagination for every list

- Status: accepted
- Date: 2026-10-15
- Amended: 2026-10-25, with the Flutter developer. The day totals are paginated, one item per day with its currencies
  inside, and the filters are validated by a serializer. The text below is the amended decision.

## Context

Transactions and repayments grow without bound, and the app scrolls through them while the user adds and deletes rows.
Page-number and offset pagination drift when rows change between two requests (an item is skipped or shown twice), and
they need a count query on every page. The API conventions already require one pagination class, one response
envelope and a documented maximum page size.

The client groups transactions by day and shows a total per day, filters them by month, by half of the month and by
account, and follows `next` until it is `null` for small lists. It needs a stable order with no repeated or missing rows
between pages.

## Decision

One keyset cursor class, `ApiCursorPagination` in `config/pagination.py`, is the project default
(`DEFAULT_PAGINATION_CLASS`) for every list endpoint, small lists included, so a client has a single list parser.

| Item | Value |
| --- | --- |
| Query parameters | `limit` (default 50, maximum 100) and `cursor` (opaque; taken from `next` or `previous`, never built by the client) |
| Envelope | `next`, `previous` and `results`; `next` and `previous` are full URLs or `null` |
| Total count | none: it costs a query per page and the app does not need it |
| Empty list | `200` with `"results": []` |

```json
{
  "next": "https://api.example.com/api/v1/transactions/?cursor=cD0yMDI2LTEwLTE0",
  "previous": null,
  "results": []
}
```

**Ordering.** Each list declares an ordering of one or more fields whose **last field is unique**; `id` is, because ids are
UUIDv7 and sort in creation order (ADR 0005). The cursor holds the values of all those fields, and the next page is
"everything after that position", compared field by field. There is no offset, so a row is neither repeated nor skipped
by paging. DRF's own cursor class positions on the first field only and breaks ties with an offset, which is why this
class is ours; it is small and is tested with ties, page boundaries and rows inserted between requests. Transactions name
`created_at` explicitly instead of relying on the id: the order within a day is a screen requirement, so it should not
depend on how ids are generated. The index is `(owner, date, created_at, id)`.

| List | Ordering |
| --- | --- |
| transactions | `-date`, `-created_at`, `-id` (newest day first, then the entry added last first within a day) |
| debts | `-created_at`, `-id` (newest first); a debt carries its repayments inline, by `date`, then `id`, so repayments have no list of their own |
| sessions | `-last_active_at`, `-id` |

**Filters.** A list takes its filters as explicit query parameters, documented in the schema and validated with `400` on a
bad value, on the parameter itself and with a stable code. One serializer per list describes and checks them
(`ledger/filters.py`), so the schema and the validation cannot differ, a repeated parameter (`category_id`) and a rule
across two (`date_from` after `date_to` is `invalid_date_range`) are handled in one place, and no dependency is added;
`django-filter`, first planned here, was not needed. Transactions take `date_from` and `date_to` (inclusive calendar
dates), `category_id` and `account_id` (repeatable, at most 20 values), `kind` and `search` (a case-insensitive substring
of the note, 2 to 100 characters, on a trigram index); the client filters by a month or by half of a month with the two
dates. Debts take `direction`, `status` (repeatable) and `search` (a case-insensitive substring of the person, 2 to 50
characters) and a `today` that replaces the person's day when the status is worked out (`debts/filters.py`). A parameter
the list does not know is ignored.

**Day totals are not computed from pages, and they are paginated by day.** A day can be split across two pages of the list,
so a client summing the pages shows a wrong total until the next page arrives. The server provides a read model instead:
`GET /api/v1/reports/day-totals/` takes the same filters as the list except `search` (a total of only the matching rows would
mislead), with `date_from` and `date_to` optional and no limit on the span. Its page is `limit` **days** (50 by default,
100 at most), the days that have a record, newest first. Each item is one day with its currencies inside,
`{date, totals: [{currency, total}]}`, so a day always arrives complete and a page never holds more than `limit` items; `total`
is one signed amount (income minus expenses of that day and currency). The cursor is the last day of the page (the one
ordering field, unique per item), in the same envelope as every other list. The order is the list's, so the first page of
totals starts on the day of the first page of the list and the two are read in step, however sparse the data or oddly
dated one record is. The first design, a window of at most 366 days without paging, would have cost about seventy calls
for one record dated 2099. A total past the largest amount is `400 invalid_amount`. Income and expense totals can be added
later without breaking a client.

Not paginated: the other read models, settings and single objects. A salary month returns its fixed items inline: they are bounded
and are not a list of their own.

## Alternatives considered

- **Page number or limit and offset:** simple, but rows drift between requests and every page needs a count.
- **DRF's `CursorPagination` as it is:** positions on the first ordering field only, so many transactions on one day
  rely on an offset and can repeat or go missing when rows are inserted.

## Consequences

- A client follows `next` and `previous`; it cannot jump to page N and gets no total. A figure the app needs is served
  by a report endpoint.
- The OpenAPI schema documents `limit`, `cursor` and the envelope once, as a generic paginated response, plus the
  filters of each list.
- Implemented in `config/pagination.py` with the first list, the salary history (step 10.4), and used by each list
  endpoint after it. The day totals, with their own pagination by day (`reports/pagination.py`), arrive with the
  transactions (step 10.6).
