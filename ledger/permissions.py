from typing import Any

from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import SAFE_METHODS, BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView

from config.error_codes import ErrorCode


class CustomCategoriesOnly(BasePermission):  # type: ignore[misc]
    """A built-in category can be read, never changed or deleted: ``403 category_is_builtin``."""

    def has_object_permission(self, request: Request, view: APIView, obj: Any) -> bool:
        if request.method in SAFE_METHODS or obj.owner_id is not None:
            return True
        raise PermissionDenied(
            detail="A built-in category cannot be changed or deleted.",
            code=ErrorCode.CATEGORY_IS_BUILTIN.value,
        )
