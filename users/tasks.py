from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .models import User


@shared_task
def purge_unverified_accounts() -> int:
    """Delete accounts that never verified their email, with their codes; returns how many.

    An unverified account holds an address, a name and a password hash for someone who never proved the
    address is theirs, so it is not kept (data minimisation). It goes once it has been idle for
    ``UNVERIFIED_ACCOUNT_RETENTION_DAYS``: no new code was requested in that time either, so a person who
    registers again, or asks for a code, keeps theirs. Staff and superuser accounts are never touched.
    """
    cutoff = timezone.now() - timedelta(days=settings.UNVERIFIED_ACCOUNT_RETENTION_DAYS)
    stale = User.objects.filter(
        email_verified_at__isnull=True,
        is_staff=False,
        is_superuser=False,
        date_joined__lt=cutoff,
    ).exclude(email_codes__created_at__gte=cutoff)
    _, deleted = stale.delete()
    return deleted.get("users.User", 0)
