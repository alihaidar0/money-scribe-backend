import uuid
from typing import ClassVar

from django.conf import settings
from django.db import models
from django.utils import timezone


class StoredResponse(models.Model):
    """The answer to a create that carried an ``Idempotency-Key`` (ADR 0016).

    A row exists only for a request that succeeded: it is inserted in the same database transaction as the
    record the request created, so a request that failed leaves nothing behind. The ``fingerprint`` is a hash
    of the method, the path and the body, which tells a repeat from a different request that reuses the key.
    The body holds the answer, which is financial data: the table is in the account's cascade, is purged when
    a key expires, and is never logged.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="idempotent_responses",
    )
    key = models.CharField(max_length=64)
    fingerprint = models.CharField(max_length=64)
    # 0 only while the request that claimed the key is still running, inside its own transaction.
    status_code = models.PositiveSmallIntegerField(default=0)
    body = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["user", "key"], name="idempotency_user_key_unique"
            )
        ]

    def __str__(self) -> str:
        return f"idempotent answer of {self.user_id}"
