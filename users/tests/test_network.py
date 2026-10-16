import pytest

from users.network import truncate_ip


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("203.0.113.77", "203.0.113.0"),
        ("192.168.1.255", "192.168.1.0"),
        ("2001:db8:abcd:1234:5678:9abc:def0:1234", "2001:db8:abcd::"),
        ("::ffff:198.51.100.200", "198.51.100.0"),
        ("", None),
        (None, None),
        ("not an address", None),
    ],
)
def test_only_the_network_of_an_address_is_kept(
    raw: str | None, expected: str | None
) -> None:
    assert truncate_ip(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("203.0.113.77", "203.0.113.77"),
        ("2001:db8:abcd:1234:5678:9abc:def0:1234", "2001:db8:abcd:1234::/64"),
        ("2001:db8:abcd:1234:ffff:ffff:ffff:ffff", "2001:db8:abcd:1234::/64"),
        ("::ffff:198.51.100.200", "198.51.100.200"),
        ("", "unknown"),
        (None, "unknown"),
        ("not an address", "unknown"),
    ],
)
def test_a_client_is_an_ipv4_address_or_an_ipv6_slash_64(
    raw: str | None, expected: str
) -> None:
    from users.network import rate_limit_key

    assert rate_limit_key(raw) == expected
