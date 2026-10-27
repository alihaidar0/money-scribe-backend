import csv
import io
import json
import uuid
import zipfile
from typing import Any

from rest_framework.response import Response
from rest_framework.test import APIClient

from transfer.cells import unescape_cell
from users.tests.helpers import PASSWORD, bearer

API = "/api/v1"
EXPORTS = f"{API}/exports/"
IMPORTS = f"{API}/imports/"
BOM = "﻿"


def fresh_key() -> str:
    return f'"{uuid.uuid4()}"'


def export(client: APIClient, token: str, kinds: list[str], **body: Any) -> Response:
    """An export request; the password is the one every test user has unless a test sends another."""
    payload: dict[str, Any] = {
        "kinds": kinds,
        "current_password": PASSWORD,
        **body,
    }
    return client.post(EXPORTS, payload, headers=bearer(token))


def import_rows(
    client: APIClient,
    token: str,
    dataset: str,
    rows: list[dict[str, Any]],
    *,
    dry_run: bool = False,
    key: str | None = None,
) -> Response:
    headers = bearer(token)
    if not dry_run:
        headers["Idempotency-Key"] = key or fresh_key()
    return client.post(
        IMPORTS,
        {"dataset": dataset, "dry_run": dry_run, "rows": rows},
        headers=headers,
    )


def text_of(response: Response) -> str:
    """The body of a CSV answer as text, without the byte order mark."""
    text = bytes(response.content).decode("utf-8")
    assert text.startswith(BOM)
    return text[len(BOM) :]


def parse_csv(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text, newline="")))


def zip_members(response: Response) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(bytes(response.content))) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


TEXT_COLUMNS = {
    "accounts": ("name",),
    "categories": ("name",),
    "transactions": ("category_name", "account_name", "note"),
    "debts": ("person", "note"),
    "salary_months": (),
}
JSON_COLUMNS = {"debts": ("repayments",), "salary_months": ("fixed_items",)}
INTEGER_COLUMNS = {
    "salary_months": (
        "working_days_in_month",
        "working_hours_in_month",
        "working_days_first_half",
        "first_pay_day",
        "second_pay_day",
    )
}


def rows_of_csv(dataset: str, text: str) -> list[dict[str, Any]]:
    """What the app does with a file: read the lines, undo the escaping of text cells, leave an empty cell out,
    read a JSON cell as an array and the whole-number cells as numbers; the line number is ``row``."""
    lines = parse_csv(text)
    header, body = lines[0], lines[1:]
    rows = []
    for number, cells in enumerate(body, start=2):
        row: dict[str, Any] = {"row": number}
        for name, cell in zip(header, cells, strict=True):
            if cell == "":
                continue
            if name in TEXT_COLUMNS[dataset]:
                row[name] = unescape_cell(cell)
            elif name in JSON_COLUMNS.get(dataset, ()):
                row[name] = json.loads(cell)
            elif name in INTEGER_COLUMNS.get(dataset, ()):
                row[name] = int(cell)
            else:
                row[name] = cell
        rows.append(row)
    return rows
