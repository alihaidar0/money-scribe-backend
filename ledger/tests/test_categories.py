"""Categories: the built-in ones, and a person's own (ADR 0017; ``docs/api-fields.md``)."""

import uuid

import pytest
from rest_framework.test import APIClient

from config.tests.isolation import assert_hidden_from_other_users
from ledger.defaults import CUSTOM_ICONS
from ledger.models import MAX_CUSTOM_CATEGORIES, Category
from ledger.tests.helpers import (
    BUILT_IN_KEYS,
    CATEGORIES,
    add_category,
    category_url,
    delete,
    get,
    names,
    patch,
    post,
)
from users.models import User
from users.tests.helpers import bearer, first_error

pytestmark = pytest.mark.django_db

BUILT_IN = [
    ("groceries", "Groceries", "expense"),
    ("dining", "Dining", "expense"),
    ("transport", "Transport", "expense"),
    ("housing", "Housing", "expense"),
    ("utilities", "Utilities", "expense"),
    ("scholarship", "Scholarship", "income"),
    ("other", "Other", "both"),
]


def make(user: User, name: str, icon: str = "gifts") -> Category:
    return Category.objects.create(owner=user, name=name, icon=icon, kind="both")


def built_in(key: str) -> Category:
    return Category.objects.get(key=key, owner__isnull=True)


# --- the built-in categories ------------------------------------------------------------------


def test_the_seven_built_in_categories_exist_with_fixed_ids() -> None:
    rows = list(Category.objects.filter(owner__isnull=True).order_by("position"))

    assert [(c.key, c.name, c.kind) for c in rows] == BUILT_IN
    assert [c.icon for c in rows] == BUILT_IN_KEYS  # the icon of a built-in is its key
    assert [str(c.id) for c in rows] == [
        f"0198f2a0-0000-7000-8000-00000000000{n}" for n in range(1, 8)
    ]
    assert all(c.version == 1 and c.owner_id is None for c in rows)
    assert all(uuid.UUID(str(c.id)).version == 7 for c in rows)


def test_a_person_sees_the_built_in_categories_first_in_a_fixed_order(
    client: APIClient, me: tuple[User, str]
) -> None:
    make(me[0], "Pets")

    response = get(client, CATEGORIES, me[1], limit=100)

    body = response.json()
    assert response.status_code == 200
    assert body["next"] is None
    assert [c["key"] for c in body["results"]] == [*BUILT_IN_KEYS, None]
    first = body["results"][0]
    assert set(first) == {
        "id",
        "name",
        "icon",
        "kind",
        "is_custom",
        "key",
        "version",
        "created_at",
        "updated_at",
    }
    assert (first["name"], first["icon"], first["kind"], first["is_custom"]) == (
        "Groceries",
        "groceries",
        "expense",
        False,
    )
    pets = body["results"][-1]
    assert (
        pets["name"],
        pets["icon"],
        pets["kind"],
        pets["is_custom"],
        pets["key"],
    ) == (
        "Pets",
        "gifts",
        "both",
        True,
        None,
    )


def test_every_person_sees_the_same_built_in_rows(
    client: APIClient, me: tuple[User, str], other: tuple[User, str]
) -> None:
    mine = get(client, CATEGORIES, me[1]).json()["results"]
    theirs = get(client, CATEGORIES, other[1]).json()["results"]

    assert [c["id"] for c in mine] == [c["id"] for c in theirs]


def test_the_list_is_the_built_in_ones_then_the_persons_own_in_the_order_made(
    client: APIClient, me: tuple[User, str], other: tuple[User, str]
) -> None:
    make(me[0], "First")
    make(other[0], "Not mine")
    make(me[0], "Second")

    listed = names(get(client, CATEGORIES, me[1], limit=100))

    assert listed[7:] == ["First", "Second"]
    assert "Not mine" not in listed


def test_the_list_pages_across_the_two_groups_without_a_gap_or_a_repeat(
    client: APIClient, me: tuple[User, str]
) -> None:
    custom = [make(me[0], f"Own {n}") for n in range(4)]

    seen: list[str] = []
    page = get(client, CATEGORIES, me[1], limit=3)
    while True:
        assert page.status_code == 200
        seen += [item["id"] for item in page.json()["results"]]
        link = page.json()["next"]
        if link is None:
            break
        page = client.get(link, headers=bearer(me[1]))

    expected = [str(built_in(k).id) for k in BUILT_IN_KEYS] + [
        str(c.id) for c in custom
    ]
    assert seen == expected


def test_a_built_in_category_can_be_read(
    client: APIClient, me: tuple[User, str]
) -> None:
    response = get(client, category_url(built_in("scholarship").id), me[1])

    assert response.status_code == 200
    assert response["ETag"] == '"1"'
    assert response.json()["kind"] == "income"


@pytest.mark.parametrize("method", ["patch", "delete"])
def test_a_built_in_category_cannot_be_changed_or_deleted(
    client: APIClient, me: tuple[User, str], method: str
) -> None:
    category = built_in("dining")
    url = category_url(category.id)

    response = (
        patch(client, url, me[1], {"name": "Eating out"})
        if method == "patch"
        else delete(client, url, me[1])
    )

    assert response.status_code == 403
    error = first_error(response)
    assert (error["code"], error["attr"]) == ("category_is_builtin", None)
    category.refresh_from_db()
    assert (category.name, category.version) == ("Dining", 1)


def test_the_refusal_comes_before_the_version_check(
    client: APIClient, me: tuple[User, str]
) -> None:
    response = patch(
        client,
        category_url(built_in("dining").id),
        me[1],
        {"name": "x"},
        **{"If-Match": '"99"'},
    )

    assert response.status_code == 403


# --- adding a custom category -----------------------------------------------------------------


def test_a_custom_category_is_added_as_both_with_no_key(
    client: APIClient, me: tuple[User, str]
) -> None:
    response = add_category(client, me[1], name="  Pets  ", icon="gifts")

    body = response.json()
    assert response.status_code == 201
    assert response["ETag"] == '"1"'
    assert body["name"] == "Pets"
    assert (body["kind"], body["key"], body["is_custom"], body["version"]) == (
        "both",
        None,
        True,
        1,
    )
    assert Category.objects.get(pk=body["id"]).owner == me[0]


@pytest.mark.parametrize("icon", CUSTOM_ICONS)
def test_each_icon_of_the_picker_is_accepted(
    client: APIClient, me: tuple[User, str], icon: str
) -> None:
    assert add_category(client, me[1], name=f"For {icon}", icon=icon).status_code == 201


def test_the_picker_has_eight_icons() -> None:
    assert CUSTOM_ICONS == (
        "gifts",
        "health",
        "entertainment",
        "shopping",
        "education",
        "travel",
        "subscriptions",
        "other",
    )


@pytest.mark.parametrize(
    "icon", ["groceries", "dining", "scholarship", "pizza", "", "GIFTS"]
)
def test_an_icon_outside_the_picker_is_refused_on_write(
    client: APIClient, me: tuple[User, str], icon: str
) -> None:
    response = add_category(client, me[1], icon=icon)

    assert response.status_code == 400
    error = first_error(response)
    assert error["attr"] == "icon"
    assert error["code"] in {"invalid_choice", "blank"}
    assert not Category.objects.filter(owner=me[0]).exists()


def test_an_unknown_icon_already_stored_is_still_read(
    client: APIClient, me: tuple[User, str]
) -> None:
    """The response icon is a plain slug: a value the picker no longer offers is returned, never an error."""
    category = make(me[0], "Old", icon="old_icon_2")

    response = get(client, category_url(category.id), me[1])

    assert response.status_code == 200
    assert response.json()["icon"] == "old_icon_2"


@pytest.mark.parametrize(
    ("body", "code", "attr"),
    [
        ({"icon": "gifts"}, "required", "name"),
        ({"name": "Pets"}, "required", "icon"),
        ({"name": "", "icon": "gifts"}, "blank", "name"),
        ({"name": "x" * 31, "icon": "gifts"}, "max_length", "name"),
        ({"name": "Line\nbreak", "icon": "gifts"}, "invalid_characters", "name"),
    ],
)
def test_a_bad_field_is_a_400_on_that_field(
    client: APIClient, me: tuple[User, str], body: dict[str, str], code: str, attr: str
) -> None:
    response = post(client, CATEGORIES, me[1], body)

    assert response.status_code == 400
    error = first_error(response)
    assert (error["code"], error["attr"]) == (code, attr)


@pytest.mark.parametrize(
    "field", ["kind", "key", "is_custom", "id", "version", "owner", "foo"]
)
def test_a_field_that_cannot_be_written_is_400_read_only(
    client: APIClient, me: tuple[User, str], field: str
) -> None:
    response = post(
        client, CATEGORIES, me[1], {"name": "Pets", "icon": "gifts", field: "expense"}
    )

    assert response.status_code == 400
    error = first_error(response)
    assert (error["code"], error["attr"]) == ("read_only", field)
    assert not Category.objects.filter(owner=me[0]).exists()


def test_an_arabic_name_is_accepted(client: APIClient, me: tuple[User, str]) -> None:
    response = add_category(client, me[1], name="حيوانات أليفة")

    assert response.status_code == 201
    assert response.json()["name"] == "حيوانات أليفة"


def test_a_repeated_create_returns_the_first_answer_and_adds_nothing(
    client: APIClient, me: tuple[User, str]
) -> None:
    key = '"4f9d2c1e-7b3a-4d65-8e10-9a2b3c4d5e6f"'
    body = {"name": "Pets", "icon": "gifts"}

    first = post(client, CATEGORIES, me[1], body, key)
    again = post(client, CATEGORIES, me[1], body, key)

    assert (first.status_code, again.status_code) == (201, 201)
    assert again["Idempotent-Replayed"] == "true"
    assert again.json() == first.json()
    assert Category.objects.filter(owner=me[0]).count() == 1
    other_body = post(client, CATEGORIES, me[1], {"name": "Toys", "icon": "gifts"}, key)
    assert other_body.status_code == 422


def test_a_create_needs_an_idempotency_key(
    client: APIClient, me: tuple[User, str]
) -> None:
    response = client.post(
        CATEGORIES, {"name": "Pets", "icon": "gifts"}, headers=bearer(me[1])
    )

    assert first_error(response)["code"] == "idempotency_key_required"


# --- names ------------------------------------------------------------------------------------


@pytest.mark.parametrize("second", ["Pets", "pets", "PETS", "  Pets "])
def test_a_name_is_unique_among_the_persons_custom_categories_in_any_case(
    client: APIClient, me: tuple[User, str], second: str
) -> None:
    make(me[0], "Pets")

    response = add_category(client, me[1], name=second)

    assert response.status_code == 400
    error = first_error(response)
    assert (error["code"], error["attr"]) == ("name_taken", "name")


@pytest.mark.parametrize(
    "name",
    [
        "groceries",
        "Groceries",
        "GROCERIES",
        "dining",
        "Dining",
        "transport",
        "housing",
        "utilities",
        "scholarship",
        "other",
        "Other",
    ],
)
def test_a_custom_name_cannot_be_a_built_in_key_or_english_name(
    client: APIClient, me: tuple[User, str], name: str
) -> None:
    response = add_category(client, me[1], name=name)

    assert response.status_code == 400
    assert first_error(response)["code"] == "name_taken"


def test_two_people_can_use_the_same_custom_name(
    client: APIClient, me: tuple[User, str], other: tuple[User, str]
) -> None:
    assert add_category(client, me[1], name="Pets").status_code == 201
    assert add_category(client, other[1], name="Pets").status_code == 201


def test_the_database_refuses_a_name_taken_in_the_same_moment(
    client: APIClient, me: tuple[User, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    make(me[0], "Pets")
    monkeypatch.setattr(
        "ledger.services.ensure_category_name_free", lambda *a, **k: None
    )
    monkeypatch.setattr("ledger.services.category_name_is_taken", lambda *a, **k: False)

    response = add_category(client, me[1], name="pets")

    assert response.status_code == 400
    assert first_error(response)["code"] == "name_taken"


# --- the limit --------------------------------------------------------------------------------


def test_a_person_has_at_most_50_custom_categories_and_the_built_in_ones_do_not_count(
    client: APIClient, me: tuple[User, str], other: tuple[User, str]
) -> None:
    for index in range(MAX_CUSTOM_CATEGORIES):
        make(me[0], f"Own {index}")

    response = add_category(client, me[1], name="One too many")

    assert MAX_CUSTOM_CATEGORIES == 50
    assert response.status_code == 409
    error = first_error(response)
    assert (error["code"], error["attr"]) == ("category_limit_reached", None)
    assert Category.objects.filter(owner=me[0]).count() == 50
    assert add_category(client, other[1]).status_code == 201


# --- changing and deleting a custom category --------------------------------------------------


def test_a_custom_category_is_changed(client: APIClient, me: tuple[User, str]) -> None:
    category = make(me[0], "Pets")

    response = patch(
        client,
        category_url(category.id),
        me[1],
        {"name": "Animals", "icon": "health"},
        **{"If-Match": '"1"'},
    )

    assert response.status_code == 200
    assert response["ETag"] == '"2"'
    body = response.json()
    assert (body["name"], body["icon"], body["version"], body["kind"]) == (
        "Animals",
        "health",
        2,
        "both",
    )


def test_a_change_to_a_taken_name_or_an_unknown_icon_is_a_400(
    client: APIClient, me: tuple[User, str]
) -> None:
    make(me[0], "Pets")
    category = make(me[0], "Toys")

    taken = patch(client, category_url(category.id), me[1], {"name": "pets"})
    builtin_name = patch(client, category_url(category.id), me[1], {"name": "Dining"})
    bad_icon = patch(client, category_url(category.id), me[1], {"icon": "dining"})

    assert first_error(taken)["code"] == "name_taken"
    assert first_error(builtin_name)["code"] == "name_taken"
    assert first_error(bad_icon)["code"] == "invalid_choice"
    category.refresh_from_db()
    assert (category.name, category.version) == ("Toys", 1)


def test_a_change_of_the_kind_is_read_only(
    client: APIClient, me: tuple[User, str]
) -> None:
    category = make(me[0], "Pets")

    response = patch(client, category_url(category.id), me[1], {"kind": "expense"})

    assert first_error(response)["code"] == "read_only"
    category.refresh_from_db()
    assert category.kind == "both"


def test_a_change_that_changes_nothing_keeps_the_version(
    client: APIClient, me: tuple[User, str]
) -> None:
    category = make(me[0], "Pets")

    response = patch(client, category_url(category.id), me[1], {"name": "Pets"})

    assert response.json()["version"] == 1


def test_a_stale_change_is_412(client: APIClient, me: tuple[User, str]) -> None:
    category = make(me[0], "Pets")
    patch(client, category_url(category.id), me[1], {"name": "Animals"})

    stale = patch(
        client,
        category_url(category.id),
        me[1],
        {"name": "Toys"},
        **{"If-Match": '"1"'},
    )

    assert stale.status_code == 412
    category.refresh_from_db()
    assert category.name == "Animals"


def test_a_custom_category_is_deleted_and_a_repeat_is_404(
    client: APIClient, me: tuple[User, str]
) -> None:
    category = make(me[0], "Pets")

    first = delete(client, category_url(category.id), me[1])
    again = delete(client, category_url(category.id), me[1])

    assert first.status_code == 204
    assert first.content == b""
    assert again.status_code == 404
    assert first_error(again)["code"] == "not_found"
    assert not Category.objects.filter(pk=category.pk).exists()


def test_a_stale_delete_is_412(client: APIClient, me: tuple[User, str]) -> None:
    category = make(me[0], "Pets")
    patch(client, category_url(category.id), me[1], {"name": "Animals"})

    stale = delete(client, category_url(category.id), me[1], **{"If-Match": '"1"'})

    assert stale.status_code == 412
    assert Category.objects.filter(pk=category.pk).exists()


# --- ownership --------------------------------------------------------------------------------


def test_another_persons_custom_category_is_404_for_every_method(
    client: APIClient, me: tuple[User, str], other: tuple[User, str]
) -> None:
    theirs = make(other[0], "Theirs")
    mine = APIClient()
    mine.raise_request_exception = False
    mine.credentials(HTTP_AUTHORIZATION=f"Bearer {me[1]}")

    assert_hidden_from_other_users(
        mine, category_url(theirs.id), methods=("get", "patch", "delete")
    )

    theirs.refresh_from_db()
    assert (theirs.name, theirs.version) == ("Theirs", 1)


def test_every_operation_needs_a_signed_in_person(client: APIClient) -> None:
    some = "0198f2a0-0000-7000-8000-000000000001"

    assert client.get(CATEGORIES).status_code == 401
    assert client.post(CATEGORIES, {}).status_code == 401
    assert client.get(category_url(some)).status_code == 401
    assert client.patch(category_url(some), {}).status_code == 401
    assert client.delete(category_url(some)).status_code == 401


def test_put_is_not_offered(client: APIClient, me: tuple[User, str]) -> None:
    category = make(me[0], "Pets")

    response = client.put(
        category_url(category.id),
        {"name": "x", "icon": "gifts"},
        headers=bearer(me[1]),
    )

    assert response.status_code == 405
