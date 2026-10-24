from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from config.api_errors import ETAG_HEADER, IF_MATCH_HEADER, PRECONDITION_FAILED_RESPONSE
from config.preconditions import check_if_match, with_etag

from . import services
from .serializers import SettingsSerializer, SettingsUpdateSerializer

TAG = ["settings"]


class SettingsView(APIView):  # type: ignore[misc]
    """The signed-in user's own settings: there is no id in the path, so no other user's can be asked for."""

    @extend_schema(
        operation_id="settings_read",
        tags=TAG,
        summary="Read the settings",
        description=(
            "Every setting, with the app's own default for one that was never set (language `en`, time zone `UTC`, "
            "currency `RUB`, one payment, pay days 20 and 5, reminders off). The first read makes the row, so there "
            "is always a `version` and an `ETag`."
        ),
        parameters=[ETAG_HEADER],
        responses={200: SettingsSerializer},
    )
    def get(self, request: Request) -> Response:
        prefs = services.read(request.user)
        return with_etag(
            Response(SettingsSerializer(services.effective(request.user, prefs)).data)
        )

    @extend_schema(
        operation_id="settings_update",
        tags=TAG,
        summary="Change the settings",
        description=(
            "Changes the fields sent and answers all the settings. The language and the time zone are the ones the "
            'emails, the reminders and "today" use (sign-in still sets them from the device it happens on). '
            "`default_currency`, `payment_mode`, `first_pay_day` and `second_pay_day` can be sent as `null` to go back "
            'to "not set". Send `If-Match` with the `version` read: a stale one is `412` and changes nothing. '
            "Errors: `400` on the field that is wrong (`invalid_choice`, `invalid`, `min_value`, `max_value`, `null` "
            "for a field that cannot be cleared), `400` `read_only` for a field that is not a setting."
        ),
        parameters=[IF_MATCH_HEADER, ETAG_HEADER],
        request=SettingsUpdateSerializer,
        responses={200: SettingsSerializer, 412: PRECONDITION_FAILED_RESPONSE},
    )
    def patch(self, request: Request) -> Response:
        serializer = SettingsUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            prefs = services.lock_or_create(request.user)
            check_if_match(request, prefs.version)
            prefs = services.apply(request.user, prefs, dict(serializer.validated_data))
            body = SettingsSerializer(services.effective(request.user, prefs)).data
        return with_etag(Response(body))
