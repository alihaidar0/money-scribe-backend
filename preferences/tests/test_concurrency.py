"""Two devices change the settings at once: one winner, and the language moves with the row it belongs to."""

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, NamedTuple

import pytest
from django.db import connections
from rest_framework.test import APIClient

from preferences import services
from preferences.models import Preferences
from users.models import User
from users.tests.helpers import bearer, start_session

pytestmark = pytest.mark.django_db(transaction=True)

URL = "/api/v1/settings/"


class Answer(NamedTuple):
    status: int
    body: Any


def patch(token: str, body: dict[str, Any], **headers: str) -> Answer:
    client = APIClient()
    client.raise_request_exception = False
    try:
        response = client.patch(URL, body, headers={**bearer(token), **headers})
        return Answer(response.status_code, response.json())
    finally:
        connections.close_all()


def test_two_changes_from_the_same_version_have_one_winner_and_the_loser_changes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, body = start_session(APIClient())
    token = str(body["access_token"])
    APIClient().get(URL, headers=bearer(token))
    original = services.apply

    def slow(*args: Any, **kwargs: Any) -> Preferences:
        time.sleep(0.5)
        result: Preferences = original(*args, **kwargs)
        return result

    monkeypatch.setattr(services, "apply", slow)
    from_one = {"If-Match": '"1"'}

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(
            patch, token, {"language": "ar", "first_pay_day": 11}, **from_one
        )
        time.sleep(0.15)
        second = pool.submit(
            patch, token, {"language": "en", "first_pay_day": 12}, **from_one
        )
        answers = [first.result(), second.result()]

    assert [a.status for a in answers] == [200, 412]
    user = User.objects.get(pk=user.pk)
    assert user.language == "ar"
    assert Preferences.objects.get().first_pay_day == 11
    assert Preferences.objects.get().version == 2
