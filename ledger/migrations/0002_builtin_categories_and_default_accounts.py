"""The seven built-in categories, and the two default accounts of the people who are already verified.

The built-in rows have fixed ids, so every environment (and the app) sees the same ones. The names, keys and icons
are copied here on purpose: a migration must not change when the code around it does.
"""

from datetime import timedelta
from typing import Any

from django.db import migrations

# (id, key, English name, icon, kind, position)
BUILT_IN = [
    ("0198f2a0-0000-7000-8000-000000000001", "groceries", "Groceries", "groceries", "expense", 1),
    ("0198f2a0-0000-7000-8000-000000000002", "dining", "Dining", "dining", "expense", 2),
    ("0198f2a0-0000-7000-8000-000000000003", "transport", "Transport", "transport", "expense", 3),
    ("0198f2a0-0000-7000-8000-000000000004", "housing", "Housing", "housing", "expense", 4),
    ("0198f2a0-0000-7000-8000-000000000005", "utilities", "Utilities", "utilities", "expense", 5),
    ("0198f2a0-0000-7000-8000-000000000006", "scholarship", "Scholarship", "scholarship", "income", 6),
    ("0198f2a0-0000-7000-8000-000000000007", "other", "Other", "other", "both", 7),
]

DEFAULT_ACCOUNTS = {
    "en": (("Card", "card"), ("Cash", "cash")),
    "ar": (("بطاقة", "card"), ("نقدًا", "cash")),
}


def add_built_in_categories(apps: Any, schema_editor: Any) -> None:
    Category = apps.get_model("ledger", "Category")
    for pk, key, name, icon, kind, position in BUILT_IN:
        Category.objects.update_or_create(
            key=key,
            defaults={
                "id": pk,
                "owner": None,
                "name": name,
                "icon": icon,
                "kind": kind,
                "position": position,
            },
        )


def remove_built_in_categories(apps: Any, schema_editor: Any) -> None:
    apps.get_model("ledger", "Category").objects.filter(
        key__in=[row[1] for row in BUILT_IN], owner__isnull=True
    ).delete()


def add_default_accounts(apps: Any, schema_editor: Any) -> None:
    """Give every verified person who has no account the two defaults, in the language of the account."""
    User = apps.get_model("users", "User")
    Account = apps.get_model("ledger", "Account")
    for user in User.objects.filter(email_verified_at__isnull=False).iterator():
        if Account.objects.filter(owner=user).exists():
            continue
        names = DEFAULT_ACCOUNTS.get(user.language, DEFAULT_ACCOUNTS["en"])
        first = None
        for index, (name, kind) in enumerate(names):
            account = Account.objects.create(owner=user, name=name, kind=kind)
            if first is None:
                first = account.created_at
            else:
                Account.objects.filter(pk=account.pk).update(
                    created_at=first + timedelta(milliseconds=index)
                )


class Migration(migrations.Migration):
    dependencies = [
        ("ledger", "0001_initial"),
        ("users", "0005_security_event"),
    ]

    operations = [
        migrations.RunPython(add_built_in_categories, remove_built_in_categories),
        # The defaults belong to the people who own them; reversing this migration leaves their accounts alone.
        migrations.RunPython(add_default_accounts, migrations.RunPython.noop),
    ]
