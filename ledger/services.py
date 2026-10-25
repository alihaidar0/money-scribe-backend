"""Accounts and categories without HTTP (ADR 0005).

The limits are checked under a lock on the owner's row, so two requests at once cannot both pass them. A name that is
taken is ``400 name_taken``: the check here gives the message, the unique index in the database is the guard against
two requests that pick the same name at the same moment.
"""

from datetime import timedelta
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.db.models.functions import Lower
from django.utils import timezone
from rest_framework.exceptions import ErrorDetail, PermissionDenied, ValidationError

from config.error_codes import ErrorCode
from config.exceptions import conflict
from users.models import User

from .defaults import DEFAULT_ACCOUNTS
from .models import (
    MAX_ACCOUNTS,
    MAX_CUSTOM_CATEGORIES,
    MAX_TRANSACTIONS,
    Account,
    Category,
    CategoryKind,
    Transaction,
)


def _name_taken() -> ValidationError:
    return ValidationError(
        {
            "name": [
                ErrorDetail(
                    "This name is already used.", code=ErrorCode.NAME_TAKEN.value
                )
            ]
        }
    )


def lock_owner(owner: User) -> None:
    """Take a row lock on the owner for the rest of the transaction (the caller is inside one)."""
    User.objects.select_for_update().get(pk=owner.pk)


# --- accounts ---------------------------------------------------------------------------------


def account_name_is_taken(
    owner: User, name: str, *, exclude: Account | None = None
) -> bool:
    """Whether the person has an account with this name, in any case."""
    taken = (
        Account.objects.filter(owner=owner)
        .annotate(lowered=Lower("name"))
        .filter(lowered=name.lower())
    )
    if exclude is not None:
        taken = taken.exclude(pk=exclude.pk)
    return taken.exists()


def ensure_account_name_free(
    owner: User, name: str, *, exclude: Account | None = None
) -> None:
    """``400 name_taken`` when the person has an account with this name, in any case."""
    if account_name_is_taken(owner, name, exclude=exclude):
        raise _name_taken()


def _save_account(account: Account) -> Account:
    try:
        with transaction.atomic():  # a savepoint: a name taken meanwhile must not break the caller's transaction
            account.save()
    except IntegrityError:
        raise _name_taken() from None
    return account


def create_account(*, owner: User, data: dict[str, Any]) -> Account:
    """Add an account; the 21st is ``409 account_limit_reached``."""
    lock_owner(owner)
    if Account.objects.filter(owner=owner).count() >= MAX_ACCOUNTS:
        raise conflict(ErrorCode.ACCOUNT_LIMIT_REACHED)
    ensure_account_name_free(owner, data["name"])
    return _save_account(Account(owner=owner, name=data["name"], kind=data["kind"]))


def update_account(*, account: Account, data: dict[str, Any]) -> Account:
    """Apply a partial edit to an account the caller has locked. A request that changes nothing saves nothing."""
    changed = False
    if "name" in data and data["name"] != account.name:
        ensure_account_name_free(account.owner, data["name"], exclude=account)
        account.name = data["name"]
        changed = True
    if "kind" in data and data["kind"] != account.kind:
        account.kind = data["kind"]
        changed = True
    return _save_account(account) if changed else account


def delete_account(account: Account) -> None:
    """Delete an account the caller has locked; one that has transactions is ``409 account_has_transactions``.

    The transactions are not moved: there is no neutral account to move money to, and moving it silently would change
    balances (ADR 0017). Creating a transaction locks its account too, so a transaction cannot slip in meanwhile.
    """
    if Transaction.objects.filter(account=account).exists():
        raise conflict(ErrorCode.ACCOUNT_HAS_TRANSACTIONS)
    account.delete()


def create_default_accounts(user: User) -> list[Account]:
    """Make the two accounts a new person starts with, in the language of the account.

    A person who already has accounts keeps them and gets nothing new. The first is made a millisecond before the
    second, so the list (ordered by creation time) always shows the card first, and the app, which preselects the
    first account, preselects the card.
    """
    if Account.objects.filter(owner=user).exists():
        return []
    names = DEFAULT_ACCOUNTS.get(str(user.language), DEFAULT_ACCOUNTS["en"])
    created: list[Account] = []
    for index, (name, kind) in enumerate(names):
        account = Account.objects.create(owner=user, name=name, kind=kind)
        if created:
            account.created_at = created[0].created_at + timedelta(milliseconds=index)
            Account.objects.filter(pk=account.pk).update(created_at=account.created_at)
        created.append(account)
    return created


# --- categories -------------------------------------------------------------------------------


def category_name_is_taken(
    owner: User, name: str, *, exclude: Category | None = None
) -> bool:
    """Whether the name is one the person already uses in any case, or is a built-in key or name.

    The server knows the keys and the English names of the built-in categories, not the words the app shows for
    them in Arabic; the app checks those itself.
    """
    own = (
        Category.objects.filter(owner=owner)
        .annotate(lowered=Lower("name"))
        .filter(lowered=name.lower())
    )
    if exclude is not None:
        own = own.exclude(pk=exclude.pk)
    built_in = Category.objects.filter(owner__isnull=True).filter(
        Q(key=name.lower()) | Q(name__iexact=name)
    )
    return own.exists() or built_in.exists()


def ensure_category_name_free(
    owner: User, name: str, *, exclude: Category | None = None
) -> None:
    """``400 name_taken`` for a name that ``category_name_is_taken``."""
    if category_name_is_taken(owner, name, exclude=exclude):
        raise _name_taken()


def _save_category(category: Category) -> Category:
    try:
        with transaction.atomic():
            category.save()
    except IntegrityError:
        raise _name_taken() from None
    return category


def create_category(*, owner: User, data: dict[str, Any]) -> Category:
    """Add a custom category (always of the kind ``both``); the 51st is ``409 category_limit_reached``."""
    lock_owner(owner)
    if Category.objects.filter(owner=owner).count() >= MAX_CUSTOM_CATEGORIES:
        raise conflict(ErrorCode.CATEGORY_LIMIT_REACHED)
    ensure_category_name_free(owner, data["name"])
    return _save_category(
        Category(
            owner=owner,
            name=data["name"],
            icon=data["icon"],
            kind=CategoryKind.BOTH,
        )
    )


def update_category(*, category: Category, data: dict[str, Any]) -> Category:
    """Apply a partial edit to a custom category the caller has locked. A request that changes nothing saves nothing."""
    owner = category.owner
    if (
        owner is None
    ):  # refused earlier by ledger.permissions; this is the guard behind that check
        raise PermissionDenied(
            detail="A built-in category cannot be changed or deleted.",
            code=ErrorCode.CATEGORY_IS_BUILTIN.value,
        )
    changed = False
    if "name" in data and data["name"] != category.name:
        ensure_category_name_free(owner, data["name"], exclude=category)
        category.name = data["name"]
        changed = True
    if "icon" in data and data["icon"] != category.icon:
        category.icon = data["icon"]
        changed = True
    return _save_category(category) if changed else category


def delete_category(category: Category) -> None:
    """Delete a custom category the caller has locked; its transactions move to the built-in ``other`` (ADR 0017).

    The move and the delete are one database transaction, so nothing is left without a category and a failure
    changes nothing. Only the category changes on a moved transaction (and its ``updated_at`` and ``version``, in the
    same statement, as for any change); an amount, a date or an account never does. ``other`` is of the kind
    ``both``, so it allows every kind of transaction.
    """
    other = Category.objects.get(key="other", owner__isnull=True)
    Transaction.objects.filter(category=category).update(
        category=other, updated_at=timezone.now(), version=F("version") + 1
    )
    category.delete()


# --- transactions -----------------------------------------------------------------------------


def _does_not_exist(field: str) -> tuple[str, list[ErrorDetail]]:
    return field, [
        ErrorDetail("This id does not exist.", code=ErrorCode.DOES_NOT_EXIST.value)
    ]


def resolve_references(
    owner: User, *, category_id: Any = None, account_id: Any = None
) -> tuple[Category | None, Account | None]:
    """The category and the account a request names, or ``400 does_not_exist`` on the field that is wrong.

    An id that is unknown and an id of another person's custom category or account give the same answer, so the
    answer cannot be used to probe ids. A custom category and an account are locked, so one cannot be deleted while
    a transaction that uses it is being saved; a built-in category is never deleted and shared by everyone, so it is
    not locked.
    """
    category: Category | None = None
    account: Account | None = None
    missing: dict[str, list[ErrorDetail]] = {}
    if category_id is not None:
        category = Category.objects.filter(
            Q(owner__isnull=True) | Q(owner=owner), pk=category_id
        ).first()
        if category is not None and category.owner_id is not None:
            category = (
                Category.objects.select_for_update()
                .filter(pk=category.pk, owner=owner)
                .first()
            )
        if category is None:
            missing.update([_does_not_exist("category_id")])
    if account_id is not None:
        account = (
            Account.objects.select_for_update()
            .filter(pk=account_id, owner=owner)
            .first()
        )
        if account is None:
            missing.update([_does_not_exist("account_id")])
    if missing:
        raise ValidationError(missing)
    return category, account


def ensure_category_allows(category: Category, kind: str) -> None:
    """``400 category_not_allowed`` on ``category_id`` when the category is not for this kind of transaction."""
    if category.kind not in (CategoryKind.BOTH, kind):
        raise ValidationError(
            {
                "category_id": [
                    ErrorDetail(
                        "This category does not allow this kind of transaction.",
                        code=ErrorCode.CATEGORY_NOT_ALLOWED.value,
                    )
                ]
            }
        )


def create_transaction(*, owner: User, data: dict[str, Any]) -> Transaction:
    """Add a transaction; the 100 001st is ``409 transaction_limit_reached``. The caller is inside a transaction."""
    lock_owner(owner)
    if Transaction.objects.filter(owner=owner).count() >= MAX_TRANSACTIONS:
        raise conflict(ErrorCode.TRANSACTION_LIMIT_REACHED)
    category, account = resolve_references(
        owner, category_id=data["category_id"], account_id=data["account_id"]
    )
    if (
        category is None or account is None
    ):  # both ids were given, so resolve_references raised otherwise
        raise RuntimeError("A category and an account were resolved.")
    ensure_category_allows(category, data["kind"])
    money = data["amount"]
    return Transaction.objects.create(
        owner=owner,
        kind=data["kind"],
        amount=money.minor,
        currency=money.currency.value,
        date=data["date"],
        category=category,
        account=account,
        note=data.get("note", ""),
    )


def update_transaction(*, record: Transaction, data: dict[str, Any]) -> Transaction:
    """Apply a partial edit to a transaction the caller has locked. A request that changes nothing saves nothing.

    The currency is not editable (the serializer refuses it): it would reinterpret the stored amount. A change of the
    kind or of the category checks the category again.
    """
    owner = record.owner
    category, account = resolve_references(
        owner, category_id=data.get("category_id"), account_id=data.get("account_id")
    )
    kind = data.get("kind", record.kind)
    if "kind" in data or "category_id" in data:
        ensure_category_allows(category or record.category, kind)

    changes: dict[str, Any] = {}
    if "kind" in data:
        changes["kind"] = kind
    if "amount" in data:
        changes["amount"] = data["amount"].minor
    for name in ("date", "note"):
        if name in data:
            changes[name] = data[name]
    if category is not None:
        changes["category"] = category
    if account is not None:
        changes["account"] = account

    changed = False
    for name, value in changes.items():
        if getattr(record, name) != value:
            setattr(record, name, value)
            changed = True
    if changed:
        record.save()
    return record
