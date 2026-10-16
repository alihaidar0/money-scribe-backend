import logging

from django.core.mail import send_mail

from .models import Language

logger = logging.getLogger(__name__)

# Written in the user's language (docs/api-fields.md). Never put the code or the address in a log line.
_VERIFICATION = {
    Language.ENGLISH: (
        "Your Money Scribe verification code",
        "Your Money Scribe verification code is {code}.\n"
        "It expires in {minutes} minutes.\n"
        "If you did not request it, you can ignore this email.\n",
    ),
    Language.ARABIC: (
        "رمز التحقق الخاص بك في Money Scribe",
        "رمز التحقق الخاص بك في Money Scribe هو {code}.\n"
        "تنتهي صلاحية الرمز خلال {minutes} دقائق.\n"
        "إذا لم تطلب هذا الرمز فتجاهل هذه الرسالة.\n",
    ),
}


_ACCOUNT_EXISTS = {
    Language.ENGLISH: (
        "Someone tried to register your Money Scribe email",
        "Someone tried to create a Money Scribe account with this email address, "
        "but you already have one.\n"
        "You can sign in with your password. If you forgot it, use the password reset in the app.\n"
        "If this was not you, you can ignore this email: nothing has changed.\n",
    ),
    Language.ARABIC: (
        "حاول شخص ما التسجيل في Money Scribe بالبريد الإلكتروني الخاص بك",
        "حاول شخص ما إنشاء حساب في Money Scribe بهذا البريد الإلكتروني، "
        "لكن لديك حساب بالفعل.\n"
        "يمكنك تسجيل الدخول بكلمة المرور. وإذا نسيتها فاستخدم خيار إعادة تعيين كلمة المرور في التطبيق.\n"
        "إذا لم تكن أنت، يمكنك تجاهل هذه الرسالة: لم يتغير شيء.\n",
    ),
}


def _send(email: str, subject: str, body: str, what: str) -> None:
    """Send one email. A delivery failure is logged without details; the user can ask again."""
    try:
        send_mail(subject, body, None, [email], fail_silently=False)
    except Exception:
        logger.warning("Sending %s failed.", what)


def send_account_exists_notice(*, email: str, language: str) -> None:
    """Tell the owner of a verified address that someone tried to register it.

    Registration answers the same whether the address is free or taken, so this email is how the real
    owner learns about the attempt. It carries no code and no link, and changes nothing.
    """
    subject, body = _ACCOUNT_EXISTS.get(
        Language(language), _ACCOUNT_EXISTS[Language.ENGLISH]
    )
    _send(email, subject, body, "an account notice")


def send_verification_code(
    *, email: str, language: str, code: str, lifetime_seconds: int
) -> None:
    """Email the code. A delivery failure is logged without details; the user can ask for a new code."""
    subject, template = _VERIFICATION.get(
        Language(language), _VERIFICATION[Language.ENGLISH]
    )
    body = template.format(code=code, minutes=max(1, lifetime_seconds // 60))
    _send(email, subject, body, "a verification email")
