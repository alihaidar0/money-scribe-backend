from typing import TYPE_CHECKING

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import AdminUserCreationForm, UserChangeForm

from .models import User

# Django's classes are generic only for the type checker; at runtime they are not subscriptable.
if TYPE_CHECKING:
    _UserAdminBase = DjangoUserAdmin[User]
    _CreationFormBase = AdminUserCreationForm[User]
    _ChangeFormBase = UserChangeForm[User]
else:
    _UserAdminBase = DjangoUserAdmin
    _CreationFormBase = AdminUserCreationForm
    _ChangeFormBase = UserChangeForm


class UserCreationForm(_CreationFormBase):
    class Meta:
        model = User
        fields = ("email", "name")


class UserEditForm(_ChangeFormBase):
    class Meta:
        model = User
        fields = "__all__"


@admin.register(User)
class UserAdmin(_UserAdminBase):
    form = UserEditForm
    add_form = UserCreationForm
    ordering = ("email",)
    list_display = ("email", "name", "email_verified_at", "is_active", "is_staff")
    list_filter = ("is_active", "is_staff", "is_superuser")
    search_fields = ("email", "name")
    readonly_fields = ("last_login", "date_joined")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("name", "email_verified_at")}),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "email",
                    "name",
                    "usable_password",
                    "password1",
                    "password2",
                ),
            },
        ),
    )
