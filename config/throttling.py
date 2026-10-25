"""Per-user rate limits by route group (ADR 0021).

Every signed-in call belongs to exactly one group and is counted only there: reads, writes, reports, lists with a
``search`` parameter, the CSV export, the CSV import and the session actions. The rates are in
``REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]``. The counter is the atomic fixed window of ``users.limits`` (the same as
the limits of the authentication calls), keyed by the user, so all the devices of one person share it. A call over its
limit is ``429 throttled`` with ``Retry-After``.

``DefaultUserThrottle`` is the project's default: a view that needs no other group is a read or a write by its method,
or a search when a list is asked with ``search``. A view in another group names it as ``throttle_class`` (for
example ``throttle_classes = [ReportsThrottle]``) or with a ``throttle_group`` attribute. A view that has its own
rate limit (the authentication calls) lists its own classes and is outside the groups.

If the limit store is down, a limit that fails open lets the request through, because a cache outage must not take
the app down; the export, the import and "end all other sessions" fail closed, as ``503 service_unavailable``.
A request without a signed-in user is not counted here: the public calls have their own limits by client address.
"""

import logging
from typing import TYPE_CHECKING, ClassVar, Final

from redis.exceptions import RedisError
from rest_framework.permissions import SAFE_METHODS
from rest_framework.request import Request
from rest_framework.settings import api_settings
from rest_framework.throttling import BaseThrottle

from .exceptions import service_unavailable

if TYPE_CHECKING:
    # Only for the type hints: DRF reads the default throttle classes while it defines APIView, so a runtime
    # import of it here would be circular.
    from rest_framework.views import APIView

logger = logging.getLogger(__name__)

_PERIODS: Final = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_rate(rate: str) -> tuple[int, int]:
    """``"300/min"`` is 300 requests in 60 seconds."""
    count, _, period = rate.partition("/")
    return int(count), _PERIODS[period[:1]]


class UserGroupThrottle(BaseThrottle):  # type: ignore[misc]
    """A limit per signed-in user for one route group. Subclasses name the ``scope`` (or override :meth:`group`)."""

    scope: ClassVar[str | None] = None
    fail_closed: ClassVar[bool] = False

    def __init__(self) -> None:
        self._wait = 1

    def group(self, request: Request, view: APIView) -> str | None:
        return self.scope

    def allow_request(self, request: Request, view: APIView) -> bool:
        user = request.user
        if user is None or not user.is_authenticated:
            return True
        group = self.group(request, view)
        if group is None:
            return True
        limit, window = parse_rate(api_settings.DEFAULT_THROTTLE_RATES[group])
        # Imported here: the users app imports config, so config cannot import it when the settings load.
        from users import limits

        key = f"throttle:{group}:{user.pk}"
        try:
            count, self._wait = limits.count_hit(key, window)
        except RedisError:
            if self.fail_closed:
                logger.error("The limit store is unavailable; the request is refused.")
                raise service_unavailable() from None
            logger.warning(
                "The limit store is unavailable; the %s limit is not applied.", group
            )
            return True
        return count <= limit

    def wait(self) -> float:
        return float(self._wait)


class DefaultUserThrottle(UserGroupThrottle):
    """The default group of a view: ``throttle_group`` when the view names one, else by the method."""

    def group(self, request: Request, view: APIView) -> str | None:
        explicit: str | None = getattr(view, "throttle_group", None)
        if explicit:
            return explicit
        if request.method in SAFE_METHODS:
            return "search" if request.query_params.get("search") else "reads"
        return "writes"


class ReportsThrottle(UserGroupThrottle):
    """The reports, the Overview and the day totals."""

    scope = "reports"


class ExportThrottle(UserGroupThrottle):
    """The CSV export: a full copy of the data, so it fails closed."""

    scope = "export"
    fail_closed = True


class ImportThrottle(UserGroupThrottle):
    """The CSV import, dry runs included; it fails closed."""

    scope = "import"
    fail_closed = True


class SessionActionThrottle(UserGroupThrottle):
    """Ending one session. It keeps working when the limit store is down, because it only reduces access."""

    scope = "session_actions"


class SessionBulkActionThrottle(UserGroupThrottle):
    """Ending every other session: it fails closed."""

    scope = "session_actions"
    fail_closed = True
