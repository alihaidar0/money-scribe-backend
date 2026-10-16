import ipaddress

from rest_framework.request import Request
from rest_framework.throttling import BaseThrottle

IPV4_PREFIX = 24
IPV6_PREFIX = 48
# One IPv6 customer usually owns a whole /64, so every address inside it is one client for the limits.
IPV6_LIMIT_PREFIX = 64


def client_ip(request: Request) -> str:
    """The client's address, honouring ``NUM_PROXIES`` (one rule for the throttles and the sessions)."""
    return str(BaseThrottle().get_ident(request))


def truncate_ip(raw: str | None) -> str | None:
    """Keep only the network of an address: IPv4 to the /24, IPv6 to the /48 (ADR 0009).

    The full address is never stored. Anything that is not an address gives ``None``.
    """
    try:
        address = ipaddress.ip_address(raw or "")
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    prefix = IPV4_PREFIX if address.version == 4 else IPV6_PREFIX
    return str(ipaddress.ip_network((address, prefix), strict=False).network_address)


def rate_limit_key(raw: str | None) -> str:
    """The identity of a client for the lock and the throttles: an IPv4 address, or an IPv6 /64.

    An IPv6 client can pick any address inside its /64, so keying a limit by the full address would
    let it start again for free with each one. Anything that is not an address is one shared client.
    """
    try:
        address = ipaddress.ip_address(raw or "")
    except ValueError:
        return "unknown"
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped:
            return str(address.ipv4_mapped)
        return str(ipaddress.ip_network((address, IPV6_LIMIT_PREFIX), strict=False))
    return str(address)
