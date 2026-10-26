"""Text cells of a CSV file and the formula injection rule (the issue 'CSV export and import').

A spreadsheet program reads a cell that starts with ``=``, ``+``, ``-`` or ``@`` as a formula, and a tab, a carriage
return or a line feed can hide one. So a text cell the person typed (a note, a person, a category name, an account
name) that starts with such a character gets one ``'`` in front. The rule is closed under the quote itself: a cell
that starts with any number of ``'`` followed by one of those characters also gets one more, because otherwise
removing one on the way back would lose a quote that was part of the text.

The app reverses it with :func:`unescape_cell` before it sends a row to the import. Amounts, dates, numbers and the JSON
cells are never passed through here. Which characters are dangerous is fixed by this module and by
``transfer/tests/csv_vectors.json``, which the app runs too.
"""

import re
from typing import Final

_DANGEROUS: Final = "=+-@\t\r\n"
# Any number of quotes, then a dangerous character: the cells that are written with one more quote in front.
_NEEDS_A_QUOTE: Final = re.compile(r"'*[=+\-@\t\r\n]")


def escape_cell(text: str) -> str:
    """The text as it is written to a cell: one ``'`` in front of a cell that a spreadsheet could read as a formula."""
    return f"'{text}" if _NEEDS_A_QUOTE.match(text) else text


def unescape_cell(cell: str) -> str:
    """The reverse of :func:`escape_cell`: remove exactly one leading ``'`` when the rest would need it again."""
    if cell.startswith("'") and _NEEDS_A_QUOTE.match(cell, 1):
        return cell[1:]
    return cell
