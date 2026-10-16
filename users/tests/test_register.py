import re
from typing import Any

import pytest
from django.core import mail
from rest_framework.response import Response
from rest_framework.test import APIClient

from conftest import Clock
from users.models import EmailCode, Session, User
from users.tests.factories import UserFactory, VerifiedUserFactory
from users.tests.helpers import AUTH, emailed_code, first_error
from users.throttles import RegisterThrottle

pytestmark = pytest.mark.django_db

REGISTER = f"{AUTH}/register/"
BODY: dict[str, Any] = {
    "name": "Sara Example",
    "email": "sara@example.com",
    "password": "a-fine-passphrase-7",
    "language": "en",
    "time_zone": "Europe/Moscow",
}


def register(client: APIClient, **overrides: Any) -> Response:
    return client.post(REGISTER, {**BODY, **overrides})


def test_register_creates_an_unverified_account_and_emails_a_code(
    api_client: APIClient,
) -> None:
    response = register(api_client)

    assert response.status_code == 201
    assert response.json() == {
        "email": "sara@example.com",
        "code_expires_in": 600,
        "resend_available_in": 60,
    }
    user = User.objects.get(email="sara@example.com")
    assert user.name == "Sara Example"
    assert user.email_verified_at is None
    assert user.language == "en"
    assert user.time_zone == "Europe/Moscow"
    assert user.check_password("a-fine-passphrase-7")
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["sara@example.com"]
    assert len(emailed_code()) == 6


def test_register_creates_no_session_and_returns_no_tokens(
    api_client: APIClient,
) -> None:
    response = register(api_client)

    assert "access_token" not in response.json()
    assert Session.objects.count() == 0


def test_the_code_is_stored_only_as_a_hash(api_client: APIClient) -> None:
    register(api_client)

    record = EmailCode.objects.get()
    assert record.code_hash != emailed_code()
    assert len(record.code_hash) == 64


def test_the_email_is_trimmed_and_lower_cased(api_client: APIClient) -> None:
    response = register(api_client, email="  Sara@Example.COM ")

    assert response.json()["email"] == "sara@example.com"
    assert User.objects.filter(email="sara@example.com").exists()


def test_language_and_time_zone_default_when_left_out(api_client: APIClient) -> None:
    body = {key: BODY[key] for key in ("name", "email", "password")}

    assert api_client.post(REGISTER, body).status_code == 201

    user = User.objects.get()
    assert (user.language, user.time_zone) == ("en", "UTC")


def test_the_email_is_written_in_the_users_language(api_client: APIClient) -> None:
    register(api_client, language="ar")

    assert "رمز التحقق" in str(mail.outbox[0].body)
    assert emailed_code() in str(mail.outbox[0].body)


@pytest.mark.parametrize(
    ("password", "codes"),
    [
        ("a" * 14, {"password_too_short"}),
        ("short", {"password_too_short"}),
        ("1q2w3e4r5t6y7u8i9o0p", {"password_too_common"}),
        ("a" * 129, {"password_too_long"}),
        ("sara@example.com!!!!", {"password_too_similar"}),
        ("saraexample", {"password_too_short", "password_too_similar"}),
    ],
)
def test_each_failed_password_rule_has_its_own_code(
    api_client: APIClient, password: str, codes: set[str]
) -> None:
    response = register(api_client, password=password)

    assert response.status_code == 400
    assert response.json()["type"] == "validation_error"
    errors = response.json()["errors"]
    assert {error["code"] for error in errors} == codes
    assert {error["attr"] for error in errors} == {"password"}
    assert User.objects.count() == 0
    assert mail.outbox == []


@pytest.mark.parametrize(
    "password",
    [
        "correct horse battery staple",
        "a" * 15,
        "x" * 128,
        "\u0645\u0631\u062d\u0628\u0627 \u0628\u0643 \u0641\u064a \u062a\u0637\u0628\u064a\u0642\u0646\u0627 \u0627\u0644\u062c\u062f\u064a\u062f",
    ],
)
def test_a_long_password_needs_no_digit_symbol_or_capital(
    api_client: APIClient, password: str
) -> None:
    assert register(api_client, password=password).status_code == 201


def test_a_password_is_stored_normalized_so_either_spelling_signs_in(
    api_client: APIClient,
) -> None:
    ascii_password = "good passphrase today"
    fullwidth = "".join(
        chr(ord(c) + 0xFEE0) if c != " " else "\u3000" for c in ascii_password
    )

    register(api_client, password=fullwidth)

    user = User.objects.get()
    assert user.check_password(ascii_password)
    assert not user.check_password(
        fullwidth
    )  # the stored hash is of the normalized text


def test_the_password_is_never_trimmed(api_client: APIClient) -> None:
    register(api_client, password="  surrounded by spaces  ")

    assert User.objects.get().check_password("  surrounded by spaces  ")


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("email", "not-an-email", "invalid"),
        ("email", "", "blank"),
        ("name", "   ", "blank"),
        ("name", "x" * 101, "max_length"),
        ("language", "fr", "invalid_choice"),
        ("time_zone", "Mars/Olympus", "invalid"),
        ("time_zone", "../../etc/passwd", "invalid"),
    ],
)
def test_field_errors_name_the_field(
    api_client: APIClient, field: str, value: str, code: str
) -> None:
    response = register(api_client, **{field: value})

    assert response.status_code == 400
    assert first_error(response)["attr"] == field
    assert first_error(response)["code"] == code


@pytest.mark.parametrize("field", ["name", "email", "password"])
def test_required_fields(api_client: APIClient, field: str) -> None:
    body = {key: value for key, value in BODY.items() if key != field}

    response = api_client.post(REGISTER, body)

    assert response.status_code == 400
    assert first_error(response) == {
        "code": "required",
        "detail": "This field is required.",
        "attr": field,
    }


def test_a_verified_address_gets_the_same_answer_and_its_owner_an_email(
    api_client: APIClient,
) -> None:
    fresh = register(api_client, email="new@example.com")
    owner = VerifiedUserFactory.create(email="sara@example.com", name="Original")
    original_hash = owner.password
    mail.outbox.clear()

    response = register(api_client)

    assert response.status_code == 201
    assert response.json() == {**fresh.json(), "email": "sara@example.com"}
    # The owner is told, with no code in it; nothing about the account changes.
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["sara@example.com"]
    assert "already have" in str(mail.outbox[0].body)
    assert not re.search(r"\b\d{6}\b", str(mail.outbox[0].body))
    owner.refresh_from_db()
    assert (owner.name, owner.password) == ("Original", original_hash)
    assert EmailCode.objects.filter(user=owner).count() == 0


def test_the_notice_is_in_the_owners_language(api_client: APIClient) -> None:
    VerifiedUserFactory.create(email="sara@example.com", language="ar")

    register(api_client, language="en")

    assert "حاول شخص" in str(mail.outbox[0].subject)


def test_a_verified_address_is_limited_exactly_like_a_new_one(
    api_client: APIClient, clock: Clock
) -> None:
    VerifiedUserFactory.create(email="sara@example.com")

    first = register(api_client)
    second = register(api_client)

    assert first.status_code == 201
    assert second.status_code == 429
    assert first_error(second)["code"] == "resend_too_soon"
    assert len(mail.outbox) == 1  # the refused attempt sends nothing


def test_the_password_rules_apply_to_a_verified_address_too(
    api_client: APIClient,
) -> None:
    VerifiedUserFactory.create(email="sara@example.com")

    response = register(api_client, password="short")

    assert response.status_code == 400
    assert mail.outbox == []


def test_an_unverified_address_is_registered_again_and_the_new_details_win(
    api_client: APIClient, clock: Clock
) -> None:
    register(
        api_client, name="Earlier Attempt", password="an-earlier-pass-1", language="en"
    )
    old_code = emailed_code()
    clock.advance(seconds=61)

    response = register(
        api_client, name="The Real Owner", password="the-owners-pass-2", language="ar"
    )

    assert response.status_code == 201
    user = User.objects.get()
    assert user.name == "The Real Owner"
    assert user.language == "ar"
    assert user.check_password("the-owners-pass-2")
    assert not user.check_password("an-earlier-pass-1")
    assert EmailCode.objects.count() == 1
    assert len(mail.outbox) == 2
    new_code = emailed_code()
    # The old code is dead unless it happens to equal the new one.
    verify = api_client.post(
        f"{AUTH}/verify-email/",
        {
            "email": "sara@example.com",
            "code": old_code,
            "device": {
                "device_id": "0b8f7c1e-6d2a-4c53-9a3e-1f2d4b5c6a7d",
                "device_name": "Pixel 8",
                "platform": "android",
                "app_version": "1",
            },
        },
    )
    assert (verify.status_code == 200) == (old_code == new_code)


def test_registering_again_inside_the_cooldown_is_too_soon(
    api_client: APIClient, clock: Clock
) -> None:
    register(api_client)
    clock.advance(seconds=20)

    response = register(api_client)

    assert response.status_code == 429
    assert first_error(response)["code"] == "resend_too_soon"
    assert response["Retry-After"] == "40"
    assert len(mail.outbox) == 1


def test_the_hourly_limit_stops_a_sixth_code(
    api_client: APIClient, clock: Clock
) -> None:
    for _ in range(5):
        assert register(api_client).status_code == 201
        clock.advance(seconds=61)

    response = register(api_client)

    assert response.status_code == 429
    assert first_error(response)["code"] == "resend_limit_reached"
    assert int(response["Retry-After"]) > 60
    assert len(mail.outbox) == 5


def test_a_client_address_is_throttled(
    api_client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(RegisterThrottle, "THROTTLE_RATES", {"register": "2/hour"})

    statuses = [
        register(api_client, email=f"person{n}@example.com").status_code
        for n in range(3)
    ]

    assert statuses == [201, 201, 429]


def test_the_throttled_answer_names_its_code_and_waits(
    api_client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(RegisterThrottle, "THROTTLE_RATES", {"register": "1/hour"})
    register(api_client)

    response = register(api_client, email="other@example.com")

    assert response.status_code == 429
    assert first_error(response)["code"] == "throttled"
    assert int(response["Retry-After"]) > 0


def test_an_unverified_account_that_exists_is_untouched_by_a_failed_registration(
    api_client: APIClient,
) -> None:
    existing = UserFactory.create(email="sara@example.com", name="Pending")

    register(api_client, password="short")

    existing.refresh_from_db()
    assert existing.name == "Pending"


def test_a_too_short_password_carries_the_minimum(api_client: APIClient) -> None:
    response = register(api_client, password="a" * 14)

    assert first_error(response) == {
        "code": "password_too_short",
        "detail": "This password is too short. It must contain at least 15 characters.",
        "attr": "password",
        "min_length": 15,
    }


def test_a_too_long_password_carries_the_maximum(api_client: APIClient) -> None:
    response = register(api_client, password="a" * 129)

    assert first_error(response) == {
        "code": "password_too_long",
        "detail": "The password must be at most 128 characters.",
        "attr": "password",
        "max_length": 128,
    }


def test_the_limits_come_from_the_settings_so_a_policy_change_needs_no_app_release(
    api_client: APIClient, settings: Any
) -> None:
    settings.PASSWORD_MIN_LENGTH = 20

    response = register(api_client, password="a" * 14)

    assert first_error(response)["min_length"] == 20


@pytest.mark.parametrize(
    ("password", "code"),
    [
        ("1q2w3e4r5t6y7u8i9o0p", "password_too_common"),
        ("sara@example.com!!!!", "password_too_similar"),
    ],
)
def test_other_password_errors_carry_no_limit(
    api_client: APIClient, password: str, code: str
) -> None:
    error = first_error(register(api_client, password=password))

    assert error["code"] == code
    assert set(error) == {"code", "detail", "attr"}


def test_a_field_that_is_too_long_carries_no_password_limit(
    api_client: APIClient,
) -> None:
    error = first_error(register(api_client, name="x" * 101))

    assert error["code"] == "max_length"
    assert "max_length" not in error
