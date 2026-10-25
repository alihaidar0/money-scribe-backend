"""The shared test vectors: the Overview, the month report and the debts report against figures worked out by hand.

The expected figures of ``overview_vectors.json`` and ``report_vectors.json`` are written from the rules of the
contract, not copied from the code, and the app runs the same files.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from rest_framework.test import APIClient

from reports.tests.builders import (
    DEBTS_REPORT,
    get,
    month_report_url,
    overview_url,
    resolve_ids,
    set_up,
)
from users.models import User

pytestmark = pytest.mark.django_db

HERE = Path(__file__).parent
OVERVIEW = json.loads((HERE / "overview_vectors.json").read_text())["cases"]
REPORTS = json.loads((HERE / "report_vectors.json").read_text())["cases"]
MONTH_CASES = [c for c in REPORTS if c["kind"] == "month"]
DEBT_CASES = [c for c in REPORTS if c["kind"] == "debts"]


@pytest.fixture(autouse=True)
def _today(clock: Any) -> None:
    """Pin the day to 2026-10-15; the debts cases pass their own `today`."""


def asked(case: dict[str, Any]) -> dict[str, str]:
    return {} if case["currency"] is None else {"currency": case["currency"]}


@pytest.mark.parametrize("case", OVERVIEW, ids=[c["name"] for c in OVERVIEW])
def test_the_overview_vectors(
    client: APIClient, me: tuple[User, str], case: dict[str, Any]
) -> None:
    set_up(client, me, case["inputs"])

    response = get(client, overview_url(case["inputs"]["month"]), me[1], **asked(case))

    assert response.status_code == 200, response.json()
    expected = dict(case["expected"])
    currency = expected.pop("currency", case["currency"])
    assert response.json() == {
        "month": case["inputs"]["month"],
        "currency": currency,
        **expected,
    }


@pytest.mark.parametrize("case", MONTH_CASES, ids=[c["name"] for c in MONTH_CASES])
def test_the_month_report_vectors(
    client: APIClient, me: tuple[User, str], case: dict[str, Any]
) -> None:
    custom = set_up(client, me, case["inputs"])

    response = get(
        client, month_report_url(case["inputs"]["month"]), me[1], **asked(case)
    )

    assert response.status_code == 200, response.json()
    expected = resolve_ids(dict(case["expected"]), custom)
    currency = expected.pop("currency", case["currency"])
    assert response.json() == {
        "month": case["inputs"]["month"],
        "currency": currency,
        **expected,
    }


@pytest.mark.parametrize("case", DEBT_CASES, ids=[c["name"] for c in DEBT_CASES])
def test_the_debts_report_vectors(
    client: APIClient, me: tuple[User, str], case: dict[str, Any]
) -> None:
    set_up(client, me, case["inputs"])

    response = get(client, DEBTS_REPORT, me[1], today=case["inputs"]["today"])

    assert response.status_code == 200, response.json()
    body = response.json()
    ids = [
        item.pop("id")
        for block in body["by_currency"]
        for key in ("overdue", "open")
        for item in block[key]
    ]
    assert len(ids) == len(set(ids))
    assert body == case["expected"]


def test_the_vector_files_have_the_cases_of_the_issue() -> None:
    names = " | ".join(c["name"] for c in OVERVIEW + REPORTS)
    for needle in (
        "worked example",
        "one payment",
        "no salary month",
        "another currency",
        "only repayments",
        "empty month",
        "15th",
        "JPY",
        "KWD",
        "negative",
        "Settings default",
        "second part",
        "deleted custom category",
        "two currencies",
    ):
        assert needle in names, needle
