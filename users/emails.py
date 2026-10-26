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


_RESET_CODE = {
    Language.ENGLISH: (
        "Your Money Scribe password reset code",
        "Your Money Scribe password reset code is {code}.\n"
        "It expires in {minutes} minutes.\n"
        "If you did not ask to reset your password, you can ignore this email: your password has not changed.\n",
    ),
    Language.ARABIC: (
        "رمز إعادة تعيين كلمة المرور في Money Scribe",
        "رمز إعادة تعيين كلمة المرور في Money Scribe هو {code}.\n"
        "تنتهي صلاحية الرمز خلال {minutes} دقائق.\n"
        "إذا لم تطلب إعادة تعيين كلمة المرور فتجاهل هذه الرسالة: لم تتغير كلمة المرور.\n",
    ),
}

_PASSWORD_CHANGED = {
    Language.ENGLISH: (
        "Your Money Scribe password was changed",
        "The password of your Money Scribe account was just changed, and the other devices were signed out.\n"
        "If this was you, there is nothing to do.\n"
        "If it was not you, reset your password in the app now.\n",
    ),
    Language.ARABIC: (
        "تم تغيير كلمة المرور في Money Scribe",
        "تم تغيير كلمة مرور حسابك في Money Scribe للتو، وتم تسجيل الخروج من الأجهزة الأخرى.\n"
        "إذا كنت أنت من فعل ذلك فلا حاجة لأي إجراء.\n"
        "وإذا لم تكن أنت، فأعد تعيين كلمة المرور من التطبيق الآن.\n",
    ),
}

_ACCOUNT_DELETED = {
    Language.ENGLISH: (
        "Your Money Scribe account was deleted",
        "Your Money Scribe account and all the data in it were deleted, and every device was signed out.\n"
        "This cannot be undone. If this was not you, your password was used to do it; "
        "you can register again at any time.\n",
    ),
    Language.ARABIC: (
        "تم حذف حسابك في Money Scribe",
        "تم حذف حسابك في Money Scribe وجميع البيانات الموجودة فيه، وتم تسجيل الخروج من كل الأجهزة.\n"
        "لا يمكن التراجع عن ذلك. إذا لم تكن أنت، فقد استُخدمت كلمة مرورك لتنفيذه؛ "
        "ويمكنك التسجيل من جديد في أي وقت.\n",
    ),
}


_EMAIL_CHANGE_CODE = {
    Language.ENGLISH: (
        "Confirm your new Money Scribe email",
        "Your Money Scribe email change code is {code}.\n"
        "It expires in {minutes} minutes.\n"
        "If you did not ask to use this address, you can ignore this email: nothing has changed.\n",
    ),
    Language.ARABIC: (
        "أكّد بريدك الإلكتروني الجديد في Money Scribe",
        "رمز تغيير البريد الإلكتروني في Money Scribe هو {code}.\n"
        "تنتهي صلاحية الرمز خلال {minutes} دقائق.\n"
        "إذا لم تطلب استخدام هذا العنوان فتجاهل هذه الرسالة: لم يتغير شيء.\n",
    ),
}

_EMAIL_CHANGED = {
    Language.ENGLISH: (
        "The email of your Money Scribe account was changed",
        "The email address of your Money Scribe account was just changed, and the other devices were signed out.\n"
        "From now on you sign in with the new address.\n"
        "If this was not you, someone knows your password and may have taken over the account.\n",
    ),
    Language.ARABIC: (
        "تم تغيير البريد الإلكتروني لحسابك في Money Scribe",
        "تم تغيير عنوان البريد الإلكتروني لحسابك في Money Scribe للتو، وتم تسجيل الخروج من الأجهزة الأخرى.\n"
        "ستسجّل الدخول من الآن بالعنوان الجديد.\n"
        "إذا لم تكن أنت، فهناك من يعرف كلمة مرورك وربما استولى على الحساب.\n",
    ),
}

_DEVICE_KEY_ADDED = {
    Language.ENGLISH: (
        "Fingerprint sign-in was turned on in Money Scribe",
        'Fingerprint sign-in was just turned on for the device "{device}" on your Money Scribe account.\n'
        "That device can now sign in without your password. You can turn it off in the app at any time.\n"
        "If this was not you, change your password in the app now: that turns fingerprint sign-in off everywhere.\n",
    ),
    Language.ARABIC: (
        "تم تفعيل تسجيل الدخول ببصمة الإصبع في Money Scribe",
        'تم للتو تفعيل تسجيل الدخول ببصمة الإصبع للجهاز "{device}" في حسابك على Money Scribe.\n'
        "يستطيع هذا الجهاز الآن تسجيل الدخول دون كلمة المرور، ويمكنك إيقاف ذلك من التطبيق في أي وقت.\n"
        "إذا لم تكن أنت، فغيّر كلمة المرور من التطبيق الآن: فهذا يوقف تسجيل الدخول ببصمة الإصبع على كل الأجهزة.\n",
    ),
}


_DATA_EXPORTED = {
    Language.ENGLISH: (
        "Your Money Scribe data was exported",
        'An export of your Money Scribe data was just made from the device "{device}" ({when}).\n'
        "It needed your password. This email says only that it happened: it has no content and no link.\n"
        "If this was not you, change your password in the app now: that signs the other devices out.\n",
    ),
    Language.ARABIC: (
        "تم تصدير بياناتك في Money Scribe",
        'تم للتو تصدير بياناتك في Money Scribe من الجهاز "{device}" ({when}).\n'
        "وقد تطلّب ذلك كلمة المرور. تذكر هذه الرسالة أن العملية تمت فقط: لا تحتوي على أي بيانات ولا على رابط.\n"
        "إذا لم تكن أنت، فغيّر كلمة المرور من التطبيق الآن: فهذا يُسجّل خروج الأجهزة الأخرى.\n",
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


def send_password_reset_code(
    *, email: str, language: str, code: str, lifetime_seconds: int
) -> None:
    """Email the password reset code. A delivery failure is logged without details; the user can ask again."""
    subject, template = _RESET_CODE.get(
        Language(language), _RESET_CODE[Language.ENGLISH]
    )
    body = template.format(code=code, minutes=max(1, lifetime_seconds // 60))
    _send(email, subject, body, "a password reset email")


def send_password_changed_notice(*, email: str, language: str) -> None:
    """Tell the owner that the password changed (by a change or a reset), so a stranger's change is noticed."""
    subject, body = _PASSWORD_CHANGED.get(
        Language(language), _PASSWORD_CHANGED[Language.ENGLISH]
    )
    _send(email, subject, body, "a password change notice")


def send_account_deleted_notice(*, email: str, language: str) -> None:
    """Confirm a deletion to the address it had. Nothing is kept: the address is only used for this email."""
    subject, body = _ACCOUNT_DELETED.get(
        Language(language), _ACCOUNT_DELETED[Language.ENGLISH]
    )
    _send(email, subject, body, "an account deletion notice")


def send_email_change_code(
    *, email: str, language: str, code: str, lifetime_seconds: int
) -> None:
    """Email the change code to the **new** address. A failure is logged without details; the user can ask again."""
    subject, template = _EMAIL_CHANGE_CODE.get(
        Language(language), _EMAIL_CHANGE_CODE[Language.ENGLISH]
    )
    body = template.format(code=code, minutes=max(1, lifetime_seconds // 60))
    _send(email, subject, body, "an email change code")


def send_email_changed_notice(*, email: str, language: str) -> None:
    """Tell the **old** address that the account moved to another one, without naming the new address."""
    subject, body = _EMAIL_CHANGED.get(
        Language(language), _EMAIL_CHANGED[Language.ENGLISH]
    )
    _send(email, subject, body, "an email change notice")


def send_device_key_added_notice(
    *, email: str, language: str, device_name: str
) -> None:
    """Tell the owner that a device can now sign in with a fingerprint, so a stranger's key is noticed.

    It names the device the way the app labelled it and carries no link and no code.
    """
    subject, template = _DEVICE_KEY_ADDED.get(
        Language(language), _DEVICE_KEY_ADDED[Language.ENGLISH]
    )
    _send(email, subject, template.format(device=device_name), "a device key notice")


def send_data_exported_notice(
    *, email: str, language: str, device_name: str | None, when: str
) -> None:
    """Tell the owner that an export was made, so a stranger's export is noticed.

    It names the device the way the app labelled it and the time, and carries no content and no link.
    """
    subject, template = _DATA_EXPORTED.get(
        Language(language), _DATA_EXPORTED[Language.ENGLISH]
    )
    body = template.format(device=device_name or "-", when=when)
    _send(email, subject, body, "a data export notice")
