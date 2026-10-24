from celery import shared_task
from django.utils import timezone

from .models import StoredResponse


@shared_task
def purge_expired_keys() -> int:
    """Delete the stored answers whose 24 hours are over; returns how many.

    An expired key is ignored at lookup whatever this job does, so correctness never depends on it: it only
    stops answers, which hold financial data, from being kept after they can no longer be replayed.
    """
    deleted, _ = StoredResponse.objects.filter(expires_at__lte=timezone.now()).delete()
    return deleted
