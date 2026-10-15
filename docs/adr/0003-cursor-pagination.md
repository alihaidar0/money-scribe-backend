# ADR 0003: Cursor pagination for every list

- Status: accepted
- Date: 2026-10-15

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
| repayments, debts | `-id` unless the screen needs a date order, then `-date`, `-id` |
| sessions | `-last_active_at`, `-id` |

**Filters.** A list takes its filters as explicit query parameters, documented in the schema, validated with `400` on a
bad value, and implemented with `django-filter` (decided at implementation, with its drf-spectacular support).
Transactions take `date_from` and `date_to` (inclusive calendar dates), `account`, `category` and `kind`; the client
filters by a month or by half of a month with the two dates.

**Day totals are not computed from pages.** A day can be split across two pages, so a client summing the pages shows a
wrong total until the next page arrives. The server provides a read model instead: `GET /api/v1/reports/day-totals/`
takes the same filters as the list (`date_from`, `date_to`, `account`, `category`, `kind`), requires `date_from` and
`date_to` (at most 366 days apart), and returns one entry per day **that has entries** and per currency, newest day first
like the list, with one signed net total (income minus expense). Income and expense totals can be added later without
breaking a client. It is a report, so it is not paginated.

Not paginated: read models, settings and single objects. A salary month returns its fixed items inline: they are bounded
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
- Implemented in `feat/api-foundation` (the class and its tests) and used by each list endpoint; day totals arrive with
  the `reports` app.
