"""A minimal reader for DER, enough to read the Android key attestation extension (ADR 0018).

It reads, it never writes, and it is strict: every length must fit inside its parent, a long-form
length must be minimal, and nothing may be left over. That keeps a hostile certificate from sending the
parser anywhere but to a :class:`DerError`. It handles the universal types the extension uses (INTEGER,
BOOLEAN, ENUMERATED, OCTET STRING, NULL, SEQUENCE, SET) and context-specific tags of any number, because
the attestation lists use tags such as ``[504]`` and ``[709]``.
"""

from dataclasses import dataclass

BOOLEAN = 0x01
INTEGER = 0x02
OCTET_STRING = 0x04
NULL = 0x05
ENUMERATED = 0x0A
SEQUENCE = 0x30
SET = 0x31

CLASS_UNIVERSAL = 0
CLASS_CONTEXT = 2


class DerError(ValueError):
    """The bytes are not the DER structure that was expected."""


@dataclass(frozen=True)
class Tlv:
    """One element: its class, whether it is constructed, its tag number and its content bytes."""

    tag_class: int
    constructed: bool
    number: int
    content: bytes

    @property
    def universal(self) -> int:
        """The first identifier byte of a universal element, for example ``0x30`` for a SEQUENCE."""
        if self.tag_class != CLASS_UNIVERSAL:
            return -1
        return self.number | (0x20 if self.constructed else 0)


def _read_length(data: bytes, position: int) -> tuple[int, int]:
    if position >= len(data):
        raise DerError("The length is missing.")
    first = data[position]
    position += 1
    if first < 0x80:
        return first, position
    count = first & 0x7F
    if count == 0 or count > 4 or position + count > len(data):
        raise DerError("The length is not valid.")
    length = int.from_bytes(data[position : position + count], "big")
    if length < 0x80 or data[position] == 0:
        raise DerError("The length is not minimal.")
    return length, position + count


def _read_tag(data: bytes, position: int) -> tuple[int, bool, int, int]:
    if position >= len(data):
        raise DerError("The tag is missing.")
    first = data[position]
    position += 1
    tag_class, constructed, number = first >> 6, bool(first & 0x20), first & 0x1F
    if number == 0x1F:
        number = 0
        for index in range(5):
            if position >= len(data):
                raise DerError("The tag is cut short.")
            byte = data[position]
            position += 1
            if index == 0 and byte == 0x80:
                raise DerError("The tag is not minimal.")
            number = (number << 7) | (byte & 0x7F)
            if not byte & 0x80:
                break
        else:
            raise DerError("The tag is too long.")
        if number < 0x1F:
            raise DerError("The tag is not minimal.")
    return tag_class, constructed, number, position


def read(data: bytes) -> Tlv:
    """The one element that ``data`` consists of, with nothing after it."""
    items = children(data)
    if len(items) != 1:
        raise DerError("Expected exactly one element.")
    return items[0]


def children(data: bytes) -> list[Tlv]:
    """The elements one after another in ``data``, which must be filled exactly."""
    items: list[Tlv] = []
    position = 0
    while position < len(data):
        tag_class, constructed, number, position = _read_tag(data, position)
        length, position = _read_length(data, position)
        end = position + length
        if end > len(data):
            raise DerError("An element is longer than its parent.")
        items.append(Tlv(tag_class, constructed, number, data[position:end]))
        position = end
    return items


def sequence(element: Tlv) -> list[Tlv]:
    if element.universal != SEQUENCE:
        raise DerError("Expected a SEQUENCE.")
    return children(element.content)


def set_of(element: Tlv) -> list[Tlv]:
    if element.universal != SET:
        raise DerError("Expected a SET.")
    return children(element.content)


def integer(element: Tlv) -> int:
    if element.universal != INTEGER or not element.content:
        raise DerError("Expected an INTEGER.")
    return int.from_bytes(element.content, "big", signed=True)


def enumerated(element: Tlv) -> int:
    if element.universal != ENUMERATED or not element.content:
        raise DerError("Expected an ENUMERATED.")
    return int.from_bytes(element.content, "big", signed=True)


def boolean(element: Tlv) -> bool:
    if element.universal != BOOLEAN or len(element.content) != 1:
        raise DerError("Expected a BOOLEAN.")
    return element.content != b"\x00"


def octets(element: Tlv) -> bytes:
    if element.universal != OCTET_STRING:
        raise DerError("Expected an OCTET STRING.")
    return element.content


def explicit(element: Tlv) -> Tlv:
    """The one element inside an EXPLICIT context tag."""
    if element.tag_class != CLASS_CONTEXT or not element.constructed:
        raise DerError("Expected an explicit context tag.")
    return read(element.content)


def context_fields(parent: Tlv) -> dict[int, Tlv]:
    """The EXPLICIT context-tagged fields of a SEQUENCE, by tag number; a repeated tag is an error."""
    fields: dict[int, Tlv] = {}
    for element in sequence(parent):
        if element.tag_class != CLASS_CONTEXT:
            raise DerError("Expected a context-tagged field.")
        if element.number in fields:
            raise DerError("A field is repeated.")
        fields[element.number] = explicit(element)
    return fields
