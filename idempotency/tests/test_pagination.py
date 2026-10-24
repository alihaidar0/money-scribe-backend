"""The keyset cursor class (ADR 0003): no row repeated or skipped, in every direction, with ties."""

import base64
import json
import uuid
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework.test import APIClient

from idempotency.tests.helpers import URL
from idempotency.tests.support import Note
from users.models import User
from users.tests.helpers import bearer, first_error, start_session

pytestmark = pytest.mark.django_db


@pytest.fixture
def account(client: APIClient) -> tuple[User, str]:
    user, body = start_session(client)
    return user, body["access_token"]


def fill(user: User, titles: list[str]) -> list[Note]:
    """Notes created one after another, so their ids rise in the order given."""
    return [Note.objects.create(owner_id=user.pk, title=title) for title in titles]


def get(client: APIClient, token: str, url: str = URL, **params: Any) -> Any:
    return client.get(url, params, headers=bearer(token))


def ids(page: Any) -> list[str]:
    return [item["id"] for item in page.json()["results"]]


def walk(
    client: APIClient, token: str, url: str = URL, **params: Any
) -> list[list[str]]:
    """Every page, following ``next`` from the first."""
    pages = []
    response = get(client, token, url, **params)
    while True:
        assert response.status_code == 200, response.json()
        pages.append(ids(response))
        link = response.json()["next"]
        if link is None:
            return pages
        response = client.get(link, headers=bearer(token))


# --- the envelope -----------------------------------------------------------------------------


def test_an_empty_list_is_200_with_empty_results_and_no_links(
    client: APIClient, account: tuple[User, str]
) -> None:
    response = get(client, account[1])

    assert response.status_code == 200
    assert response.json() == {"next": None, "previous": None, "results": []}


def test_a_short_list_is_one_page_with_no_links(
    client: APIClient, account: tuple[User, str]
) -> None:
    fill(account[0], ["a", "b", "c"])

    body = get(client, account[1]).json()

    assert len(body["results"]) == 3
    assert body["next"] is None
    assert body["previous"] is None


def test_the_default_page_is_50_and_the_maximum_100(
    client: APIClient, account: tuple[User, str]
) -> None:
    fill(account[0], ["a"] * 120)

    assert len(get(client, account[1]).json()["results"]) == 50
    assert len(get(client, account[1], limit=1000).json()["results"]) == 100
    assert len(get(client, account[1], limit=7).json()["results"]) == 7


@pytest.mark.parametrize("limit", ["0", "-1", "abc", "1.5", "", " 5", "٣"])
def test_a_limit_that_is_not_a_positive_whole_number_is_a_400(
    client: APIClient, account: tuple[User, str], limit: str
) -> None:
    response = get(client, account[1], limit=limit)

    assert response.status_code == 400
    assert first_error(response)["attr"] == "limit"


# --- walking forward --------------------------------------------------------------------------


def test_following_next_visits_every_row_once_in_the_declared_order(
    client: APIClient, account: tuple[User, str]
) -> None:
    fill(
        account[0], ["a"] * 12 + ["b"] * 12 + ["c"] * 1
    )  # many ties on the first field

    pages = walk(client, account[1], limit=10)

    expected = [str(n.id) for n in Note.objects.order_by("-title", "-id")]
    assert [len(p) for p in pages] == [10, 10, 5]
    assert [i for page in pages for i in page] == expected


def test_a_list_that_fills_the_last_page_exactly_has_no_empty_page_after_it(
    client: APIClient, account: tuple[User, str]
) -> None:
    fill(account[0], ["a"] * 20)

    pages = walk(client, account[1], limit=10)

    assert [len(p) for p in pages] == [10, 10]


@pytest.mark.parametrize(
    ("url", "ordering"),
    [
        ("/api/v1/notes-ascending/", ("title", "id")),
        ("/api/v1/notes-mixed/", ("title", "-id")),
        (URL, ("-title", "-id")),
    ],
    ids=["both ascending", "mixed", "both descending"],
)
def test_every_mix_of_directions_visits_every_row_once(
    client: APIClient, account: tuple[User, str], url: str, ordering: tuple[str, ...]
) -> None:
    fill(account[0], ["b", "a", "c", "a", "b", "a", "c", "c", "b", "a", "a", "b"])

    pages = walk(client, account[1], url, limit=4)

    expected = [str(n.id) for n in Note.objects.order_by(*ordering)]
    assert [i for page in pages for i in page] == expected


def test_the_links_keep_the_other_query_parameters(
    client: APIClient, account: tuple[User, str]
) -> None:
    fill(account[0], ["a"] * 5)

    link = get(client, account[1], limit=2).json()["next"]

    assert "limit=2" in link
    assert link.startswith("http://testserver/api/v1/notes/?")


# --- walking back -----------------------------------------------------------------------------


def test_following_previous_goes_back_over_the_same_pages(
    client: APIClient, account: tuple[User, str]
) -> None:
    fill(account[0], ["a"] * 25)
    forward = walk(client, account[1], limit=10)

    response = client.get(
        get(client, account[1], limit=10).json()["next"], headers=bearer(account[1])
    )
    last = client.get(response.json()["next"], headers=bearer(account[1]))
    back = [ids(last)]
    while last.json()["previous"] is not None:
        last = client.get(last.json()["previous"], headers=bearer(account[1]))
        back.append(ids(last))

    assert back == [forward[2], forward[1], forward[0]]
    assert last.json()["previous"] is None


def test_the_first_page_has_no_previous_and_the_second_has_one(
    client: APIClient, account: tuple[User, str]
) -> None:
    fill(account[0], ["a"] * 15)
    first = get(client, account[1], limit=10).json()

    second = client.get(first["next"], headers=bearer(account[1])).json()

    assert first["previous"] is None
    assert second["previous"] is not None
    assert second["next"] is None


# --- rows that change between two requests ----------------------------------------------------


def test_a_row_added_while_paging_is_neither_repeated_nor_does_it_hide_another(
    client: APIClient, account: tuple[User, str]
) -> None:
    rows = fill(account[0], ["a"] * 25)
    first = get(client, account[1], limit=10)
    seen = ids(first)

    fill(
        account[0], ["a"]
    )  # a newer row appears, which sorts before everything on the first page
    rest = []
    response = first
    while response.json()["next"] is not None:
        response = client.get(response.json()["next"], headers=bearer(account[1]))
        rest += ids(response)

    assert not set(seen) & set(rest)
    assert set(seen) | set(rest) == {str(r.id) for r in rows}


def test_a_row_deleted_while_paging_does_not_break_the_next_page(
    client: APIClient, account: tuple[User, str]
) -> None:
    rows = fill(account[0], ["a"] * 25)
    first = get(client, account[1], limit=10)
    last_seen = ids(first)[-1]
    Note.objects.filter(pk=last_seen).delete()  # the row the cursor points at is gone

    second = client.get(first.json()["next"], headers=bearer(account[1]))

    assert second.status_code == 200
    expected = [str(r.id) for r in sorted(rows, key=lambda n: n.id, reverse=True)][
        10:20
    ]
    assert ids(second) == expected


# --- a cursor that is not ours ----------------------------------------------------------------


def cursor(payload: Any) -> str:
    raw = json.dumps(payload).encode() if not isinstance(payload, bytes) else payload
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@pytest.mark.parametrize(
    "token",
    [
        "x",
        "!!!",
        "",
        cursor({"d": "n", "v": ["a"]}),  # one value for two fields
        cursor({"d": "x", "v": ["a", str(uuid.uuid4())]}),  # an unknown direction
        cursor({"d": "n", "v": ["a", "not-a-uuid"]}),  # a value of the wrong type
        cursor({"v": ["a", str(uuid.uuid4())]}),
        cursor([1, 2]),
        cursor(b"not json"),
        cursor({"d": "n", "v": None}),
        cursor({"d": "n", "v": [["a"], {"x": 1}]}),
    ],
)
def test_a_cursor_that_this_class_did_not_make_is_a_400_on_cursor(
    client: APIClient, account: tuple[User, str], token: str
) -> None:
    response = get(client, account[1], cursor=token)

    assert response.status_code == 400
    assert first_error(response)["attr"] == "cursor"


def test_another_users_cursor_shows_only_the_callers_own_rows(
    client: APIClient, account: tuple[User, str]
) -> None:
    fill(account[0], ["a"] * 15)
    link = get(client, account[1], limit=5).json()["next"]
    stranger = APIClient()
    other, body = start_session(stranger)
    mine = fill(other, ["a"] * 3)

    response = stranger.get(link, headers=bearer(body["access_token"]))

    assert response.status_code == 200
    assert set(ids(response)) <= {str(n.id) for n in mine}


def test_a_view_without_an_ordering_is_a_configuration_error(
    client: APIClient, account: tuple[User, str]
) -> None:
    client.raise_request_exception = True

    with pytest.raises(ImproperlyConfigured):
        get(client, account[1], "/api/v1/notes-unordered/")
