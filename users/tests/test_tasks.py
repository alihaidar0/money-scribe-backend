from datetime import timedelta

import pytest
from celery.schedules import crontab
from django.conf import settings
from django.utils import timezone

from users.models import EmailCode, EmailCodePurpose, User
from users.tasks import purge_unverified_accounts
from users.tests.factories import UserFactory, VerifiedUserFactory

pytestmark = pytest.mark.django_db


def aged(user: User, days: int) -> User:
    user.date_joined = timezone.now() - timedelta(days=days)
    user.save()
    return user


def code_for(user: User, days_ago: int) -> EmailCode:
    moment = timezone.now() - timedelta(days=days_ago)
    return EmailCode.objects.create(
        user=user,
        purpose=EmailCodePurpose.VERIFY_EMAIL,
        code_hash="h",
        created_at=moment,
        expires_at=moment + timedelta(minutes=10),
    )


def test_an_account_that_never_verified_is_deleted_with_its_codes() -> None:
    stale = aged(UserFactory.create(), 8)
    code_for(stale, 8)

    assert purge_unverified_accounts() == 1

    assert not User.objects.filter(pk=stale.pk).exists()
    assert EmailCode.objects.count() == 0


def test_a_recent_unverified_account_is_kept() -> None:
    recent = aged(UserFactory.create(), 6)

    assert purge_unverified_accounts() == 0

    assert User.objects.filter(pk=recent.pk).exists()


def test_a_verified_account_is_never_deleted() -> None:
    old = aged(VerifiedUserFactory.create(), 400)

    assert purge_unverified_accounts() == 0

    assert User.objects.filter(pk=old.pk).exists()


@pytest.mark.parametrize("flags", [{"is_staff": True}, {"is_superuser": True}])
def test_staff_and_superusers_are_never_deleted_even_unverified(
    flags: dict[str, bool],
) -> None:
    admin = aged(UserFactory.create(**flags), 400)

    assert purge_unverified_accounts() == 0

    assert User.objects.filter(pk=admin.pk).exists()


def test_asking_for_a_new_code_keeps_an_old_pending_account() -> None:
    returning = aged(UserFactory.create(), 30)
    code_for(returning, 1)

    assert purge_unverified_accounts() == 0

    assert User.objects.filter(pk=returning.pk).exists()


def test_a_pending_account_whose_last_code_is_old_is_deleted() -> None:
    forgotten = aged(UserFactory.create(), 30)
    code_for(forgotten, 9)

    assert purge_unverified_accounts() == 1


def test_only_the_stale_accounts_go() -> None:
    stale = [aged(UserFactory.create(), 10) for _ in range(3)]
    kept = [UserFactory.create(), VerifiedUserFactory.create()]

    assert purge_unverified_accounts() == 3

    assert User.objects.filter(pk__in=[user.pk for user in kept]).count() == 2
    assert not User.objects.filter(pk__in=[user.pk for user in stale]).exists()


def test_running_it_twice_is_harmless() -> None:
    aged(UserFactory.create(), 10)

    assert purge_unverified_accounts() == 1
    assert purge_unverified_accounts() == 0


def test_the_retention_is_a_setting_and_the_task_is_scheduled_daily() -> None:
    assert settings.UNVERIFIED_ACCOUNT_RETENTION_DAYS == 7
    entry = settings.CELERY_BEAT_SCHEDULE["users-purge-unverified"]
    assert entry["task"] == "users.tasks.purge_unverified_accounts"
    assert entry["schedule"] == crontab(minute=37, hour=3)
