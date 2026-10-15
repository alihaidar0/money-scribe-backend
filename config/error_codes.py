from enum import StrEnum


class ErrorCode(StrEnum):
    """The stable error codes of the API (ADR 0004).

    A client acts on the ``code`` of an error, so these values are a contract: renaming or removing one
    is a breaking change. Add a code here, and to the registry in ``docs/adr/0004-error-status-codes.md``,
    in the same pull request as the endpoint that raises it.
    """

    # Sign-in and tokens
    INVALID_CREDENTIALS = "invalid_credentials"
    TOKEN_EXPIRED = "token_expired"
    TOKEN_INVALID = "token_invalid"
    SESSION_SIGNED_OUT = "session_signed_out"
    SESSION_REVOKED_BY_USER = "session_revoked_by_user"
    SESSION_PASSWORD_CHANGED = "session_password_changed"
    SESSION_EMAIL_CHANGED = "session_email_changed"
    SESSION_TOKEN_REUSE = "session_token_reuse"
    SESSION_EXPIRED = "session_expired"
    EMAIL_NOT_VERIFIED = "email_not_verified"
    SIGN_IN_LOCKED = "sign_in_locked"

    # Email codes and account changes
    CODE_INVALID = "code_invalid"
    CODE_EXPIRED = "code_expired"
    CODE_ATTEMPTS_EXCEEDED = "code_attempts_exceeded"
    RESEND_TOO_SOON = "resend_too_soon"
    EMAIL_ALREADY_REGISTERED = "email_already_registered"
    INVALID_PASSWORD = "invalid_password"

    # Money and data rules
    INVALID_AMOUNT = "invalid_amount"
    CURRENCY_MISMATCH = "currency_mismatch"
    EXCEEDS_REMAINING = "exceeds_remaining"
    ACCOUNT_HAS_TRANSACTIONS = "account_has_transactions"
    CATEGORY_HAS_TRANSACTIONS = "category_has_transactions"
    CATEGORY_IS_BUILTIN = "category_is_builtin"
