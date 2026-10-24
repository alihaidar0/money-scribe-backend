"""One keyset cursor class for every list (ADR 0003).

A list declares its ordering on the view as ``cursor_ordering``, for example ``("-date", "-created_at", "-id")``.
The **last field must be unique** (the id is), so the order is total. The cursor holds the values of all the ordering
fields of one row, and a page is "everything after (or before) that row", compared field by field, so a row is never
repeated or skipped by paging even when rows are added or deleted between two requests. There is no offset and no
count. The ordering fields must be real, non-null columns of the model.

The client follows ``next`` and ``previous``; a cursor is opaque and is never built by the client. A cursor that is not
one this class made is ``400`` on ``cursor``; a ``limit`` that is not a whole number of at least 1 is ``400`` on
``limit``, and one above the maximum is lowered to it.
"""

import base64
import binascii
import json
from collections.abc import Sequence
from typing import Any, Final, cast

from django.core.exceptions import (
    ImproperlyConfigured,
)
from django.core.exceptions import (
    ValidationError as DjangoValidationError,
)
from django.db.models import Field, Model, Q, QuerySet
from rest_framework.exceptions import ErrorDetail, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

DEFAULT_LIMIT: Final = 50
MAX_LIMIT: Final = 100
LIMIT_PARAM: Final = "limit"
CURSOR_PARAM: Final = "cursor"
_AFTER: Final = "n"  # the next page: rows after the position
_BEFORE: Final = "p"  # the previous page: rows before the position


def _invalid(field: str, detail: str) -> ValidationError:
    return ValidationError({field: [ErrorDetail(detail, code="invalid")]})


class ApiCursorPagination:
    """The project's ``DEFAULT_PAGINATION_CLASS``."""

    default_limit = DEFAULT_LIMIT
    max_limit = MAX_LIMIT

    def __init__(self) -> None:
        self._next: str | None = None
        self._previous: str | None = None

    # --- reading the request -------------------------------------------------------------------

    def _limit(self, request: Request) -> int:
        raw = request.query_params.get(LIMIT_PARAM)
        if raw is None:
            return self.default_limit
        if not raw.isascii() or not raw.isdigit() or int(raw) < 1:
            raise _invalid(LIMIT_PARAM, "A whole number of at least 1.")
        return min(int(raw), self.max_limit)

    @staticmethod
    def _ordering(view: Any) -> list[tuple[str, bool]]:
        ordering = getattr(view, "cursor_ordering", None)
        if not ordering:
            raise ImproperlyConfigured(
                f"{type(view).__name__} needs cursor_ordering, ending in a unique field."
            )
        return [(name.lstrip("-"), name.startswith("-")) for name in ordering]

    def _decode(
        self, request: Request, fields: Sequence[tuple[str, bool]], model: type[Model]
    ) -> tuple[str, list[Any]] | None:
        raw = request.query_params.get(CURSOR_PARAM)
        if raw is None:
            return None
        try:
            payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
            direction, texts = payload["d"], payload["v"]
            if direction not in (_AFTER, _BEFORE) or len(texts) != len(fields):
                raise ValueError("wrong shape")
            values = [
                cast("Field[Any, Any]", model._meta.get_field(name)).to_python(text)
                for (name, _), text in zip(fields, texts, strict=True)
            ]
        except (
            ValueError,
            KeyError,
            TypeError,
            binascii.Error,
            DjangoValidationError,
            UnicodeDecodeError,
        ):
            raise _invalid(CURSOR_PARAM, "The cursor is not valid.") from None
        return direction, values

    # --- building the page ---------------------------------------------------------------------

    @staticmethod
    def _beyond(
        fields: Sequence[tuple[str, bool]], values: Sequence[Any], after: bool
    ) -> Q:
        """Rows after (or before) the position, field by field: ``a > x OR (a = x AND b > y) OR ...``."""
        beyond = Q()
        for index, (name, descending) in enumerate(fields):
            equal = Q(**{fields[j][0]: values[j] for j in range(index)})
            lookup = "lt" if descending == after else "gt"
            beyond |= equal & Q(**{f"{name}__{lookup}": values[index]})
        return beyond

    @staticmethod
    def _token(row: Any, fields: Sequence[tuple[str, bool]], direction: str) -> str:
        payload = {"d": direction, "v": [str(getattr(row, name)) for name, _ in fields]}
        raw = json.dumps(payload, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    @staticmethod
    def _link(request: Request, token: str) -> str:
        query = request.query_params.copy()
        query[CURSOR_PARAM] = token
        url: str = request.build_absolute_uri(request.path)
        return f"{url}?{query.urlencode()}"

    def paginate_queryset(
        self, queryset: QuerySet[Any], request: Request, view: Any = None
    ) -> list[Any]:
        fields = self._ordering(view)
        limit = self._limit(request)
        cursor = self._decode(request, fields, queryset.model)
        ordering = [("-" if desc else "") + name for name, desc in fields]
        flipped = [("" if desc else "-") + name for name, desc in fields]
        ordered = queryset.order_by(*ordering)

        if cursor is not None and cursor[0] == _BEFORE:
            rows = list(
                queryset.order_by(*flipped).filter(
                    self._beyond(fields, cursor[1], after=False)
                )[: limit + 1]
            )
            page = list(reversed(rows[:limit]))
            has_next = True  # the page we came from is after this one
            has_previous = len(rows) > limit
        else:
            window = ordered
            if cursor is not None:
                window = ordered.filter(self._beyond(fields, cursor[1], after=True))
            rows = list(window[: limit + 1])
            page = rows[:limit]
            has_next = len(rows) > limit
            # Coming from a cursor, rows before this page exist unless they were all deleted meanwhile.
            has_previous = (
                cursor is not None
                and bool(page)
                and ordered.filter(
                    self._beyond(
                        fields, [getattr(page[0], n) for n, _ in fields], after=False
                    )
                ).exists()
            )

        self._next = self._previous = None
        if page and has_next:
            self._next = self._link(request, self._token(page[-1], fields, _AFTER))
        if page and has_previous:
            self._previous = self._link(request, self._token(page[0], fields, _BEFORE))
        return page

    def get_paginated_response(self, data: Any) -> Response:
        return Response(
            {"next": self._next, "previous": self._previous, "results": data}
        )

    # --- the OpenAPI document ------------------------------------------------------------------

    def get_paginated_response_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        link = {"type": "string", "format": "uri", "nullable": True}
        return {
            "type": "object",
            "required": ["next", "previous", "results"],
            "properties": {
                "next": {
                    **link,
                    "description": "The URL of the next page, or `null` on the last one. Follow it as it is.",
                },
                "previous": {
                    **link,
                    "description": "The URL of the previous page, or `null` on the first one.",
                },
                "results": schema,
            },
        }

    def get_schema_operation_parameters(self, view: Any) -> list[dict[str, Any]]:
        return [
            {
                "name": LIMIT_PARAM,
                "required": False,
                "in": "query",
                "description": f"How many results a page holds: {self.default_limit} by default, at most {self.max_limit}.",
                "schema": {"type": "integer", "minimum": 1, "maximum": self.max_limit},
            },
            {
                "name": CURSOR_PARAM,
                "required": False,
                "in": "query",
                "description": "An opaque position taken from `next` or `previous`; never built by the client.",
                "schema": {"type": "string"},
            },
        ]
