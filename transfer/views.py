from typing import Any, ClassVar

from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.negotiation import DefaultContentNegotiation
from rest_framework.renderers import BaseRenderer
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from config.api_errors import (
    IDEMPOTENT_REPLAYED_HEADER,
    RETRY_AFTER_HEADER,
    SERVICE_UNAVAILABLE_RESPONSE,
    UNPROCESSABLE_RESPONSE,
)
from config.throttling import ExportThrottle, ImportThrottle
from idempotency import services as idempotency
from users import emails, events
from users import services as user_services
from users.models import SecurityEventType, Session, User

from . import export, importing
from .schema_shapes import IMPORT_REQUEST
from .serializers import (
    ExportRequestSerializer,
    ImportRequestSerializer,
    ImportResultSerializer,
)

CONTENT_DISPOSITION_HEADER = OpenApiParameter(
    name="Content-Disposition",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.HEADER,
    response=[200],
    description=(
        '`attachment; filename="money-scribe-transactions.csv"` for one kind, `money-scribe-export.zip` for several: '
        "a fixed name with no personal data and no date."
    ),
)


class AnyAcceptNegotiation(DefaultContentNegotiation):  # type: ignore[misc]
    """The export answers a file whatever the client's ``Accept`` says (it may well ask for ``text/csv``)."""

    def select_renderer(
        self,
        request: Request,
        renderers: list[BaseRenderer],
        format_suffix: str | None = None,
    ) -> tuple[BaseRenderer, str]:
        return renderers[0], renderers[0].media_type


class ExportView(APIView):  # type: ignore[misc]
    throttle_classes: ClassVar[list[Any]] = [ExportThrottle]
    content_negotiation_class = AnyAcceptNegotiation

    @extend_schema(
        operation_id="export_create",
        tags=["exports"],
        summary="Export the data as CSV",
        description=(
            "Needs the account password (it is `POST` so the password is never in a URL). `kinds` is 1 to 5 different "
            "values of `accounts`, `categories`, `transactions`, `debts`, `salary_months`; `from` and `to` are optional "
            "inclusive days (both left out is all time; they do not apply to accounts and categories, which have no "
            "date). One kind is answered as `text/csv; charset=utf-8`, several as `application/zip` with "
            "`accounts.csv`, `categories.csv`, `transactions.csv`, `debts.csv` and `salary_months.csv`, always in this "
            "order. The categories are the person's custom ones. The file name is fixed and has no personal data "
            'and no date. Each file is UTF-8 with a byte order mark, a fixed header row, `,`, `\\"` quoting, CRLF line '
            "ends, oldest first; dates are `YYYY-MM-DD`, months `YYYY-MM`, amounts decimal strings of the currency's "
            "digits, an empty cell means none. A text cell that a spreadsheet could read as a formula gets one `'` in "
            "front; the figures and the JSON cells are never changed. Nothing in the period is a file with the header "
            "row only. The export is rate limited (5 an hour), a `data_exported` security event is written and the "
            "owner gets an email that an export was made. Errors: `400` `invalid_choice`, `min_length` or `max_length` "
            "on `kinds`, `invalid_date_range` on `to`, `date_out_of_range`, `required` or `invalid_password` on "
            "`current_password`; `429` `password_attempts_exceeded` (shared with the password change) or `throttled`; "
            "`503` `service_unavailable` while the limit store is down."
        ),
        parameters=[CONTENT_DISPOSITION_HEADER, RETRY_AFTER_HEADER],
        request=ExportRequestSerializer,
        responses={
            (200, "text/csv"): OpenApiTypes.BINARY,
            (200, "application/zip"): OpenApiTypes.BINARY,
            503: SERVICE_UNAVAILABLE_RESPONSE,
        },
    )
    def post(self, request: Request) -> HttpResponse:
        serializer = ExportRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        user: User = request.user
        session: Session = request.auth
        user_services.confirm_password(
            user, data["current_password"], "current_password"
        )
        kinds: list[str] = data["kinds"]
        period = export.Period(data.get("from"), data.get("to"))
        now = timezone.now()
        with transaction.atomic():
            files = export.export_files(user, kinds, period)
            events.record_data_event(
                user,
                SecurityEventType.DATA_EXPORTED,
                session,
                kinds=kinds,
                count=sum(file.rows for file in files),
                now=now,
            )
        emails.send_data_exported_notice(
            email=user.email,
            language=user.language,
            device_name=session.device_name,
            when=emails.local_time(user.time_zone, now),
        )
        if len(files) == 1:
            body, content_type = files[0].content, "text/csv; charset=utf-8"
            name = export.CSV_NAMES[files[0].kind]
        else:
            body, content_type = export.zip_files(files), "application/zip"
            name = export.ZIP_NAME
        response = HttpResponse(body, content_type=content_type)
        response["Content-Disposition"] = f'attachment; filename="{name}"'
        response["Cache-Control"] = "no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response


class ImportView(APIView):  # type: ignore[misc]
    throttle_classes: ClassVar[list[Any]] = [ImportThrottle]

    @extend_schema(
        operation_id="import_create",
        tags=["imports"],
        summary="Import rows of one dataset",
        description=(
            "The app reads the CSV and sends rows as JSON; the server never parses an uploaded file. `dataset` is "
            "`transactions`, `debts` or `salary_months`, `rows` is 1 to 500 rows, each with its line number `row` (a "
            "positive whole number, unique in the call) and the fields of the dataset named like the CSV columns. Every "
            "row goes through the rules of a single create (a `PUT` for a salary month) and gets the same codes. The "
            "call is one database transaction: a row that passes is written, a row that fails is skipped and reported "
            "in `problems` (the first problem of the row), and the answer is always `200`. An import never creates an "
            "account or a category (`unknown_category`, `unknown_account`), never reads an id, and never overwrites: a "
            "row equal to an existing record is a `duplicate`, counted by multiplicity, and a salary month that exists "
            "is a `duplicate`. The record caps apply (`transaction_limit_reached`, `debt_limit_reached`, "
            "`repayment_limit_reached`, `fixed_item_limit_reached`). A real import needs an `Idempotency-Key`; with "
            "`dry_run` `true` nothing is written or stored and no key is needed. A call that writes at least one row "
            "records a `data_imported` security event. Limits: 500 rows and 1 MB a call, 120 calls an hour; `503` "
            "`service_unavailable` while the limit store is down."
        ),
        parameters=[
            OpenApiParameter(
                name="Idempotency-Key",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.HEADER,
                required=False,
                description=(
                    "Required unless `dry_run` is `true`: a random id, once per user action, as a quoted string of 16 to "
                    "64 letters, digits, hyphens or underscores. The same key with the same body returns the first "
                    "answer with `Idempotent-Replayed: true`; another body under the same key is `422`."
                ),
            ),
            IDEMPOTENT_REPLAYED_HEADER,
            RETRY_AFTER_HEADER,
        ],
        request=IMPORT_REQUEST,
        responses={
            200: ImportResultSerializer,
            422: UNPROCESSABLE_RESPONSE,
            503: SERVICE_UNAVAILABLE_RESPONSE,
        },
    )
    def post(self, request: Request) -> Response:
        serializer = ImportRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        user: User = request.user
        session: Session = request.auth
        dataset: str = data["dataset"]
        dry_run: bool = data["dry_run"]

        def answer(result: importing.ImportResult) -> Response:
            return Response(
                ImportResultSerializer(
                    {
                        "dataset": result.dataset,
                        "dry_run": dry_run,
                        "imported": result.imported,
                        "skipped": result.skipped,
                        "problems": [
                            {"row": p.row, "code": p.code, "attr": p.attr}
                            for p in result.problems
                        ],
                    }
                ).data
            )

        if dry_run:
            with transaction.atomic():
                result = importing.run_import(user, dataset, data["rows"])
                transaction.set_rollback(True)  # the estimate leaves nothing behind
            return answer(result)

        def action() -> Response:
            result = importing.run_import(user, dataset, data["rows"])
            if result.imported:
                events.record_data_event(
                    user,
                    SecurityEventType.DATA_IMPORTED,
                    session,
                    kinds=[dataset],
                    count=result.imported,
                )
            return answer(result)

        done: Response = idempotency.run(request, action)
        return done
