"""The check of an Android key attestation (ADR 0018), free of HTTP.

When the phone creates its signing key it asks the hardware to certify it: the answer is a certificate chain
whose leaf holds the public key and an extension that says how the key lives. This module accepts a key only
when the chain leads to a Google attestation root and every rule below holds. A rule that fails raises
:class:`AttestationFailed` with its number, which is logged; the client is only told that the attestation
was not accepted (``attestation_invalid``), because the person cannot fix it.

1. The chain is signed link by link up to a trusted Google root, no certificate of it is on Google's list
   of revoked attestation keys, and the leaf holds the submitted public key.
2. The attested challenge is the server's (the registration challenge, as bytes).
3. The key is hardware-backed (TEE or StrongBox), generated on the device, and made for signing only with
   an EC P-256 key.
4. Every use needs the user: no ``noAuthRequired``, no timeout, a fingerprint-class biometric and no
   device credential.
5. The app is the one of this environment: its package name and every signing-certificate digest are on the
   configured lists.
6. The verified boot state is Verified and the device is locked.

Certificate validity dates are not checked: Google states that the attestation keys of devices launched
before 2021 have expired certificates and are still trustworthy unless revoked, so the revocation list is the
control, not the clock. The layout and the numbers come from Google's key attestation documentation and the
KeyMint AIDL definitions (``KeyPurpose.SIGN`` 2, ``Algorithm.EC`` 3, ``EcCurve.P_256`` 1, ``KeyOrigin.GENERATED`` 0,
``HardwareAuthenticatorType.FINGERPRINT`` 2).
"""

import base64
import binascii
import functools
import hmac
import itertools
import json
import logging
import re
import urllib.request
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from django.conf import settings
from django.core.cache import cache

from config.exceptions import service_unavailable

from . import der
from .limits import fail_closed

logger = logging.getLogger(__name__)

ATTESTATION_OID = x509.ObjectIdentifier("1.3.6.1.4.1.11129.2.1.17")
# The list of attestation keys Google has revoked or suspended; its entries are keyed by certificate serial.
REVOCATION_URL = "https://android.googleapis.com/attestation/status"
MAX_CHAIN_LENGTH = 10
MAX_STATUS_BYTES = 8_000_000
_REVOCATION_CACHE_KEY = "devicekey:revoked"

# AuthorizationList tags, and the values they carry (see the module docstring for the sources).
TAG_PURPOSE = 1
TAG_ALGORITHM = 2
TAG_KEY_SIZE = 3
TAG_EC_CURVE = 10
TAG_NO_AUTH_REQUIRED = 503
TAG_USER_AUTH_TYPE = 504
TAG_AUTH_TIMEOUT = 505
TAG_ORIGIN = 702
TAG_ROOT_OF_TRUST = 704
TAG_APPLICATION_ID = 709
PURPOSE_SIGN = 2
ALGORITHM_EC = 3
EC_CURVE_P256 = 1
ORIGIN_GENERATED = 0
AUTH_PASSWORD = 1
AUTH_FINGERPRINT = 2
SECURITY_SOFTWARE = 0
BOOT_VERIFIED = 0


class AttestationFailed(Exception):
    """A rule of the attestation does not hold; ``rule`` is its number (1 to 6)."""

    def __init__(self, rule: int, reason: str) -> None:
        super().__init__(f"rule {rule}: {reason}")
        self.rule = rule
        self.reason = reason


def _public_key_der(key: Any) -> bytes:
    return bytes(
        key.public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )


@functools.cache
def _load_root_keys(path: str) -> frozenset[bytes]:
    text = Path(path).read_text()
    blocks = re.findall(
        r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", text, re.DOTALL
    )
    return frozenset(
        _public_key_der(x509.load_pem_x509_certificate(block.encode()).public_key())
        for block in blocks
    )


def trusted_root_keys() -> frozenset[bytes]:
    """The public keys (SPKI) of the roots a chain may end in."""
    return _load_root_keys(str(settings.DEVICE_KEY_TRUSTED_ROOTS_PATH))


def _fetch_status() -> set[str]:
    """Google's list of attestation keys that are not good: the serials, in lowercase hex."""
    request = urllib.request.Request(
        REVOCATION_URL,
        headers={"User-Agent": "money-scribe-backend"},
    )
    with urllib.request.urlopen(  # noqa: S310 - a fixed https URL from the settings
        request, timeout=settings.DEVICE_KEY_REVOCATION_TIMEOUT_SECONDS
    ) as response:
        raw = response.read(MAX_STATUS_BYTES + 1)
    if len(raw) > MAX_STATUS_BYTES:
        raise ValueError("The revocation list is too large.")
    entries = json.loads(raw.decode())["entries"]
    if not isinstance(entries, dict):
        raise ValueError("The revocation list has no entries.")
    return {str(serial).lower() for serial in entries}


@fail_closed
def revoked_serials() -> frozenset[str]:
    """The revoked serials, kept in the cache for an hour; ``503`` if they cannot be had at all.

    A key that cannot be checked against the list is not accepted: the registration is refused with
    ``service_unavailable`` and the person tries again, rather than a revoked key being let through.
    """
    cached = cache.get(_REVOCATION_CACHE_KEY)
    if cached is not None:
        return frozenset(cached)
    try:
        serials = _fetch_status()
    except OSError, ValueError, KeyError, TypeError:
        logger.error("The attestation revocation list could not be fetched.")
        raise service_unavailable() from None
    cache.set(
        _REVOCATION_CACHE_KEY,
        sorted(serials),
        timeout=settings.DEVICE_KEY_REVOCATION_CACHE_SECONDS,
    )
    return frozenset(serials)


def _parse_chain(chain: list[str]) -> list[x509.Certificate]:
    if not 1 <= len(chain) <= MAX_CHAIN_LENGTH:
        raise AttestationFailed(1, "the chain has no certificates or too many")
    try:
        return [
            x509.load_der_x509_certificate(base64.b64decode(item, validate=True))
            for item in chain
        ]
    except ValueError, binascii.Error:
        raise AttestationFailed(1, "a certificate cannot be read") from None


def _check_chain(certificates: list[x509.Certificate], public_key: bytes) -> None:
    """Rule 1, apart from the revocation list."""
    for certificate, issuer in itertools.pairwise(certificates):
        try:
            certificate.verify_directly_issued_by(issuer)
        except ValueError, TypeError, InvalidSignature, NotImplementedError:
            raise AttestationFailed(
                1, "a certificate is not signed by the next"
            ) from None
    for issuer in certificates[1:]:
        try:
            constraints = issuer.extensions.get_extension_for_class(
                x509.BasicConstraints
            ).value
        except x509.ExtensionNotFound:
            raise AttestationFailed(1, "an issuer is not marked as a CA") from None
        if not constraints.ca:
            raise AttestationFailed(1, "an issuer is not a CA")
    root = certificates[-1]
    try:
        root.verify_directly_issued_by(root)
    except ValueError, TypeError, InvalidSignature, NotImplementedError:
        raise AttestationFailed(1, "the last certificate is not a root") from None
    if _public_key_der(root.public_key()) not in trusted_root_keys():
        raise AttestationFailed(1, "the root is not a trusted Google root")
    if _public_key_der(certificates[0].public_key()) != public_key:
        raise AttestationFailed(1, "the leaf holds another key")


def _check_revocation(certificates: list[x509.Certificate]) -> None:
    revoked = revoked_serials()
    for certificate in certificates:
        if format(certificate.serial_number, "x") in revoked:
            raise AttestationFailed(1, "a certificate is on the revocation list")


def _key_description(certificates: list[x509.Certificate]) -> der.Tlv:
    """The first key description in the chain, from the leaf up: only that one can be trusted."""
    for certificate in certificates:
        try:
            extension = certificate.extensions.get_extension_for_oid(ATTESTATION_OID)
        except x509.ExtensionNotFound:
            continue
        value = extension.value
        if not isinstance(value, x509.UnrecognizedExtension):
            raise AttestationFailed(2, "the attestation extension is not readable")
        try:
            return der.read(value.value)
        except der.DerError:
            raise AttestationFailed(
                2, "the attestation extension is not valid"
            ) from None
    raise AttestationFailed(2, "the chain has no attestation extension")


def _integer_set(element: der.Tlv) -> set[int]:
    return {der.integer(item) for item in der.set_of(element)}


def _need(fields: dict[int, der.Tlv], tag: int, rule: int) -> der.Tlv:
    try:
        return fields[tag]
    except KeyError:
        raise AttestationFailed(rule, f"the hardware list lacks tag {tag}") from None


def _check_key_properties(hardware: dict[int, der.Tlv]) -> None:
    """Rule 3 (the key itself) and rule 4 (every use needs the user), read from the hardware list."""
    try:
        if _integer_set(_need(hardware, TAG_PURPOSE, 3)) != {PURPOSE_SIGN}:
            raise AttestationFailed(3, "the key is not for signing only")
        if der.integer(_need(hardware, TAG_ALGORITHM, 3)) != ALGORITHM_EC:
            raise AttestationFailed(3, "the key is not an EC key")
        if TAG_EC_CURVE in hardware:
            curve_ok = der.integer(hardware[TAG_EC_CURVE]) == EC_CURVE_P256
        else:  # Keymaster 3 and 4 name no curve, only a 256-bit size
            curve_ok = der.integer(_need(hardware, TAG_KEY_SIZE, 3)) == 256
        if not curve_ok:
            raise AttestationFailed(3, "the key is not on P-256")
        if der.integer(_need(hardware, TAG_ORIGIN, 3)) != ORIGIN_GENERATED:
            raise AttestationFailed(3, "the key was not generated on the device")
        if TAG_NO_AUTH_REQUIRED in hardware:
            raise AttestationFailed(4, "the key needs no authentication")
        auth_type = der.integer(_need(hardware, TAG_USER_AUTH_TYPE, 4))
        if not auth_type & AUTH_FINGERPRINT or auth_type & AUTH_PASSWORD:
            raise AttestationFailed(4, "the key does not need a biometric alone")
        if (
            TAG_AUTH_TIMEOUT in hardware
            and der.integer(hardware[TAG_AUTH_TIMEOUT]) != 0
        ):
            raise AttestationFailed(4, "the key stays unlocked for a while")
    except der.DerError:
        raise AttestationFailed(3, "the hardware list is not valid") from None


def _check_application(
    software: dict[int, der.Tlv], hardware: dict[int, der.Tlv]
) -> None:
    """Rule 5: the app is the one of this environment."""
    allowed_package = settings.DEVICE_KEY_ANDROID_PACKAGE
    allowed_digests = set(settings.DEVICE_KEY_ANDROID_CERT_SHA256)
    if not allowed_package or not allowed_digests:
        logger.error("The Android package and certificate list are not configured.")
        raise AttestationFailed(5, "the allowed app is not configured")
    field = software.get(TAG_APPLICATION_ID) or hardware.get(TAG_APPLICATION_ID)
    if field is None:
        raise AttestationFailed(5, "the attestation names no app")
    try:
        packages, digests = der.sequence(der.read(der.octets(field)))
        names = {
            der.octets(der.sequence(info)[0]).decode() for info in der.set_of(packages)
        }
        found = {der.octets(item).hex() for item in der.set_of(digests)}
    except der.DerError, IndexError, UnicodeDecodeError, ValueError:
        raise AttestationFailed(5, "the app id is not valid") from None
    if not names or names != {allowed_package}:
        raise AttestationFailed(5, "the package is not the allowed one")
    if not found or not found <= allowed_digests:
        raise AttestationFailed(5, "the signing certificate is not an allowed one")


def _check_boot(hardware: dict[int, der.Tlv]) -> None:
    """Rule 6: the boot chain is verified and the device is locked."""
    try:
        parts = der.sequence(hardware[TAG_ROOT_OF_TRUST])
        locked = der.boolean(parts[1])
        state = der.enumerated(parts[2])
    except KeyError, IndexError, der.DerError:
        raise AttestationFailed(
            6, "the root of trust is missing or not valid"
        ) from None
    if not locked or state != BOOT_VERIFIED:
        raise AttestationFailed(6, "the device is unlocked or the boot is not verified")


def verify_android_key(
    *, chain: list[str], challenge: bytes, public_key: bytes
) -> None:
    """Raise :class:`AttestationFailed` unless the chain proves a good key.

    ``chain`` is the list of base64 DER certificates, leaf first; ``challenge`` is the registration
    challenge as bytes; ``public_key`` is the submitted key (SPKI DER).
    """
    certificates = _parse_chain(chain)
    _check_chain(certificates, public_key)
    _check_revocation(certificates)
    description = _key_description(certificates)
    try:
        fields = der.sequence(description)
        attestation_level = der.enumerated(fields[1])
        key_level = der.enumerated(fields[3])
        attested_challenge = der.octets(fields[4])
        software = der.context_fields(fields[6])
        hardware = der.context_fields(fields[7])
    except der.DerError, IndexError:
        raise AttestationFailed(2, "the key description is not valid") from None
    if not hmac.compare_digest(attested_challenge, challenge):
        raise AttestationFailed(2, "the attested challenge is not the server's")
    if SECURITY_SOFTWARE in (attestation_level, key_level):
        raise AttestationFailed(3, "the key is not hardware-backed")
    _check_key_properties(hardware)
    _check_application(software, hardware)
    _check_boot(hardware)
