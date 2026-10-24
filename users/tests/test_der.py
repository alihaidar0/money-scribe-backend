import pytest

from users import der
from users.tests.device_key_helpers import (
    ctx,
    der_bool,
    der_enum,
    der_int,
    der_null,
    der_octets,
    der_seq,
    der_set,
    tlv,
)


def test_a_sequence_of_scalars_is_read_back() -> None:
    element = der.read(
        der_seq(der_int(300), der_enum(2), der_bool(True), der_octets(b"ab"))
    )

    integer, level, flag, text = der.sequence(element)

    assert der.integer(integer) == 300
    assert der.enumerated(level) == 2
    assert der.boolean(flag) is True
    assert der.octets(text) == b"ab"


def test_a_negative_integer_is_signed() -> None:
    assert der.integer(der.read(der_int(-1))) == -1
    assert der.integer(der.read(der_int(255))) == 255


@pytest.mark.parametrize("number", [1, 10, 30, 31, 127, 128, 504, 709, 16384])
def test_a_context_tag_of_any_number_is_read(number: int) -> None:
    element = der.read(der_seq(ctx(number, der_int(7))))

    fields = der.context_fields(element)

    assert list(fields) == [number]
    assert der.integer(fields[number]) == 7


def test_a_set_of_integers_is_read() -> None:
    element = der.read(der_set(der_int(2), der_int(3)))

    assert [der.integer(item) for item in der.set_of(element)] == [2, 3]


def test_a_long_length_is_read() -> None:
    element = der.read(der_octets(b"x" * 300))

    assert len(der.octets(element)) == 300


def test_null_has_no_content() -> None:
    assert der.read(der_null()).content == b""


@pytest.mark.parametrize(
    "data",
    [
        b"",  # nothing at all
        b"\x30",  # a tag with no length
        b"\x30\x05\x01",  # a length longer than what follows
        b"\x30\x81\x05" + b"\x00" * 5,  # a long form where the short form fits
        b"\x30\x80",  # an indefinite length is not DER
        b"\x04\x01\x00\x04\x01\x00",  # two elements where one is expected
        b"\xbf\x80\x01\x00",  # a tag number with a leading zero group
        b"\xbf\x1e\x00",  # a high-tag form for a number that fits the low form
        b"\xbf\xff\xff\xff\xff\xff\x01\x00",  # a tag number that never ends
    ],
)
def test_a_malformed_element_is_refused(data: bytes) -> None:
    with pytest.raises(der.DerError):
        der.read(data)


def test_the_wrong_type_is_refused() -> None:
    with pytest.raises(der.DerError):
        der.integer(der.read(der_octets(b"a")))
    with pytest.raises(der.DerError):
        der.sequence(der.read(der_int(1)))
    with pytest.raises(der.DerError):
        der.boolean(der.read(tlv(0x01, b"\x00\x00")))


def test_a_repeated_context_field_is_refused() -> None:
    element = der.read(der_seq(ctx(2, der_int(1)), ctx(2, der_int(2))))

    with pytest.raises(der.DerError):
        der.context_fields(element)


def test_a_field_that_is_not_context_tagged_is_refused() -> None:
    with pytest.raises(der.DerError):
        der.context_fields(der.read(der_seq(der_int(1))))
