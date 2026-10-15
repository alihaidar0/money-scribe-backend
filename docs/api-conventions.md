# API conventions (defaults)

Short defaults for a project's HTTP API. They keep the contract (`openapi/schema.yaml`)
consistent from the first endpoint; a project may change any of them, but should decide once and
apply it everywhere. Rules that depend on the project (money and currency formats, identifiers,
authentication, domain naming) are **not** here: each project adds its own to this file.

## Versioning

Version in the path: `/api/v1/...`. A breaking change gets a new version (`/api/v2/...`) next to
the old one (see `docs/api-workflow.md`). The `SCHEMA_PATH_PREFIX` setting already keeps the
version out of operation ids and tags.

## One error body

Every error raised inside a DRF view has the same body, so a client has a single error parser. The
template ships it with [drf-standardized-errors](https://github.com/ghazi-git/drf-standardized-errors)
(`EXCEPTION_HANDLER` and the schema class are already set by `scripts/init-django.sh`):

```json
{
  "type": "validation_error",
  "errors": [
    { "code": "required", "detail": "This field is required.", "attr": "price" }
  ]
}
```

- `type` is `validation_error` (invalid input), `client_error` (any other 4xx, including an unreadable body)
  or `server_error`.
- `errors` is a list. `code` is stable and meant for programs (`required`, `blank`, `not_found`,
  `permission_denied`, `throttled`, …); `detail` is human text; `attr` is the field the error belongs to
  (dotted for nested data) or `null` when it is about the request as a whole.
- This is **not** RFC 9457 problem details. The format is the package's own; what the contract
  gives a client is that it is documented and typed.

What the contract documents: the 400, 401, 403, 404 and 429 responses of each operation that can return
them. The 400 body is one shared schema (`Error400Response`, from `config/api_errors.py`), because
the package's default, one schema per operation and field, grows a generated client by dozens of classes per
endpoint. Add a status code to `DRF_STANDARDIZED_ERRORS["ALLOWED_ERROR_STATUS_CODES"]` (and its enum to
`ENUM_NAME_OVERRIDES`) when a project wants it documented too.

Limits to know:

- Only errors raised inside DRF views are covered. Django's own responses (a disallowed host, a URL outside
  the API, a middleware rejection) keep their normal HTML or plain-text body; a client treats a non-JSON
  error as a generic failure.
- An unhandled exception returns a generic message, never the exception text.
- Errors raised straight from a view (`raise ValidationError(...)` with a custom code) keep that code at runtime;
  the shared 400 schema describes `code` as a string, so nothing needs registering.

## Pagination

Every list endpoint is paginated: the same class everywhere (`DEFAULT_PAGINATION_CLASS` and
`PAGE_SIZE` in `REST_FRAMEWORK`), the same response envelope, and a documented maximum page size.
Prefer cursor pagination for large or fast-changing lists, page-number pagination for small, stable ones.

## Timestamps

UTC, ISO 8601, with an explicit offset: `2026-01-31T14:05:09Z`. Keep `USE_TZ = True`; a client
converts to the user's time zone for display only. Dates without a time are `YYYY-MM-DD`.

## Also

- Field and query-parameter names are `snake_case`.
- Use HTTP status codes for the outcome (`201` created, `204` no content, `400` invalid, `401`/`403`
  not authenticated/allowed, `404` not found, `409` conflict) and keep them in the schema.
- An empty list is `200` with an empty `results`, never `404`.
