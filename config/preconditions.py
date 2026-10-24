"""Conditional requests (RFC 9110) for the safe writes of ADR 0016.

The ``ETag`` of a record is its ``version`` as a strong tag, ``"3"``. An edit or a delete may carry
``If-Match`` with the version it was made from; a create under a key the client chose may carry
``If-None-Match: *`` to mean "only if nothing is there". A condition that does not hold is
``412 precondition_failed`` and nothing changes. Without the header the last write wins.
"""

import re
from typing import Any, Final

from rest_framework.request import Request
from rest_framework.response import Response

from .exceptions import precondition_failed

_STRONG_TAG: Final = re.compile(r'"(0|[1-9][0-9]{0,9})"')


def etag_of(version: int) -> str:
    return f'"{version}"'


def with_etag(response: Response) -> Response:
    """Put the ``ETag`` on an answer that carries one record with a ``version``."""
    data: Any = getattr(response, "data", None)
    if isinstance(data, dict) and type(data.get("version")) is int:
        response["ETag"] = etag_of(data["version"])
    return response


def check_if_match(request: Request, current_version: int) -> None:
    """Refuse an edit or a delete made from another version than the stored one.

    ``If-Match: *`` matches any record that exists. A weak tag (``W/"3"``) or anything unreadable never
    matches, which is what RFC 9110 says of a tag it cannot compare strongly.
    """
    header = request.headers.get("If-Match")
    if header is None:
        return
    candidates = [part.strip() for part in header.split(",")]
    if "*" in candidates:
        return
    for candidate in candidates:
        match = _STRONG_TAG.fullmatch(candidate)
        if match is not None and int(match.group(1)) == current_version:
            return
    raise precondition_failed()


def check_create_only(request: Request, exists: bool) -> None:
    """``If-None-Match: *`` on a create under a client-chosen key: refuse when the record is already there."""
    header = request.headers.get("If-None-Match")
    if header is not None and exists and "*" in [p.strip() for p in header.split(",")]:
        raise precondition_failed()


def check_if_match_on_create(request: Request) -> None:
    """``If-Match`` on a record that does not exist yet can never match."""
    if request.headers.get("If-Match") is not None:
        raise precondition_failed()
