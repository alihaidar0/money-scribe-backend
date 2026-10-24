"""An abstract base for every record the user can edit (ADR 0005, ADR 0016)."""

from typing import Any

from django.db import models
from django.db.models import F


class VersionedModel(models.Model):
    """``created_at``, ``updated_at`` and a ``version`` that rises by one with every change.

    The version is what the API sends as the ``ETag`` and what an edit's ``If-Match`` is compared with, so a
    save from a stale copy can be refused (``config.api.VersionedMixin``). It is raised in the same ``UPDATE``
    statement as the change (``version = version + 1``), so concurrent saves each count, and it is never set from
    request data. ``common`` is not an installed app, so only an abstract model can live here.
    """

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1, editable=False)

    class Meta:
        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self._state.adding:
            super().save(*args, **kwargs)
            return
        fields = kwargs.get("update_fields")
        if fields is not None:
            kwargs["update_fields"] = {*fields, "version", "updated_at"}
        self.version = F("version") + 1
        super().save(*args, **kwargs)
        self.refresh_from_db(fields=["version"])
