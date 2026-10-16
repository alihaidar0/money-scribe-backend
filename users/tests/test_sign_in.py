from typing import Any

import pytest
from django.core.cache import cache
from rest_framework.response import Response
from rest_framework.test import APIClient

from users import tokens
from users.models import Session, User
from users.tests.factories import UserFactory, VerifiedUserFactory
from users.tests.helpers import AUTH, PASSWORD, bearer, device, first_error, sign_in
from users.throttles import SignInThrottle

pytestmark = pytest.mark.django_db

EMAIL = "sara@example.com"


def wrong(client: APIClient, email: str = EMAIL) -> Response:
    return sign_in(client, email, "not-the-password-9")


def test_the_right_credentials_start_a_session(api_client: APIClient) -> None:
    user = VerifiedUserFactory.create(email=EMAIL, name="Sara Example")

    response = sign_in(api_client, EMAIL)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "access_token",
        "refresh_token",
        "token_type",
        "expires_in",
        "user",
    }
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 600
    assert body["user"] == {"id": str(user.id), "name": "Sara Example", "email": EMAIL}
    assert response["Cache-Control"] == "no-store"
    session = Session.objects.get()
    assert session.user == user
    assert tokens.decode_access_token(body["access_token"]).session_id == session.id
    assert tokens.decode_refresh_token(body["refresh_token"]).generation == 0
    user.refresh_from_db()
    assert user.last_login is not None


def test_the_stored_refresh_state_matches_the_token_that_was_handed_out(
    api_client: APIClient,
) -> None:
    VerifiedUserFactory.create(email=EMAIL)

    body = sign_in(api_client, EMAIL).json()

    assert Session.objects.get().refresh_hash == tokens.hash_token(
        body["refresh_token"]
    )


def test_the_email_is_matched_without_case_or_padding(api_client: APIClient) -> None:
    VerifiedUserFactory.create(email=EMAIL)

    assert sign_in(api_client, "  SARA@Example.com ").status_code == 200


def test_the_signing_device_sets_the_language_and_time_zone(
    api_client: APIClient,
) -> None:
    VerifiedUserFactory.create(email=EMAIL, language="en", time_zone="UTC")

    sign_in(api_client, EMAIL, language="ar", time_zone="Asia/Riyadh")

    user = User.objects.get()
    assert (user.language, user.time_zone) == ("ar", "Asia/Riyadh")


def test_leaving_them_out_keeps_the_stored_values(api_client: APIClient) -> None:
    VerifiedUserFactory.create(email=EMAIL, language="ar", time_zone="Asia/Riyadh")

    sign_in(api_client, EMAIL)

    user = User.objects.get()
    assert (user.language, user.time_zone) == ("ar", "Asia/Riyadh")


def test_every_sign_in_is_its_own_session(api_client: APIClient) -> None:
    VerifiedUserFactory.create(email=EMAIL)

    sign_in(api_client, EMAIL)
    api_client.post(
        f"{AUTH}/sign-in/",
        {"email": EMAIL, "password": PASSWORD, "device": device(device_name="Pixel 9")},
    )

    assert Session.objects.count() == 2


def test_a_wrong_password_is_a_401_with_the_bearer_challenge(
    api_client: APIClient,
) -> None:
    VerifiedUserFactory.create(email=EMAIL)

    response = wrong(api_client)

    assert response.status_code == 401
    assert response["WWW-Authenticate"] == "Bearer"
    assert response.json() == {
        "type": "client_error",
        "errors": [
            {
                "code": "invalid_credentials",
                "detail": "The email or password is wrong.",
                "attr": None,
            }
        ],
    }
    assert Session.objects.count() == 0


def test_an_unknown_email_looks_exactly_like_a_wrong_password(
    api_client: APIClient,
) -> None:
    VerifiedUserFactory.create(email=EMAIL)

    known = wrong(api_client)
    unknown = wrong(api_client, "nobody@example.com")

    assert unknown.status_code == known.status_code == 401
    assert unknown.json() == known.json()


def test_an_inactive_account_cannot_sign_in(api_client: APIClient) -> None:
    VerifiedUserFactory.create(email=EMAIL, is_active=False)

    response = sign_in(api_client, EMAIL)

    assert response.status_code == 401
    assert first_error(response)["code"] == "invalid_credentials"


def test_an_unverified_account_with_the_right_password_is_told_to_verify(
    api_client: APIClient,
) -> None:
    UserFactory.create(email=EMAIL)

    response = sign_in(api_client, EMAIL)

    assert response.status_code == 403
    assert first_error(response)["code"] == "email_not_verified"
    assert Session.objects.count() == 0


def test_an_unverified_account_with_a_wrong_password_learns_nothing(
    api_client: APIClient,
) -> None:
    UserFactory.create(email=EMAIL)

    response = wrong(api_client)

    assert response.status_code == 401
    assert first_error(response)["code"] == "invalid_credentials"


def test_email_not_verified_does_not_count_towards_the_lock(
    api_client: APIClient,
) -> None:
    UserFactory.create(email=EMAIL)

    statuses = {sign_in(api_client, EMAIL).status_code for _ in range(8)}

    assert statuses == {403}


def test_the_fifth_failure_locks_the_account_and_answers_429(
    api_client: APIClient,
) -> None:
    VerifiedUserFactory.create(email=EMAIL)

    statuses = [wrong(api_client).status_code for _ in range(4)]
    fifth = wrong(api_client)

    assert statuses == [401, 401, 401, 401]
    assert fifth.status_code == 429
    assert first_error(fifth)["code"] == "sign_in_locked"
    assert fifth["Retry-After"] == "900"


def test_the_right_password_is_refused_while_the_account_is_locked(
    api_client: APIClient,
) -> None:
    VerifiedUserFactory.create(email=EMAIL)
    for _ in range(5):
        wrong(api_client)

    response = sign_in(api_client, EMAIL)

    assert response.status_code == 429
    assert first_error(response)["code"] == "sign_in_locked"
    assert 1 <= int(response["Retry-After"]) <= 900
    assert Session.objects.count() == 0


def test_a_lock_on_one_account_leaves_the_others_alone(api_client: APIClient) -> None:
    VerifiedUserFactory.create(email=EMAIL)
    VerifiedUserFactory.create(email="other@example.com")
    for _ in range(5):
        wrong(api_client)

    assert sign_in(api_client, "other@example.com").status_code == 200


def test_an_unknown_email_is_counted_and_locked_the_same_way(
    api_client: APIClient,
) -> None:
    statuses = [wrong(api_client, "nobody@example.com").status_code for _ in range(6)]

    assert statuses == [401, 401, 401, 401, 429, 429]


def test_a_success_resets_the_count(api_client: APIClient) -> None:
    VerifiedUserFactory.create(email=EMAIL)
    for _ in range(4):
        wrong(api_client)
    assert sign_in(api_client, EMAIL).status_code == 200

    statuses = [wrong(api_client).status_code for _ in range(4)]

    assert statuses == [401, 401, 401, 401]


def test_the_lock_ends_when_its_time_is_up(api_client: APIClient) -> None:
    VerifiedUserFactory.create(email=EMAIL)
    for _ in range(5):
        wrong(api_client)
    cache.clear()  # what the cache does by itself after the lock's 15 minutes

    assert sign_in(api_client, EMAIL).status_code == 200


def test_a_stale_token_in_the_header_cannot_break_a_sign_in(
    api_client: APIClient,
) -> None:
    VerifiedUserFactory.create(email=EMAIL)
    body: dict[str, Any] = {"email": EMAIL, "password": PASSWORD, "device": device()}

    response = api_client.post(
        f"{AUTH}/sign-in/", body, headers=bearer("an-expired-or-garbage-token")
    )

    assert response.status_code == 200


def test_a_client_address_is_throttled(
    api_client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(SignInThrottle, "THROTTLE_RATES", {"sign_in": "3/min"})
    VerifiedUserFactory.create(email=EMAIL)

    responses = [wrong(api_client, f"person{n}@example.com") for n in range(4)]

    assert [response.status_code for response in responses] == [401, 401, 401, 429]
    assert first_error(responses[3])["code"] == "throttled"
    assert int(responses[3]["Retry-After"]) >= 1


def test_the_session_keeps_only_the_network_of_the_address(
    api_client: APIClient,
) -> None:
    VerifiedUserFactory.create(email=EMAIL)

    api_client.post(
        f"{AUTH}/sign-in/",
        {"email": EMAIL, "password": PASSWORD, "device": device()},
        REMOTE_ADDR="198.51.100.200",
    )

    assert Session.objects.get().ip == "198.51.100.0"


@pytest.mark.parametrize(
    "overrides",
    [
        {"email": "nope"},
        {"email": ""},
        {"password": ""},
        {"time_zone": "Mars/Olympus"},
        {"language": "fr"},
    ],
)
def test_malformed_input_is_a_400(
    api_client: APIClient, overrides: dict[str, str]
) -> None:
    VerifiedUserFactory.create(email=EMAIL)
    body: dict[str, Any] = {
        "email": EMAIL,
        "password": PASSWORD,
        "device": device(),
        **overrides,
    }

    response = api_client.post(f"{AUTH}/sign-in/", body)

    assert response.status_code == 400
    assert response.json()["type"] == "validation_error"


def test_the_password_is_not_trimmed(api_client: APIClient) -> None:
    VerifiedUserFactory.create(email=EMAIL)

    assert sign_in(api_client, EMAIL, f" {PASSWORD} ").status_code == 401


def attempt(
    client: APIClient, password: str, address: str, email: str = EMAIL
) -> Response:
    return client.post(
        f"{AUTH}/sign-in/",
        {"email": email, "password": password, "device": device()},
        REMOTE_ADDR=address,
    )


def test_someone_elsewhere_cannot_lock_the_owner_out(api_client: APIClient) -> None:
    VerifiedUserFactory.create(email=EMAIL)

    attacker = [
        attempt(api_client, "wrong-password-0", "198.51.100.9").status_code
        for _ in range(6)
    ]
    owner = attempt(api_client, PASSWORD, "203.0.113.1")

    assert attacker == [401, 401, 401, 401, 429, 429]
    assert owner.status_code == 200


def test_the_attacker_stays_locked_even_with_the_right_password(
    api_client: APIClient,
) -> None:
    VerifiedUserFactory.create(email=EMAIL)
    for _ in range(5):
        attempt(api_client, "wrong-password-0", "198.51.100.9")

    response = attempt(api_client, PASSWORD, "198.51.100.9")

    assert response.status_code == 429
    assert first_error(response)["code"] == "sign_in_locked"


def test_a_guess_spread_over_many_addresses_locks_the_account_for_everyone(
    api_client: APIClient,
) -> None:
    VerifiedUserFactory.create(email=EMAIL)

    statuses = [
        attempt(api_client, "wrong-password-0", f"198.51.100.{n}").status_code
        for n in range(50)
    ]
    owner = attempt(api_client, PASSWORD, "203.0.113.1")

    assert statuses[:49] == [401] * 49
    assert statuses[49] == 429
    assert owner.status_code == 429
    assert first_error(owner)["code"] == "sign_in_locked"


def test_every_ipv6_address_of_one_network_is_one_client(api_client: APIClient) -> None:
    """Choosing another address inside the same /64 does not start the count again."""
    VerifiedUserFactory.create(email=EMAIL)

    same_network = [
        attempt(api_client, "wrong-password-0", f"2001:db8:1:2::{n:x}").status_code
        for n in range(1, 7)
    ]
    other_network = attempt(api_client, PASSWORD, "2001:db8:1:3::1")

    assert same_network == [401, 401, 401, 401, 429, 429]
    assert other_network.status_code == 200
