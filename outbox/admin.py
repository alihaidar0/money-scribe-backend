from typing import TYPE_CHECKING

from django.contrib import admin
from django.http import HttpRequest

from .models import OutgoingEmail

# Django's ModelAdmin is generic only for the type checker; at runtime it is not subscriptable.
if TYPE_CHECKING:
    _OutgoingEmailAdminBase = admin.ModelAdmin[OutgoingEmail]
else:
    _OutgoingEmailAdminBase = admin.ModelAdmin


@admin.register(OutgoingEmail)
class OutgoingEmailAdmin(_OutgoingEmailAdminBase):
    """Read-only list of the saved emails, for superuser accounts only (ADR 0010).

    Open an email to copy its code. Messages can be deleted but not added or edited.
    """

    list_display = ("created_at", "recipient_list", "subject")
    fields = ("created_at", "recipients", "subject", "body", "from_email", "id")
    readonly_fields = fields
    search_fields = ("subject", "body")
    date_hierarchy = "created_at"
    list_per_page = 25

    @admin.display(description="Recipients")
    def recipient_list(self, obj: OutgoingEmail) -> str:
        return ", ".join(obj.recipients)

    def _is_superuser(self, request: HttpRequest) -> bool:
        return bool(request.user.is_active and request.user.is_superuser)

    def has_module_permission(self, request: HttpRequest) -> bool:
        return self._is_superuser(request)

    def has_view_permission(
        self, request: HttpRequest, obj: OutgoingEmail | None = None
    ) -> bool:
        return self._is_superuser(request)

    def has_delete_permission(
        self, request: HttpRequest, obj: OutgoingEmail | None = None
    ) -> bool:
        return self._is_superuser(request)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: OutgoingEmail | None = None
    ) -> bool:
        return False
