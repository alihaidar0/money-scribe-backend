"""The text rules of ADR 0021: what a name or a note may hold."""

from typing import Any

import pytest
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from common.serializers import TextField
from common.text import has_forbidden_character, normalize_text

FORBIDDEN = [
    pytest.param("line\nbreak", id="newline"),
    pytest.param("tab\tseparated", id="tab"),
    pytest.param("carriage\rreturn", id="carriage-return"),
    pytest.param("nul\x00byte", id="nul"),
    pytest.param("bell\x07", id="bell"),
    pytest.param("next\x85line", id="next-line"),
    pytest.param("line\u2028separator", id="line-separator"),
    pytest.param("para\u2029separator", id="paragraph-separator"),
    pytest.param("half\ud800surrogate", id="unpaired-surrogate"),
    *(
        pytest.param(f"x{chr(code)}y", id=f"bidi-{code:04x}")
        for code in [*range(0x202A, 0x202F), *range(0x2066, 0x206A)]
    ),
]

ALLOWED = [
    pytest.param("Groceries", id="ascii"),
    pytest.param("Café Müller", id="accents"),
    pytest.param("نقدًا", id="arabic-with-tanwin"),
    pytest.param("بطاقة", id="arabic"),
    pytest.param("price \u200e12\u200e", id="left-to-right-mark"),
    pytest.param("سعر \u200f12\u200f", id="right-to-left-mark"),
    pytest.param("رقم \u061c12\u061c", id="arabic-letter-mark"),
    pytest.param("می\u200cخواهم", id="zero-width-non-joiner"),  # noqa: RUF001  # real Persian text
    pytest.param("a\u200db", id="zero-width-joiner"),
    pytest.param("Lunch 🍕", id="emoji"),
    pytest.param("two  inner  spaces", id="inner-spaces"),
]


@pytest.mark.parametrize("value", FORBIDDEN)
def test_a_forbidden_character_is_found(value: str) -> None:
    assert has_forbidden_character(value)


@pytest.mark.parametrize("value", ALLOWED)
def test_an_allowed_text_is_not_flagged(value: str) -> None:
    assert not has_forbidden_character(value)


def test_the_text_is_trimmed_and_normalized_to_nfc() -> None:
    decomposed = "Café"  # e + combining acute accent

    assert normalize_text(f"  {decomposed}  ") == "Café"
    assert len(normalize_text(decomposed)) == 4


def field(**kwargs: Any) -> TextField:
    return TextField(**kwargs)


@pytest.mark.parametrize("value", FORBIDDEN)
def test_the_field_refuses_a_forbidden_character_with_its_own_code(value: str) -> None:
    with pytest.raises(ValidationError) as error:
        field(max_length=100).run_validation(value)

    assert error.value.get_codes() == ["invalid_characters"]


@pytest.mark.parametrize("value", ALLOWED)
def test_the_field_accepts_an_allowed_text(value: str) -> None:
    assert field(max_length=100).run_validation(value) == value


def test_the_field_trims_and_normalizes_what_it_returns() -> None:
    assert field(max_length=100).run_validation("  Café ") == "Café"


def test_the_length_counts_code_points_after_normalization() -> None:
    decomposed = "é" * 30  # 60 code points, 30 after NFC

    assert field(max_length=30).run_validation(decomposed) == "é" * 30
    with pytest.raises(ValidationError) as error:
        field(max_length=30).run_validation("é" * 31)
    assert error.value.get_codes() == ["max_length"]


@pytest.mark.parametrize("value", [12, 1.5, True, None, ["a"], {"a": 1}])
def test_the_field_takes_only_a_string(value: Any) -> None:
    with pytest.raises(ValidationError):
        field(max_length=100).run_validation(value)


def test_blank_after_trimming_keeps_the_usual_code() -> None:
    with pytest.raises(ValidationError) as error:
        field(max_length=100).run_validation("  \u00a0 ")

    assert error.value.get_codes() == ["blank"]


def test_the_error_is_reported_on_the_field_of_a_serializer() -> None:
    class Body(serializers.Serializer):  # type: ignore[misc]
        name = TextField(max_length=50)

    serializer = Body(data={"name": "Sam\u202eEvil"})

    assert not serializer.is_valid()
    assert serializer.errors["name"][0].code == "invalid_characters"
