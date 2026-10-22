from typing import Any, cast

import pytest
from django.contrib.auth.hashers import check_password, identify_hasher, make_password
from django.core.checks import Error
from rest_framework.test import APIClient

from config.settings import base
from users.checks import check_signing_key
from users.tests.factories import VerifiedUserFactory
from users.tests.helpers import AUTH, PASSWORD, sign_in


def test_a_good_signing_key_passes_the_check() -> None:
    assert check_signing_key(None) == []


@pytest.mark.parametrize("key", ["", "too-short", "x" * 31])
def test_a_missing_or_short_signing_key_is_an_error(settings: object, key: str) -> None:
    settings.JWT_SIGNING_KEY = key  # type: ignore[attr-defined]

    [problem] = check_signing_key(None)

    assert isinstance(problem, Error)
    assert problem.id == "users.E001"


def test_a_32_byte_key_is_enough(settings: object) -> None:
    settings.JWT_SIGNING_KEY = "x" * 32  # type: ignore[attr-defined]

    assert check_signing_key(None) == []


def test_new_passwords_use_argon2id_and_old_hashes_still_verify() -> None:
    assert (
        base.PASSWORD_HASHERS[0] == "django.contrib.auth.hashers.Argon2PasswordHasher"
    )
    assert "django.contrib.auth.hashers.PBKDF2PasswordHasher" in base.PASSWORD_HASHERS


def test_argon2_hashes_are_argon2id(settings: object) -> None:
    settings.PASSWORD_HASHERS = base.PASSWORD_HASHERS  # type: ignore[attr-defined]

    encoded = make_password("a-fine-passphrase-7")

    assert encoded.startswith("argon2$argon2id$")
    assert identify_hasher(encoded).algorithm == "argon2"
    assert check_password("a-fine-passphrase-7", encoded)


@pytest.mark.django_db
def test_a_pbkdf2_hash_is_upgraded_to_argon2_at_the_next_sign_in(
    settings: object, api_client: APIClient
) -> None:
    settings.PASSWORD_HASHERS = base.PASSWORD_HASHERS  # type: ignore[attr-defined]
    user = VerifiedUserFactory.create()
    user.password = make_password(PASSWORD, hasher="pbkdf2_sha256")
    user.save()
    assert user.password.startswith("pbkdf2_sha256$")

    assert sign_in(api_client, user.email).status_code == 200

    user.refresh_from_db()
    assert user.password.startswith("argon2$")


@pytest.mark.django_db
def test_a_browser_may_read_retry_after(
    settings: object, api_client: APIClient
) -> None:
    settings.CORS_ALLOWED_ORIGINS = ["http://localhost:8080"]  # type: ignore[attr-defined]
    # a lock needs a real account
    VerifiedUserFactory.create(email="nobody@example.com")
    for _ in range(5):
        api_client.post(
            f"{AUTH}/sign-in/",
            {
                "email": "nobody@example.com",
                "password": "x",
                "device": {
                    "device_id": "0b8f7c1e-6d2a-4c53-9a3e-1f2d4b5c6a7d",
                    "device_name": "d",
                    "platform": "web",
                    "app_version": "1",
                },
            },
            headers={"Origin": "http://localhost:8080"},
        )

    response = api_client.post(
        f"{AUTH}/sign-in/",
        {
            "email": "nobody@example.com",
            "password": "x",
            "device": {
                "device_id": "0b8f7c1e-6d2a-4c53-9a3e-1f2d4b5c6a7d",
                "device_name": "d",
                "platform": "web",
                "app_version": "1",
            },
        },
        headers={"Origin": "http://localhost:8080"},
    )

    assert response.status_code == 429
    assert response["Access-Control-Allow-Origin"] == "http://localhost:8080"
    assert "Retry-After" in response["Access-Control-Expose-Headers"]


def test_the_password_policy_follows_nist_sp_800_63b_4() -> None:
    validators = cast("list[dict[str, Any]]", base.AUTH_PASSWORD_VALIDATORS)
    names = [validator["NAME"].rsplit(".", 1)[1] for validator in validators]
    options = {
        validator["NAME"].rsplit(".", 1)[1]: validator.get("OPTIONS")
        for validator in validators
    }

    # A single-factor password needs 15 characters; 128 are accepted; no composition rule exists.
    assert options["MinimumLengthValidator"] == {"min_length": 15}
    assert options["MaximumLengthPasswordValidator"] == {"max_length": 128}
    assert "CompromisedPasswordValidator" in names
    assert "CommonPasswordValidator" in names
    assert "UserAttributeSimilarityValidator" in names
    assert not [
        name
        for name in names
        if "Digit" in name or "Numeric" in name or "Symbol" in name
    ]


def test_the_email_code_has_six_digits_a_short_life_and_few_tries() -> None:
    assert base.EMAIL_CODE_LENGTH == 6
    assert base.EMAIL_CODE_LIFETIME_SECONDS == 600
    assert base.EMAIL_CODE_MAX_ATTEMPTS == 5
    assert base.EMAIL_CODE_SENDS_PER_HOUR == 5


def test_the_breach_check_is_on_by_default_and_off_in_tests(settings: object) -> None:
    assert base.PWNED_PASSWORD_CHECK is True
    assert settings.PWNED_PASSWORD_CHECK is False  # type: ignore[attr-defined]


def test_the_account_wide_lock_is_far_above_the_per_client_one() -> None:
    assert base.SIGN_IN_MAX_FAILURES == 5
    assert base.SIGN_IN_MAX_FAILURES < base.SIGN_IN_ACCOUNT_MAX_FAILURES <= 100
