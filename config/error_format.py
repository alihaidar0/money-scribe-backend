from typing import Any

from django.conf import settings
from drf_standardized_errors.formatter import ExceptionFormatter

from .error_codes import ErrorCode
from .exceptions import CodeInvalid, ExceedsRemaining, FieldAuthenticationFailed

# The limit behind a password error, sent with it so the client can word "at least {n} characters"
# from the server's own value and a change of policy needs no app release: code -> (member, setting).
LIMIT_MEMBERS = {
    ErrorCode.PASSWORD_TOO_SHORT.value: ("min_length", "PASSWORD_MIN_LENGTH"),
    ErrorCode.PASSWORD_TOO_LONG.value: ("max_length", "PASSWORD_MAX_LENGTH"),
}


class ApiExceptionFormatter(ExceptionFormatter):
    """The package's ``{type, errors[]}`` body, plus the optional members of a few errors.

    ``attr`` on the 401 of a sign-in that names its field, ``attempts_left`` on ``code_invalid``, ``remaining`` on ``exceeds_remaining``, and ``min_length`` or ``max_length`` on the password length
    errors. It is a formatter, not a replacement handler, because the schema generator documents the
    error responses only for views that use the package's own handler (``config.api_errors`` has the
    schema).
    """

    def format_error_response(self, error_response: Any) -> Any:
        body = super().format_error_response(error_response)
        for item in body["errors"]:
            if isinstance(self.exc, FieldAuthenticationFailed):
                item["attr"] = self.exc.attr
            elif item["code"] in LIMIT_MEMBERS:
                member, setting = LIMIT_MEMBERS[item["code"]]
                item[member] = getattr(settings, setting)
            elif (
                isinstance(self.exc, CodeInvalid)
                and item["code"] == ErrorCode.CODE_INVALID.value
            ):
                item["attempts_left"] = self.exc.attempts_left
            elif (
                isinstance(self.exc, ExceedsRemaining)
                and item["code"] == ErrorCode.EXCEEDS_REMAINING.value
            ):
                item["remaining"] = self.exc.remaining
        return body
