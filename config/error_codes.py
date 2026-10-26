from enum import StrEnum


class ErrorCode(StrEnum):
    """The stable error codes of the API (ADR 0004).

    A client acts on the ``code`` of an error, so these values are a contract: renaming or removing one
    is a breaking change. Add a code here, and to the registry in ``docs/adr/0004-error-status-codes.md``,
    in the same pull request as the endpoint that raises it.
    """

    # Sign-in and tokens
    INVALID_CREDENTIALS = "invalid_credentials"
    EMAIL_NOT_REGISTERED = "email_not_registered"
    WRONG_PASSWORD = "wrong_password"
    CAPTCHA_REQUIRED = "captcha_required"
    CAPTCHA_INVALID = "captcha_invalid"
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
    THROTTLED = "throttled"

    # Sign-in with a device key (ADR 0018)
    DEVICE_SIGN_IN_FAILED = "device_sign_in_failed"
    CHALLENGE_INVALID = "challenge_invalid"
    PUBLIC_KEY_INVALID = "public_key_invalid"
    ATTESTATION_INVALID = "attestation_invalid"
    PLATFORM_NOT_SUPPORTED = "platform_not_supported"
    DEVICE_KEY_LIMIT_REACHED = "device_key_limit_reached"

    # Email codes and account changes
    CODE_INVALID = "code_invalid"
    CODE_EXPIRED = "code_expired"
    CODE_ATTEMPTS_EXCEEDED = "code_attempts_exceeded"
    RESEND_TOO_SOON = "resend_too_soon"
    RESEND_LIMIT_REACHED = "resend_limit_reached"
    EMAIL_ALREADY_REGISTERED = "email_already_registered"
    EMAIL_UNCHANGED = "email_unchanged"
    INVALID_PASSWORD = "invalid_password"
    PASSWORD_TOO_SHORT = "password_too_short"
    PASSWORD_TOO_LONG = "password_too_long"
    PASSWORD_TOO_SIMILAR = "password_too_similar"
    PASSWORD_COMPROMISED = "password_compromised"
    PASSWORD_TOO_COMMON = "password_too_common"
    PASSWORD_UNCHANGED = "password_unchanged"
    PASSWORD_ATTEMPTS_EXCEEDED = "password_attempts_exceeded"
    RESET_TOKEN_INVALID = "reset_token_invalid"

    # The ledger: accounts and categories (ADR 0004, 0017)
    NAME_TAKEN = "name_taken"
    ACCOUNT_LIMIT_REACHED = "account_limit_reached"
    CATEGORY_LIMIT_REACHED = "category_limit_reached"

    # Transactions (ADR 0003, 0004)
    DOES_NOT_EXIST = "does_not_exist"
    CATEGORY_NOT_ALLOWED = "category_not_allowed"
    TRANSACTION_LIMIT_REACHED = "transaction_limit_reached"
    DATE_OUT_OF_RANGE = "date_out_of_range"
    INVALID_DATE_RANGE = "invalid_date_range"
    TOO_MANY_VALUES = "too_many_values"

    # Debts and repayments (ADR 0004)
    AMOUNT_BELOW_REPAID = "amount_below_repaid"
    DUE_BEFORE_START = "due_before_start"
    REPAYMENT_BEFORE_START = "repayment_before_start"
    START_AFTER_REPAYMENT = "start_after_repayment"
    DEBT_LIMIT_REACHED = "debt_limit_reached"
    REPAYMENT_LIMIT_REACHED = "repayment_limit_reached"

    # CSV import: the problems of a row (ADR 0004)
    UNKNOWN_CATEGORY = "unknown_category"
    UNKNOWN_ACCOUNT = "unknown_account"
    DUPLICATE = "duplicate"

    # Safe writes (ADR 0016)
    IDEMPOTENCY_KEY_REQUIRED = "idempotency_key_required"
    IDEMPOTENCY_KEY_INVALID = "idempotency_key_invalid"
    IDEMPOTENCY_KEY_REUSED = "idempotency_key_reused"
    IDEMPOTENCY_IN_PROGRESS = "idempotency_in_progress"
    PRECONDITION_FAILED = "precondition_failed"

    # Salary (ADR 0007)
    FIXED_ITEM_LIMIT_REACHED = "fixed_item_limit_reached"
    MONTH_HAS_FIXED_ITEMS = "month_has_fixed_items"

    # The server cannot do its job safely right now
    SERVICE_UNAVAILABLE = "service_unavailable"

    # Input hygiene (ADR 0021)
    INVALID_CHARACTERS = "invalid_characters"
    REQUEST_TOO_LARGE = "request_too_large"

    # Money and data rules
    INVALID_AMOUNT = "invalid_amount"
    CURRENCY_MISMATCH = "currency_mismatch"
    EXCEEDS_REMAINING = "exceeds_remaining"
    ACCOUNT_HAS_TRANSACTIONS = "account_has_transactions"
    CATEGORY_HAS_TRANSACTIONS = "category_has_transactions"
    CATEGORY_IS_BUILTIN = "category_is_builtin"
