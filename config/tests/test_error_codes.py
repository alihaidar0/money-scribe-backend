import re
from pathlib import Path

from config.error_codes import ErrorCode

ADR = (
    Path(__file__).resolve().parents[2] / "docs" / "adr" / "0004-error-status-codes.md"
)


def _codes_in_the_adr_registry() -> set[str]:
    """Every code in the first column of the registry table of ADR 0004."""
    text = ADR.read_text()
    registry = text.split("Initial registry:", 1)[1]
    return set(re.findall(r"^\| `([a-z_]+)` \|", registry, flags=re.MULTILINE))


def test_codes_are_unique_snake_case_strings() -> None:
    values = [code.value for code in ErrorCode]

    assert len(values) == len(set(values))
    assert all(re.fullmatch(r"[a-z]+(_[a-z]+)*", value) for value in values)


def test_the_enum_and_the_adr_registry_list_the_same_codes() -> None:
    in_code = {code.value for code in ErrorCode}

    assert in_code == _codes_in_the_adr_registry()
