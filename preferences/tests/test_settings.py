"""The Settings: read, change, reset, the version check, and what they do to the rest (docs/api-fields.md)."""

from typing import Any

import pytest
from django.db import IntegrityError
from rest_framework.test import APIClient

from common.money import Currency
from preferences import services
from preferences.models import Preferences
from users.models import User
from users.tests.helpers import bearer, first_error, start_session

pytestmark = pytest.mark.django_db

URL = "/api/v1/settings/"
DEFAULTS = {
    "language": "en",
    "time_zone": "UTC",
    "default_currency": "RUB",
    "payment_mode": "one_payment",
    "first_pay_day": 20,
    "second_pay_day": 5,
    "remind_on_pay_days": False,
}
EVERYTHING = {
    "language": "ar",
    "time_zone": "Asia/Riyadh",
    "default_currency": "SAR",
    "payment_mode": "two_payments",
    "first_pay_day": 15,
    "second_pay_day": 2,
    "remind_on_pay_days": True,
}


def read(client: APIClient, token: str) -> Any:
    return client.get(URL, headers=bearer(token))


def change(client: APIClient, token: str, body: dict[str, Any], **headers: str) -> Any:
    return client.patch(URL, body, headers={**bearer(token), **headers})


def shown(response: Any) -> dict[str, Any]:
    body = response.json()
    return {key: body[key] for key in DEFAULTS}


# --- reading ----------------------------------------------------------------------------------


def test_a_new_user_gets_the_apps_own_defaults_and_a_version(
    client: APIClient, account: tuple[User, str]
) -> None:
    response = read(client, account[1])

    assert response.status_code == 200
    assert shown(response) == DEFAULTS
    assert response.json()["version"] == 1
    assert response["ETag"] == '"1"'
    assert response.json()["updated_at"].endswith("Z")


def test_reading_twice_makes_one_row_and_changes_nothing(
    client: APIClient, account: tuple[User, str]
) -> None:
    first = read(client, account[1]).json()
    second = read(client, account[1]).json()

    assert first == second
    assert Preferences.objects.count() == 1


def test_the_language_and_the_zone_shown_are_the_ones_of_the_user(
    client: APIClient, account: tuple[User, str]
) -> None:
    User.objects.filter(pk=account[0].pk).update(
        language="ar", time_zone="Europe/Moscow"
    )

    body = read(client, account[1]).json()

    assert (body["language"], body["time_zone"]) == ("ar", "Europe/Moscow")


# --- changing ---------------------------------------------------------------------------------


def test_every_setting_can_be_changed_at_once(
    client: APIClient, account: tuple[User, str]
) -> None:
    response = change(client, account[1], EVERYTHING)

    assert response.status_code == 200
    assert shown(response) == EVERYTHING
    assert response.json()["version"] == 2
    assert response["ETag"] == '"2"'
    assert shown(read(client, account[1])) == EVERYTHING


def test_only_the_fields_sent_change(
    client: APIClient, account: tuple[User, str]
) -> None:
    change(client, account[1], EVERYTHING)

    response = change(client, account[1], {"first_pay_day": 10})

    assert shown(response) == {**EVERYTHING, "first_pay_day": 10}


def test_the_language_and_the_zone_go_to_the_user_and_are_seen_by_the_profile(
    client: APIClient, account: tuple[User, str]
) -> None:
    change(client, account[1], {"language": "ar", "time_zone": "Asia/Riyadh"})

    account[0].refresh_from_db()
    profile = client.get("/api/v1/auth/profile/", headers=bearer(account[1])).json()
    assert (account[0].language, account[0].time_zone) == ("ar", "Asia/Riyadh")
    assert (profile["language"], profile["time_zone"]) == ("ar", "Asia/Riyadh")


def test_nothing_sent_changes_nothing_not_even_the_version(
    client: APIClient, account: tuple[User, str]
) -> None:
    read(client, account[1])

    response = change(client, account[1], {})

    assert response.status_code == 200
    assert response.json()["version"] == 1


def test_a_change_on_the_first_call_makes_the_row(
    client: APIClient, account: tuple[User, str]
) -> None:
    response = change(client, account[1], {"remind_on_pay_days": True})

    assert response.status_code == 200
    assert response.json()["remind_on_pay_days"] is True
    assert Preferences.objects.count() == 1


# --- resetting --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "default"),
    [
        ("default_currency", "RUB"),
        ("payment_mode", "one_payment"),
        ("first_pay_day", 20),
        ("second_pay_day", 5),
    ],
)
def test_null_goes_back_to_not_set_and_the_apps_default_shows(
    client: APIClient, account: tuple[User, str], field: str, default: Any
) -> None:
    change(client, account[1], EVERYTHING)

    response = change(client, account[1], {field: None})

    assert response.json()[field] == default
    stored = Preferences.objects.get()
    assert getattr(stored, field) in ("", None)


@pytest.mark.parametrize("field", ["language", "time_zone", "remind_on_pay_days"])
def test_these_cannot_be_cleared(
    client: APIClient, account: tuple[User, str], field: str
) -> None:
    response = change(client, account[1], {field: None})

    assert response.status_code == 400
    assert (first_error(response)["attr"], first_error(response)["code"]) == (
        field,
        "null",
    )


# --- validation -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("language", "fr", "invalid_choice"),
        ("language", "EN", "invalid_choice"),
        ("time_zone", "Mars/Olympus", "invalid"),
        ("time_zone", "", "blank"),
        ("time_zone", "x" * 65, "max_length"),
        ("default_currency", "XXX", "invalid_choice"),
        ("default_currency", "rub", "invalid_choice"),
        ("payment_mode", "three_payments", "invalid_choice"),
        ("first_pay_day", 0, "min_value"),
        ("first_pay_day", 29, "max_value"),
        ("second_pay_day", 29, "max_value"),
        ("first_pay_day", "20", "invalid"),
        ("first_pay_day", 20.0, "invalid"),
        ("first_pay_day", True, "invalid"),
        ("remind_on_pay_days", "true", "invalid"),
        ("remind_on_pay_days", 1, "invalid"),
    ],
)
def test_a_wrong_value_is_a_400_with_its_own_code_and_changes_nothing(
    client: APIClient, account: tuple[User, str], field: str, value: Any, code: str
) -> None:
    before = shown(read(client, account[1]))

    response = change(client, account[1], {field: value})

    assert response.status_code == 400
    assert (first_error(response)["attr"], first_error(response)["code"]) == (
        field,
        code,
    )
    assert shown(read(client, account[1])) == before


@pytest.mark.parametrize(
    "field", ["version", "id", "updated_at", "user", "theme", "mode"]
)
def test_a_field_that_is_not_a_setting_is_read_only(
    client: APIClient, account: tuple[User, str], field: str
) -> None:
    response = change(client, account[1], {field: 5})

    assert response.status_code == 400
    assert (first_error(response)["attr"], first_error(response)["code"]) == (
        field,
        "read_only",
    )


def test_a_bad_field_stops_the_good_ones_beside_it(
    client: APIClient, account: tuple[User, str]
) -> None:
    response = change(client, account[1], {"language": "ar", "first_pay_day": 99})

    assert response.status_code == 400
    account[0].refresh_from_db()
    assert account[0].language == "en"


# --- the version ------------------------------------------------------------------------------


def test_a_stale_if_match_is_a_412_and_changes_nothing_not_even_the_language(
    client: APIClient, account: tuple[User, str]
) -> None:
    change(client, account[1], {"first_pay_day": 11})

    stale = change(
        client,
        account[1],
        {"language": "ar", "first_pay_day": 12},
        **{"If-Match": '"1"'},
    )

    assert stale.status_code == 412
    assert first_error(stale)["code"] == "precondition_failed"
    account[0].refresh_from_db()
    assert account[0].language == "en"
    assert Preferences.objects.get().first_pay_day == 11


def test_the_current_version_or_a_star_is_accepted(
    client: APIClient, account: tuple[User, str]
) -> None:
    read(client, account[1])

    assert (
        change(
            client, account[1], {"first_pay_day": 1}, **{"If-Match": '"1"'}
        ).status_code
        == 200
    )
    assert (
        change(
            client, account[1], {"first_pay_day": 2}, **{"If-Match": "*"}
        ).status_code
        == 200
    )
    assert change(client, account[1], {"first_pay_day": 3}).json()["version"] == 4


# --- other users, tokens, methods -------------------------------------------------------------


def test_each_user_has_their_own_settings(
    client: APIClient, account: tuple[User, str]
) -> None:
    change(client, account[1], EVERYTHING)
    stranger = APIClient()
    _, body = start_session(stranger)

    theirs = read(stranger, body["access_token"])

    assert shown(theirs) == DEFAULTS
    assert Preferences.objects.count() == 2


def test_the_settings_need_a_token(client: APIClient) -> None:
    assert client.get(URL).status_code == 401
    assert client.patch(URL, {}).status_code == 401


@pytest.mark.parametrize("method", ["put", "post", "delete"])
def test_only_reading_and_changing_exist(
    client: APIClient, account: tuple[User, str], method: str
) -> None:
    assert (
        getattr(client, method)(URL, {}, headers=bearer(account[1])).status_code == 405
    )


def test_deleting_the_account_deletes_the_settings(
    client: APIClient, account: tuple[User, str]
) -> None:
    change(client, account[1], EVERYTHING)

    account[0].delete()

    assert Preferences.objects.count() == 0


# --- what the salary defaults read, and the database ------------------------------------------


def test_what_the_person_set_is_told_apart_from_what_was_never_set(
    client: APIClient, account: tuple[User, str]
) -> None:
    assert services.set_values(account[0]) == services.SetValues()
    assert Preferences.objects.count() == 0  # looking does not make a row

    change(client, account[1], {"default_currency": "USD", "first_pay_day": 7})

    values = services.set_values(account[0])
    assert values.default_currency is Currency.USD
    assert values.first_pay_day == 7
    assert values.payment_mode is None
    assert values.second_pay_day is None


@pytest.mark.parametrize(
    "changes",
    [
        {"default_currency": "XXX"},
        {"payment_mode": "weekly"},
        {"first_pay_day": 0},
        {"first_pay_day": 29},
        {"second_pay_day": 29},
    ],
    ids=str,
)
def test_the_database_refuses_what_the_api_would_(
    account: tuple[User, str], changes: dict[str, Any]
) -> None:
    with pytest.raises(IntegrityError):
        Preferences.objects.create(user=account[0], **changes)
