"""The escaping of text cells and the exact units of the salary months file."""

import json
import random
from pathlib import Path
from typing import Any

import pytest

from transfer.cells import escape_cell, unescape_cell
from transfer.units import (
    InvalidUnit,
    hours_to_hundredths,
    percent_to_basis_points,
    write_hundredths,
)

VECTORS = json.loads((Path(__file__).parent / "csv_vectors.json").read_text())
CELLS: list[dict[str, Any]] = VECTORS["cells"]


@pytest.mark.parametrize("case", CELLS, ids=[c["name"] for c in CELLS])
def test_the_shared_cells_are_escaped_and_unescaped_as_written(
    case: dict[str, Any],
) -> None:
    assert escape_cell(case["text"]) == case["cell"]
    assert unescape_cell(case["cell"]) == case["text"]


def test_a_text_never_changes_by_a_round_trip_whatever_it_is() -> None:
    alphabet = ["'", "=", "+", "-", "@", "\t", "\r", "\n", "a", " ", "ب", ",", '"']
    generator = random.Random(20261025)  # noqa: S311  # a seeded generator for test data, the same cells on every run
    for _ in range(20000):
        text = "".join(generator.choices(alphabet, k=generator.randint(0, 7)))

        cell = escape_cell(text)

        assert unescape_cell(cell) == text
        assert cell in (text, f"'{text}")


def test_a_cell_is_never_left_starting_with_a_formula_character() -> None:
    for text in ["=1+1", "+1", "-1", "@SUM(A1)", "\tx", "\rx", "\nx", "'=x", "''=x"]:
        assert escape_cell(text)[0] == "'"


@pytest.mark.parametrize("text", ["", "x", "a=b", "1+1", "'plain", "''", " =x"])
def test_other_texts_are_written_as_they_are(text: str) -> None:
    assert escape_cell(text) == text
    assert unescape_cell(text) == text


@pytest.mark.parametrize(
    ("text", "hundredths"),
    [("0.00", 0), ("7.50", 750), ("744.00", 74400), ("0.01", 1), ("12.34", 1234)],
)
def test_hours_and_percent_read_exactly_to_hundredths(
    text: str, hundredths: int
) -> None:
    assert hours_to_hundredths(text) == hundredths
    assert write_hundredths(hundredths) == text


def test_the_largest_values_are_accepted_and_the_next_is_not() -> None:
    assert hours_to_hundredths("744.00") == 74400
    assert percent_to_basis_points("100.00") == 10000
    for call, text in (
        (hours_to_hundredths, "744.01"),
        (percent_to_basis_points, "100.01"),
    ):
        with pytest.raises(InvalidUnit) as error:
            call(text)
        assert error.value.code == "max_value"


@pytest.mark.parametrize("text", ["-0.01", "-1.00"])
def test_below_zero_is_min_value(text: str) -> None:
    with pytest.raises(InvalidUnit) as error:
        hours_to_hundredths(text)
    assert error.value.code == "min_value"


@pytest.mark.parametrize(
    "text",
    [
        "7.5",
        "7.500",
        "7,50",
        "7",
        ".50",
        "07.50",
        "+7.50",
        "-0.00",
        " 7.50",
        "7.50 ",
        "1e1",
        "",
        "abc",
        7.5,
        None,
        750,
    ],
)
def test_anything_but_exactly_two_decimals_is_invalid(text: Any) -> None:
    with pytest.raises(InvalidUnit) as error:
        hours_to_hundredths(text)
    assert error.value.code == "invalid"


def test_every_whole_number_of_hundredths_survives_the_trip() -> None:
    for value in [*range(3000), 9999, 10000, 74399, 74400]:
        text = write_hundredths(value)
        assert text.count(".") == 1 and len(text.split(".")[1]) == 2
        if value <= 74400:
            assert hours_to_hundredths(text) == value
        if value <= 10000:
            assert percent_to_basis_points(text) == value
