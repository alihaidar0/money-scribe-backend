from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .models import OutgoingEmail


@shared_task
def purge_old_emails() -> int:
    """Delete saved emails older than ``OUTBOX_RETENTION_HOURS``; returns how many were deleted."""
    cutoff = timezone.now() - timedelta(hours=settings.OUTBOX_RETENTION_HOURS)
    deleted, _ = OutgoingEmail.objects.filter(created_at__lt=cutoff).delete()
    return deleted
