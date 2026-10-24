"""What the OpenAPI document, the purge job, the system checks and the email promise for the device keys."""

from datetime import timedelta
from typing import Any

import pytest
from django.core import mail
from django.utils import timezone
from drf_spectacular.generators import SchemaGenerator
from rest_framework.test import APIClient

from users import checks as user_checks
from users.models import DeviceKey
from users.tasks import purge_expired_device_keys
from users.tests.device_key_helpers import KEYS, Phone
from users.tests.factories import VerifiedUserFactory
from users.tests.test_device_keys import enroll, open_account

KEY_PATHS = f"{KEYS}/"


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    # drf-spectacular is only partly typed
    generator = SchemaGenerator()  # type: ignore[no-untyped-call]
    document: dict[str, Any] = generator.get_schema(request=None, public=True)  # type: ignore[no-untyped-call]
    return document


def operation(schema: dict[str, Any], path: str, method: str) -> dict[str, Any]:
    found: dict[str, Any] = schema["paths"][path][method]
    return found


def test_the_operations_have_the_ids_the_client_calls_them_by(
    schema: dict[str, Any],
) -> None:
    ids = {
        (path.removeprefix(KEY_PATHS), method): body["operationId"]
        for path, methods in schema["paths"].items()
        if path.startswith(KEY_PATHS)
        for method, body in methods.items()
    }

    assert ids == {
        ("", "get"): "auth_device_key_list",
        ("challenge/", "post"): "auth_device_key_challenge",
        ("sign-in/", "post"): "auth_device_key_sign_in",
        ("current/", "put"): "auth_device_key_register",
        ("current/", "delete"): "auth_device_key_remove_current",
        ("{device_id}/", "delete"): "auth_device_key_remove",
    }


@pytest.mark.parametrize(
    ("path", "method"),
    [("challenge/", "post"), ("sign-in/", "post"), ("current/", "put")],
)
def test_the_three_calls_that_use_the_limit_store_declare_a_503(
    schema: dict[str, Any], path: str, method: str
) -> None:
    responses = operation(schema, KEY_PATHS + path, method)["responses"]

    assert responses["503"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ServerErrorResponse"
    }
    for status in ("429", "503"):
        assert responses[status]["headers"]["Retry-After"]["schema"] == {
            "type": "integer"
        }


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("", "get"),
        ("current/", "delete"),
        ("{device_id}/", "delete"),
    ],
)
def test_the_list_and_the_removals_use_no_store_so_declare_no_503(
    schema: dict[str, Any], path: str, method: str
) -> None:
    assert "503" not in operation(schema, KEY_PATHS + path, method)["responses"]


def test_the_public_calls_need_no_token_and_the_others_need_the_bearer(
    schema: dict[str, Any],
) -> None:
    for path, method in (("challenge/", "post"), ("sign-in/", "post")):
        assert "security" not in operation(schema, KEY_PATHS + path, method)
    for path, method in (
        ("", "get"),
        ("current/", "put"),
        ("current/", "delete"),
        ("{device_id}/", "delete"),
    ):
        assert operation(schema, KEY_PATHS + path, method)["security"] == [
            {"bearerAuth": []}
        ]


def test_the_registration_declares_201_200_and_the_409(schema: dict[str, Any]) -> None:
    responses = operation(schema, f"{KEYS}/current/", "put")["responses"]

    assert {"200", "201", "400", "401", "409", "429", "503"} <= responses.keys()


def test_the_sign_in_answers_the_body_of_sign_in_and_declares_a_401(
    schema: dict[str, Any],
) -> None:
    responses = operation(schema, f"{KEYS}/sign-in/", "post")["responses"]

    assert responses["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/SignedInResponse"
    }
    assert "401" in responses


def test_the_registry_lists_the_codes_of_the_device_keys(
    schema: dict[str, Any],
) -> None:
    description = schema["components"]["schemas"]["ErrorItem"]["properties"]["code"][
        "description"
    ]

    for code in (
        "device_sign_in_failed",
        "challenge_invalid",
        "public_key_invalid",
        "attestation_invalid",
        "platform_not_supported",
        "device_key_limit_reached",
    ):
        assert f"`{code}`" in description


# --- the purge --------------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_purge_deletes_the_keys_that_expired_and_keeps_the_rest(
    api_client: APIClient,
) -> None:
    account = open_account(api_client)
    enroll(api_client, account, Phone())
    DeviceKey.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    other = open_account(api_client, device_id="6f1d2c3b-4a59-4e8f-8b7a-0c9d1e2f3a4b")
    enroll(api_client, other, Phone())

    assert purge_expired_device_keys() == 1

    assert list(DeviceKey.objects.values_list("user_id", flat=True)) == [other.user.id]


def test_the_purge_runs_every_day() -> None:
    from celery.schedules import crontab
    from django.conf import settings

    entry = settings.CELERY_BEAT_SCHEDULE["users-purge-device-keys"]

    assert entry["task"] == "users.tasks.purge_expired_device_keys"
    assert entry["schedule"] == crontab(minute=57, hour=3)


# --- the system checks ------------------------------------------------------------------------


def test_skipping_the_attestation_is_refused_outside_development(settings: Any) -> None:
    settings.DEVICE_KEY_REQUIRE_ATTESTATION = False
    for environment in ("staging", "production"):
        settings.APP_ENVIRONMENT = environment

        assert [m.id for m in user_checks.check_device_key_attestation(None)] == [
            "users.E002"
        ]


def test_development_may_skip_it_and_every_other_environment_requires_it(
    settings: Any,
) -> None:
    settings.DEVICE_KEY_REQUIRE_ATTESTATION = False
    settings.APP_ENVIRONMENT = "development"
    assert user_checks.check_device_key_attestation(None) == []

    settings.DEVICE_KEY_REQUIRE_ATTESTATION = True
    settings.APP_ENVIRONMENT = "production"
    assert user_checks.check_device_key_attestation(None) == []


def test_a_deployment_without_the_app_warns(settings: Any) -> None:
    settings.APP_ENVIRONMENT = "staging"
    settings.DEVICE_KEY_REQUIRE_ATTESTATION = True
    settings.DEVICE_KEY_ANDROID_PACKAGE = ""
    settings.DEVICE_KEY_ANDROID_CERT_SHA256 = []

    assert [m.id for m in user_checks.check_device_key_app(None)] == ["users.W002"]

    settings.DEVICE_KEY_ANDROID_PACKAGE = "com.example.app"
    assert [m.id for m in user_checks.check_device_key_app(None)] == ["users.W002"]

    settings.DEVICE_KEY_ANDROID_CERT_SHA256 = ["ab" * 32]
    assert user_checks.check_device_key_app(None) == []


def test_development_and_a_skipped_attestation_need_no_app(settings: Any) -> None:
    settings.DEVICE_KEY_ANDROID_PACKAGE = ""
    settings.DEVICE_KEY_ANDROID_CERT_SHA256 = []
    settings.APP_ENVIRONMENT = "development"
    assert user_checks.check_device_key_app(None) == []

    settings.APP_ENVIRONMENT = "staging"
    settings.DEVICE_KEY_REQUIRE_ATTESTATION = False
    assert user_checks.check_device_key_app(None) == []


def test_the_limits_of_the_adr_are_the_settings() -> None:
    from django.conf import settings

    assert settings.DEVICE_KEY_LIFETIME_DAYS == 30
    assert settings.DEVICE_KEY_MAX_PER_USER == 5
    assert settings.DEVICE_KEY_CHALLENGE_LIFETIME_SECONDS == 120
    assert settings.DEVICE_KEY_SIGN_IN_MAX_FAILURES == 5
    assert settings.DEVICE_KEY_ADDRESS_MAX_FAILURES == 20


# --- the email --------------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_notice_is_in_the_language_of_the_account(api_client: APIClient) -> None:
    user = VerifiedUserFactory.create(language="ar")
    from users.tests.test_device_keys import sign_in_account

    account = sign_in_account(
        api_client, user, device_id="0b8f7c1e-6d2a-4c53-9a3e-1f2d4b5c6a7d"
    )
    mail.outbox.clear()

    enroll(api_client, account, Phone())

    assert (
        mail.outbox[0].subject == "تم تفعيل تسجيل الدخول ببصمة الإصبع في Money Scribe"
    )
    assert "Pixel 8" in mail.outbox[0].body
