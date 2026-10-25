"""Rules for user-entered text (ADR 0021).

A text value is trimmed, normalized to Unicode NFC, and refused when it holds a character that has no place in a
name or a note:

* control characters, which include the newline and the tab (PostgreSQL also refuses the NUL character);
* the line and paragraph separators U+2028 and U+2029, which are newlines by another name;
* unpaired surrogates, which a JSON escape can produce and no database can store;
* the bidirectional override, embedding and isolate characters (U+202A to U+202E and U+2066 to U+2069), which can make
  text display as something it is not.

The direction marks (U+200E, U+200F, U+061C) and the joiners (U+200C, U+200D) stay allowed, because Arabic text needs
them. The server never edits a value beyond trimming and NFC, so a rejected character is always an error.
"""

import unicodedata
from typing import Final

_FORBIDDEN_CATEGORIES: Final = frozenset({"Cc", "Cs", "Zl", "Zp"})
_FORBIDDEN_BIDI: Final = frozenset(
    [*range(0x202A, 0x202F), *range(0x2066, 0x206A)]
)  # U+202A..U+202E, U+2066..U+2069


def has_forbidden_character(value: str) -> bool:
    """Whether ``value`` holds a character the API refuses in text."""
    return any(
        unicodedata.category(character) in _FORBIDDEN_CATEGORIES
        or ord(character) in _FORBIDDEN_BIDI
        for character in value
    )


def normalize_text(value: str) -> str:
    """Trim the value and normalize it to NFC; nothing else is changed."""
    return unicodedata.normalize("NFC", value.strip()).strip()
