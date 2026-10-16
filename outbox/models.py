import uuid
from typing import ClassVar

from django.db import models


class OutgoingEmail(models.Model):
    """An email that the database backend saved instead of sending (ADR 0010).

    Used on development and staging only, so that a 6-digit code can be copied from the admin before real
    email delivery exists. A row holds the message in readable form, so it lives for a day (a scheduled task
    deletes older rows) and is visible to superuser accounts only.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    from_email = models.CharField(max_length=254)
    recipients = models.JSONField(default=list)
    subject = models.CharField(max_length=998)
    body = models.TextField()

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.subject} → {', '.join(self.recipients)}"
