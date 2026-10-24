"""Every rule of the Android key attestation (ADR 0018), against chains built in the test."""

import json
import re
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from django.conf import settings as django_settings
from django.core.cache import cache

from config.exceptions import ServiceUnavailable
from users import attestation
from users.tests.device_key_helpers import (
    CERT_DIGEST,
    PACKAGE,
    Chain,
    Phone,
    Spec,
    build_chain,
    spki,
)

CHALLENGE = bytes(range(32))
Trust = Callable[[Chain], None]


@pytest.fixture(autouse=True)
def app(settings: Any) -> None:
    settings.DEVICE_KEY_ANDROID_PACKAGE = PACKAGE
    settings.DEVICE_KEY_ANDROID_CERT_SHA256 = [CERT_DIGEST.hex()]


@pytest.fixture
def revoked(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    serials: set[str] = set()
    monkeypatch.setattr(attestation, "_fetch_status", lambda: serials)
    return serials


@pytest.fixture
def trust(settings: Any, tmp_path: Path) -> Trust:
    """Make a chain's root a trusted one, as the shipped file does for Google's."""

    def install(chain: Chain) -> None:
        path = tmp_path / "roots.pem"
        path.write_text(chain.root_pem)
        settings.DEVICE_KEY_TRUSTED_ROOTS_PATH = path

    return install


def verify(phone: Phone, chain: Chain, challenge: bytes = CHALLENGE) -> None:
    attestation.verify_android_key(
        chain=chain.b64, challenge=challenge, public_key=spki(phone.public)
    )


def good(phone: Phone, trust: Trust, **spec: Any) -> Chain:
    chain = build_chain(phone, Spec(challenge=CHALLENGE, **spec))
    trust(chain)
    return chain


def test_a_good_attestation_is_accepted(revoked: set[str], trust: Trust) -> None:
    phone = Phone()

    verify(phone, good(phone, trust))


def test_a_keymaster_key_with_a_size_and_no_curve_is_accepted(
    revoked: set[str], trust: Trust
) -> None:
    phone = Phone()

    verify(phone, good(phone, trust, ec_curve=None, key_size=256))


def test_a_strongbox_attestation_is_accepted(revoked: set[str], trust: Trust) -> None:
    phone = Phone()

    verify(phone, good(phone, trust, attestation_level=2, key_level=2))


def test_a_zero_authentication_timeout_is_accepted(
    revoked: set[str], trust: Trust
) -> None:
    phone = Phone()

    verify(phone, good(phone, trust, auth_timeout=0))


def test_the_app_id_may_sit_in_the_hardware_list(
    revoked: set[str], trust: Trust
) -> None:
    phone = Phone()

    verify(phone, good(phone, trust, app_id_in_hardware_list=True))


def test_expired_certificates_do_not_matter(revoked: set[str], trust: Trust) -> None:
    """Google: keys of devices launched before 2021 have expired certificates and are still trusted."""
    phone = Phone()
    chain = build_chain(phone, Spec(challenge=CHALLENGE), expired_leaf=True)
    trust(chain)

    verify(phone, chain)


@pytest.mark.parametrize(
    ("spec", "rule"),
    [
        ({"attestation_level": 0}, 3),
        ({"key_level": 0}, 3),
        ({"purpose": (2, 3)}, 3),
        ({"purpose": (3,)}, 3),
        ({"algorithm": 1}, 3),
        ({"ec_curve": 2}, 3),
        ({"ec_curve": None, "key_size": None}, 3),
        ({"ec_curve": None, "key_size": 384}, 3),
        ({"origin": 2}, 3),
        ({"no_auth_required": True}, 4),
        ({"user_auth_type": None}, 4),
        ({"user_auth_type": 0}, 4),
        ({"user_auth_type": 1}, 4),
        ({"user_auth_type": 3}, 4),
        ({"user_auth_type": -1}, 4),
        ({"auth_timeout": 300}, 4),
        ({"package": "com.example.other"}, 5),
        ({"digests": (b"\x01" * 32,)}, 5),
        ({"digests": (CERT_DIGEST, b"\x01" * 32)}, 5),
        ({"digests": ()}, 5),
        ({"root_of_trust": False}, 6),
        ({"locked": False}, 6),
        ({"boot_state": 1}, 6),
        ({"boot_state": 2}, 6),
        ({"boot_state": 3}, 6),
    ],
)
def test_each_rule_that_fails_names_its_number(
    revoked: set[str], trust: Trust, spec: dict[str, Any], rule: int
) -> None:
    phone = Phone()

    with pytest.raises(attestation.AttestationFailed) as raised:
        verify(phone, good(phone, trust, **spec))

    assert raised.value.rule == rule


def test_another_challenge_is_refused(revoked: set[str], trust: Trust) -> None:
    phone = Phone()
    chain = good(phone, trust)

    with pytest.raises(attestation.AttestationFailed) as raised:
        verify(phone, chain, challenge=bytes(reversed(CHALLENGE)))

    assert raised.value.rule == 2


def test_a_chain_without_the_extension_is_refused(
    revoked: set[str], trust: Trust
) -> None:
    phone = Phone()
    chain = build_chain(phone, None)
    trust(chain)

    with pytest.raises(attestation.AttestationFailed) as raised:
        verify(phone, chain)

    assert raised.value.rule == 2


def test_an_unconfigured_app_is_refused(
    revoked: set[str], trust: Trust, settings: Any
) -> None:
    phone = Phone()
    chain = good(phone, trust)
    settings.DEVICE_KEY_ANDROID_PACKAGE = ""

    with pytest.raises(attestation.AttestationFailed) as raised:
        verify(phone, chain)

    assert raised.value.rule == 5


def test_a_root_that_is_not_trusted_is_refused(revoked: set[str]) -> None:
    phone = Phone()
    chain = build_chain(phone, Spec(challenge=CHALLENGE))

    with pytest.raises(attestation.AttestationFailed) as raised:
        verify(phone, chain)  # the shipped roots are Google's, not the test root

    assert raised.value.rule == 1


def test_another_key_in_the_leaf_is_refused(revoked: set[str], trust: Trust) -> None:
    phone, other = Phone(), Phone()
    chain = build_chain(phone, Spec(challenge=CHALLENGE), leaf_key=other)
    trust(chain)

    with pytest.raises(attestation.AttestationFailed) as raised:
        verify(phone, chain)

    assert raised.value.rule == 1


def test_a_chain_cut_before_the_root_is_refused(
    revoked: set[str], trust: Trust
) -> None:
    phone = Phone()
    chain = good(phone, trust)
    chain.certificates.pop()

    with pytest.raises(attestation.AttestationFailed) as raised:
        verify(phone, chain)

    assert raised.value.rule == 1


def test_a_chain_in_the_wrong_order_is_refused(revoked: set[str], trust: Trust) -> None:
    phone = Phone()
    chain = good(phone, trust)
    chain.certificates.reverse()

    with pytest.raises(attestation.AttestationFailed) as raised:
        verify(phone, chain)

    assert raised.value.rule == 1


def test_an_issuer_that_is_not_a_ca_is_refused(revoked: set[str], trust: Trust) -> None:
    phone = Phone()
    chain = build_chain(phone, Spec(challenge=CHALLENGE), middle_ca=False)
    trust(chain)

    with pytest.raises(attestation.AttestationFailed) as raised:
        verify(phone, chain)

    assert raised.value.rule == 1


@pytest.mark.parametrize("position", [0, 1, 2])
def test_a_revoked_certificate_anywhere_in_the_chain_is_refused(
    revoked: set[str], trust: Trust, position: int
) -> None:
    phone = Phone()
    chain = good(phone, trust)
    revoked.add(chain.serials[position])

    with pytest.raises(attestation.AttestationFailed) as raised:
        verify(phone, chain)

    assert raised.value.rule == 1


@pytest.mark.parametrize(
    "chain", [[], ["not base64!"], ["AAAA"], ["QUJD"] * 11], ids=str
)
def test_a_chain_that_cannot_be_read_is_refused(chain: list[str]) -> None:
    with pytest.raises(attestation.AttestationFailed) as raised:
        attestation.verify_android_key(
            chain=chain, challenge=CHALLENGE, public_key=b"x"
        )

    assert raised.value.rule == 1


def test_the_revocation_list_is_cached(
    trust: Trust, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    def fetch() -> set[str]:
        calls.append(1)
        return {"abc"}

    monkeypatch.setattr(attestation, "_fetch_status", fetch)

    assert attestation.revoked_serials() == {"abc"}
    assert attestation.revoked_serials() == {"abc"}
    assert len(calls) == 1


def test_a_revocation_list_that_cannot_be_fetched_is_a_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail() -> set[str]:
        raise OSError("down")

    monkeypatch.setattr(attestation, "_fetch_status", fail)
    cache.clear()

    with pytest.raises(ServiceUnavailable):
        attestation.revoked_serials()


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self, limit: int = -1) -> bytes:
        return self.body if limit < 0 else self.body[:limit]


def fake_url(monkeypatch: pytest.MonkeyPatch, body: bytes) -> list[str]:
    seen: list[str] = []

    def urlopen(request: Any, timeout: float) -> FakeResponse:
        seen.append(request.full_url)
        return FakeResponse(body)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return seen


def test_the_status_list_is_read_by_serial(monkeypatch: pytest.MonkeyPatch) -> None:
    body = json.dumps(
        {
            "entries": {
                "2C8CDDDFD5E03BFC": {"status": "REVOKED"},
                "c8966fcb2fbb0d7a": {"status": "SUSPENDED", "reason": "SOFTWARE_FLAW"},
            }
        }
    ).encode()
    seen = fake_url(monkeypatch, body)

    assert attestation._fetch_status() == {"2c8cdddfd5e03bfc", "c8966fcb2fbb0d7a"}
    assert seen == ["https://android.googleapis.com/attestation/status"]


@pytest.mark.parametrize(
    "body",
    [b"not json", b'{"entries": []}', b"{}", b"[]", b'{"entries": null}'],
)
def test_a_status_list_of_the_wrong_shape_is_an_error(
    monkeypatch: pytest.MonkeyPatch, body: bytes
) -> None:
    fake_url(monkeypatch, body)

    with pytest.raises((ValueError, KeyError, TypeError)):
        attestation._fetch_status()


def test_a_status_list_that_is_too_large_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_url(monkeypatch, b" " * (attestation.MAX_STATUS_BYTES + 1))

    with pytest.raises(ValueError, match="too large"):
        attestation._fetch_status()


def test_the_shipped_roots_are_googles_and_intact() -> None:
    """Each root verifies its own signature, so a damaged copy of the file is caught."""
    text = Path(django_settings.DEVICE_KEY_TRUSTED_ROOTS_PATH).read_text()
    blocks = re.findall(
        r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", text, re.DOTALL
    )

    roots = [x509.load_pem_x509_certificate(block.encode()) for block in blocks]

    assert len(roots) == 2
    for root in roots:
        root.verify_directly_issued_by(root)
    assert len(attestation.trusted_root_keys()) == 2
