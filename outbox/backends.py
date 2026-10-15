from collections.abc import Sequence

from django.core.mail import EmailMessage
from django.core.mail.backends.base import BaseEmailBackend

from .models import OutgoingEmail


class DatabaseEmailBackend(BaseEmailBackend):
    """Save each email to the ``OutgoingEmail`` table instead of sending it.

    Select it with ``EMAIL_BACKEND=outbox.backends.DatabaseEmailBackend``. Only the text body is kept;
    the system check ``outbox.E001`` refuses this backend when ``APP_ENVIRONMENT`` is ``production``.
    """

    def send_messages(self, email_messages: Sequence[EmailMessage]) -> int:
        rows = [
            OutgoingEmail(
                from_email=message.from_email,
                recipients=message.recipients(),
                subject=str(message.subject),
                body=str(message.body),
            )
            for message in email_messages
        ]
        OutgoingEmail.objects.bulk_create(rows)
        return len(rows)
