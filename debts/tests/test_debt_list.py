"""The debt list: its order, its pages, its filters and the repayments it carries (ADR 0003)."""

from datetime import UTC, date, datetime
from typing import Any

import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.response import Response
from rest_framework.test import APIClient

from debts.models import Debt
from debts.tests.factories import DebtFactory, RepaymentFactory
from debts.tests.helpers import DEBTS, get
from users.models import User
from users.tests.helpers import bearer, first_error

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _today(clock: Any) -> None:
    """Every test runs on 2026-10-15."""


def make(me: tuple[User, str], **fields: Any) -> Debt:
    return DebtFactory.create(owner=me[0], **fields)


def listed(response: Response) -> list[str]:
    assert response.status_code == 200, response.json()
    return [item["id"] for item in response.json()["results"]]


def walk(client: APIClient, token: str, **params: Any) -> list[list[str]]:
    pages = []
    response = get(client, DEBTS, token, **params)
    while True:
        pages.append(listed(response))
        link = response.json()["next"]
        if link is None:
            return pages
        response = client.get(link, headers=bearer(token))


# --- the order and the pages ------------------------------------------------------------------


def test_an_empty_list_is_200_with_no_results(
    client: APIClient, me: tuple[User, str]
) -> None:
    response = get(client, DEBTS, me[1])

    assert response.status_code == 200
    assert response.json() == {"next": None, "previous": None, "results": []}


def test_a_head_is_a_get_without_a_body_and_takes_the_same_parameters(
    client: APIClient, me: tuple[User, str]
) -> None:
    make(me)

    assert client.head(DEBTS, headers=bearer(me[1])).status_code == 200
    bad = client.head(DEBTS, {"status": "paid"}, headers=bearer(me[1]))

    assert bad.status_code == 400


def test_the_newest_debt_comes_first(client: APIClient, me: tuple[User, str]) -> None:
    first = make(me)
    second = make(me)
    third = make(me)

    assert listed(get(client, DEBTS, me[1])) == [
        str(third.id),
        str(second.id),
        str(first.id),
    ]


def test_debts_made_at_the_same_instant_are_ordered_by_id_descending(
    client: APIClient, me: tuple[User, str]
) -> None:
    made = [make(me) for _ in range(4)]
    same = datetime(2026, 10, 1, tzinfo=UTC)
    Debt.objects.update(created_at=same)

    assert listed(get(client, DEBTS, me[1])) == [
        str(d.id) for d in sorted(made, key=lambda d: d.id, reverse=True)
    ]


def test_pages_follow_next_without_a_gap_or_a_repeat(
    client: APIClient, me: tuple[User, str]
) -> None:
    made = [make(me) for _ in range(5)]
    expected = [str(d.id) for d in reversed(made)]

    pages = walk(client, me[1], limit=2)

    assert [len(page) for page in pages] == [2, 2, 1]
    assert [i for page in pages for i in page] == expected


def test_a_debt_added_between_two_pages_does_not_shift_them(
    client: APIClient, me: tuple[User, str]
) -> None:
    made = [make(me) for _ in range(4)]
    first = get(client, DEBTS, me[1], limit=2)
    make(me)

    second = client.get(first.json()["next"], headers=bearer(me[1]))

    assert listed(second) == [str(made[1].id), str(made[0].id)]


def test_the_default_page_is_50_and_the_largest_is_100(
    client: APIClient, me: tuple[User, str]
) -> None:
    Debt.objects.bulk_create(
        [
            Debt(
                owner=me[0],
                direction="lent",
                person="Alex",
                amount=100,
                currency="USD",
                start_date=date(2026, 10, 1),
            )
            for _ in range(105)
        ]
    )

    assert len(get(client, DEBTS, me[1]).json()["results"]) == 50
    assert len(get(client, DEBTS, me[1], limit=100).json()["results"]) == 100
    assert len(get(client, DEBTS, me[1], limit=500).json()["results"]) == 100


# --- what each debt carries -------------------------------------------------------------------


def test_each_debt_carries_its_repayments_by_date_then_id(
    client: APIClient, me: tuple[User, str]
) -> None:
    debt = make(me)
    late = RepaymentFactory.create(debt=debt, date=date(2026, 10, 22), amount=1000)
    early = RepaymentFactory.create(debt=debt, date=date(2026, 10, 18), amount=2000)
    same = [
        RepaymentFactory.create(debt=debt, date=date(2026, 10, 20), amount=500)
        for _ in range(2)
    ]

    item = get(client, DEBTS, me[1]).json()["results"][0]

    assert [r["id"] for r in item["repayments"]] == [
        str(early.id),
        *sorted(str(r.id) for r in same),
        str(late.id),
    ]
    assert (item["repaid"], item["remaining"]) == ("40.00", "160.00")


def test_a_debt_without_repayments_has_an_empty_list(
    client: APIClient, me: tuple[User, str]
) -> None:
    make(me)

    assert get(client, DEBTS, me[1]).json()["results"][0]["repayments"] == []


def test_the_number_of_queries_does_not_grow_with_the_debts(
    client: APIClient, me: tuple[User, str]
) -> None:
    def count() -> int:
        cache.clear()
        with CaptureQueriesContext(connection) as queries:
            assert get(client, DEBTS, me[1]).status_code == 200
        return len(queries)

    for _ in range(2):
        RepaymentFactory.create_batch(3, debt=make(me))
    few = count()
    for _ in range(8):
        RepaymentFactory.create_batch(3, debt=make(me))

    assert count() == few


def test_a_list_never_shows_another_persons_debts(
    client: APIClient, me: tuple[User, str], other: tuple[User, str]
) -> None:
    mine = make(me)
    DebtFactory.create(owner=other[0], person="Secret")

    assert listed(get(client, DEBTS, me[1])) == [str(mine.id)]
    assert listed(get(client, DEBTS, me[1], search="Secret")) == []


# --- the filters ------------------------------------------------------------------------------


def test_direction_narrows_the_list(client: APIClient, me: tuple[User, str]) -> None:
    lent = make(me, direction="lent")
    borrowed = make(me, direction="borrowed")

    assert listed(get(client, DEBTS, me[1], direction="lent")) == [str(lent.id)]
    assert listed(get(client, DEBTS, me[1], direction="borrowed")) == [str(borrowed.id)]


@pytest.fixture
def one_of_each(me: tuple[User, str]) -> dict[str, Debt]:
    """An open, an overdue and a settled debt, on the day 2026-10-15."""
    open_ = make(me, person="Open", due_date=date(2026, 10, 15))
    no_due = make(me, person="NoDue", due_date=None)
    overdue = make(me, person="Overdue", due_date=date(2026, 10, 14))
    settled = make(me, person="Settled", due_date=date(2026, 10, 1))
    RepaymentFactory.create(debt=settled, amount=20000)
    almost = make(me, person="Almost", due_date=date(2026, 10, 1))
    RepaymentFactory.create(debt=almost, amount=19999)
    return {
        "open": open_,
        "no_due": no_due,
        "overdue": overdue,
        "settled": settled,
        "almost": almost,
    }


def ids(debts: dict[str, Debt], *names: str) -> list[str]:
    """The ids of the named debts, newest first (the order of the list)."""
    chosen = [debts[name] for name in names]
    return [
        str(d.id)
        for d in sorted(chosen, key=lambda d: (d.created_at, d.id), reverse=True)
    ]


@pytest.mark.parametrize(
    ("status", "names"),
    [
        (["open"], ("open", "no_due")),
        (["overdue"], ("overdue", "almost")),
        (["settled"], ("settled",)),
        (["open", "overdue"], ("open", "no_due", "overdue", "almost")),
        (["settled", "overdue"], ("overdue", "settled", "almost")),
        (
            ["open", "overdue", "settled"],
            ("open", "no_due", "overdue", "settled", "almost"),
        ),
    ],
)
def test_status_matches_any_of_the_repeated_values(
    client: APIClient,
    me: tuple[User, str],
    one_of_each: dict[str, Debt],
    status: list[str],
    names: tuple[str, ...],
) -> None:
    response = client.get(DEBTS, {"status": status}, headers=bearer(me[1]))

    assert listed(response) == ids(one_of_each, *names)


def test_the_status_filter_agrees_with_the_status_of_each_debt(
    client: APIClient, me: tuple[User, str], one_of_each: dict[str, Debt]
) -> None:
    every = get(client, DEBTS, me[1]).json()["results"]
    for status in ("open", "overdue", "settled"):
        wanted = {i["id"] for i in every if i["status"] == status}

        got = set(listed(client.get(DEBTS, {"status": status}, headers=bearer(me[1]))))

        assert got == wanted, status


def test_the_status_filter_counts_the_day_from_today_when_it_is_sent(
    client: APIClient, me: tuple[User, str], one_of_each: dict[str, Debt]
) -> None:
    later = get(client, DEBTS, me[1], status="overdue", today="2026-10-16")
    early = get(client, DEBTS, me[1], status="overdue", today="2026-10-01")

    assert set(listed(later)) == {
        str(one_of_each[n].id) for n in ("open", "overdue", "almost")
    }
    assert listed(early) == []
    only = get(client, DEBTS, me[1], today="2026-10-16").json()["results"]
    assert {i["person"]: i["status"] for i in only}["Open"] == "overdue"


def test_the_status_filter_uses_the_persons_own_day(
    client: APIClient, me: tuple[User, str], clock: Any
) -> None:
    clock.current = datetime(2026, 10, 15, 5, 0, tzinfo=UTC)
    debt = make(me, due_date=date(2026, 10, 14))
    assert listed(get(client, DEBTS, me[1], status="overdue")) == [str(debt.id)]

    User.objects.filter(pk=me[0].pk).update(time_zone="Etc/GMT+12")  # still the 14th

    assert listed(get(client, DEBTS, me[1], status="overdue")) == []
    assert listed(get(client, DEBTS, me[1], status="open")) == [str(debt.id)]


def test_a_filter_by_status_keeps_one_row_per_debt_with_many_repayments(
    client: APIClient, me: tuple[User, str]
) -> None:
    debt = make(me)
    RepaymentFactory.create_batch(5, debt=debt, amount=1000)

    response = get(client, DEBTS, me[1], status="open")

    assert listed(response) == [str(debt.id)]
    assert len(response.json()["results"][0]["repayments"]) == 5


def test_status_with_the_other_filters_and_pages(
    client: APIClient, me: tuple[User, str]
) -> None:
    wanted = [
        make(
            me,
            direction="borrowed",
            person="Sam",
            start_date=date(2026, 8, 1),
            due_date=date(2026, 9, 1),
        )
        for _ in range(3)
    ]
    make(
        me,
        direction="lent",
        person="Sam",
        start_date=date(2026, 8, 1),
        due_date=date(2026, 9, 1),
    )
    make(
        me,
        direction="borrowed",
        person="Lee",
        start_date=date(2026, 8, 1),
        due_date=date(2026, 9, 1),
    )
    make(me, direction="borrowed", person="Sam", due_date=date(2026, 12, 1))

    pages = walk(
        client, me[1], status="overdue", direction="borrowed", search="sam", limit=2
    )

    assert [len(p) for p in pages] == [2, 1]
    assert {i for p in pages for i in p} == {str(d.id) for d in wanted}


def test_search_is_a_case_insensitive_substring_of_the_person_with_literal_wildcards(
    client: APIClient, me: tuple[User, str]
) -> None:
    alex = make(me, person="Alexander")
    make(me, person="Sam")
    percent = make(me, person="100% Bob")
    underscore = make(me, person="a_b_c")
    make(me, person="abxc")

    assert listed(get(client, DEBTS, me[1], search="LEX")) == [str(alex.id)]
    assert listed(get(client, DEBTS, me[1], search="0%")) == [str(percent.id)]
    assert listed(get(client, DEBTS, me[1], search="a_b")) == [str(underscore.id)]
    assert listed(get(client, DEBTS, me[1], search="zz")) == []


def test_search_looks_at_the_person_only(
    client: APIClient, me: tuple[User, str]
) -> None:
    make(me, person="Alex", note="Laptop repair")

    assert listed(get(client, DEBTS, me[1], search="Laptop")) == []


@pytest.mark.parametrize(
    ("param", "value", "code"),
    [
        ("search", "a", "min_length"),
        ("search", "x" * 51, "max_length"),
        ("search", "bad\u0000", "invalid_characters"),
        ("direction", "gave", "invalid_choice"),
        ("status", "paid", "invalid_choice"),
        ("today", "someday", "invalid"),
    ],
)
def test_a_bad_parameter_is_a_400_on_that_parameter(
    client: APIClient, me: tuple[User, str], param: str, value: str, code: str
) -> None:
    response = get(client, DEBTS, me[1], **{param: value})

    assert response.status_code == 400
    error = first_error(response)
    assert (error["code"], error["attr"]) == (code, param)


def test_a_bad_status_among_good_ones_is_a_400(
    client: APIClient, me: tuple[User, str]
) -> None:
    response = client.get(DEBTS, {"status": ["open", "paid"]}, headers=bearer(me[1]))

    assert response.status_code == 400
    assert first_error(response)["code"] == "invalid_choice"


def test_a_parameter_the_list_does_not_know_is_ignored(
    client: APIClient, me: tuple[User, str]
) -> None:
    debt = make(me)

    assert listed(get(client, DEBTS, me[1], colour="red")) == [str(debt.id)]


def test_a_list_asked_with_search_counts_in_the_search_group(
    client: APIClient, me: tuple[User, str], settings: Any
) -> None:
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        "DEFAULT_THROTTLE_RATES": {
            **settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
            "search": "2/min",
        },
    }
    codes = [get(client, DEBTS, me[1], search="ab").status_code for _ in range(3)]
    plain = get(client, DEBTS, me[1]).status_code

    assert codes == [200, 200, 429]
    assert plain == 200
