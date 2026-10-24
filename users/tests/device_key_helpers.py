"""A phone for the tests: a P-256 key pair that signs the texts of ADR 0018, and a fake Android attestation."""

import base64
import datetime
import uuid
from dataclasses import dataclass, field
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from rest_framework.response import Response
from rest_framework.test import APIClient

from users import device_keys
from users.attestation import ATTESTATION_OID
from users.tests.helpers import AUTH, PASSWORD, bearer, device

KEYS = f"{AUTH}/device-keys"
PACKAGE = "com.example.moneyscribe.test"
CERT_DIGEST = bytes(range(32))


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def spki(public_key: ec.EllipticCurvePublicKey) -> bytes:
    return public_key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )


class Phone:
    """The hardware key of a phone. ``sign`` is what the Secure Enclave or the Keystore does."""

    def __init__(self, curve: ec.EllipticCurve | None = None) -> None:
        self.private = ec.generate_private_key(curve or ec.SECP256R1())
        self.public = self.private.public_key()

    @property
    def spki_b64(self) -> str:
        return b64(spki(self.public))

    @property
    def raw_point_b64(self) -> str:
        return b64(
            self.public.public_bytes(
                serialization.Encoding.X962,
                serialization.PublicFormat.UncompressedPoint,
            )
        )

    def sign(self, message: bytes) -> str:
        return b64(self.private.sign(message, ec.ECDSA(hashes.SHA256())))


def ask_challenge(client: APIClient, device_id: str) -> dict[str, Any]:
    response = client.post(f"{KEYS}/challenge/", {"device_id": device_id})
    assert response.status_code == 200, response.json()
    body: dict[str, Any] = response.json()
    return body


def registration_body(
    phone: Phone,
    issued: dict[str, Any],
    *,
    device_id: str,
    user_id: str,
    password: str = PASSWORD,
    chain: list[str] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """The request of a correct registration; a test breaks one field through ``overrides``."""
    message = device_keys.registration_message(
        uuid.UUID(issued["challenge_id"]),
        issued["challenge"],
        uuid.UUID(device_id),
        uuid.UUID(user_id),
    )
    body: dict[str, Any] = {
        "current_password": password,
        "public_key": phone.spki_b64,
        "algorithm": "ES256",
        "challenge_id": issued["challenge_id"],
        "signature": phone.sign(message),
        "attestation_chain": chain,
    }
    return {**body, **overrides}


def register(client: APIClient, access_token: str, body: dict[str, Any]) -> Response:
    return client.put(f"{KEYS}/current/", body, headers=bearer(access_token))


def sign_in_body(
    phone: Phone,
    issued: dict[str, Any],
    *,
    device_id: str,
    platform: str = "ios",
    **overrides: Any,
) -> dict[str, Any]:
    message = device_keys.sign_in_message(
        uuid.UUID(issued["challenge_id"]), issued["challenge"], uuid.UUID(device_id)
    )
    body: dict[str, Any] = {
        "challenge_id": issued["challenge_id"],
        "signature": phone.sign(message),
        "device": device(device_id=device_id, platform=platform),
    }
    return {**body, **overrides}


def key_sign_in(
    client: APIClient,
    phone: Phone,
    device_id: str,
    platform: str = "ios",
    **overrides: Any,
) -> Response:
    issued = ask_challenge(client, device_id)
    return client.post(
        f"{KEYS}/sign-in/",
        sign_in_body(
            phone, issued, device_id=device_id, platform=platform, **overrides
        ),
    )


# --- a fake Android attestation ---------------------------------------------------------------


def _length(size: int) -> bytes:
    if size < 0x80:
        return bytes([size])
    raw = size.to_bytes((size.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def tlv(first: int, content: bytes) -> bytes:
    return bytes([first]) + _length(len(content)) + content


def der_int(value: int) -> bytes:
    size = max(1, (value.bit_length() + 8) // 8)
    return tlv(0x02, value.to_bytes(size, "big", signed=True))


def der_enum(value: int) -> bytes:
    return tlv(0x0A, bytes([value]))


def der_bool(value: bool) -> bytes:
    return tlv(0x01, b"\xff" if value else b"\x00")


def der_octets(value: bytes) -> bytes:
    return tlv(0x04, value)


def der_null() -> bytes:
    return tlv(0x05, b"")


def der_seq(*items: bytes) -> bytes:
    return tlv(0x30, b"".join(items))


def der_set(*items: bytes) -> bytes:
    return tlv(0x31, b"".join(items))


def ctx(number: int, inner: bytes) -> bytes:
    """An EXPLICIT context tag, with the multi-byte form for numbers from 31 up."""
    if number < 31:
        return tlv(0xA0 | number, inner)
    groups = []
    value = number
    while value:
        groups.append(value & 0x7F)
        value >>= 7
    encoded = bytes(
        [(part | 0x80) if index else part for index, part in enumerate(groups)]
    )
    return bytes([0xBF]) + encoded[::-1] + _length(len(inner)) + inner


@dataclass
class Spec:
    """What the fake attestation says. The defaults are a good key; a test changes one thing."""

    challenge: bytes
    attestation_level: int = 1
    key_level: int = 2
    purpose: tuple[int, ...] = (2,)
    algorithm: int = 3
    ec_curve: int | None = 1
    key_size: int | None = None
    origin: int = 0
    user_auth_type: int | None = 2
    no_auth_required: bool = False
    auth_timeout: int | None = None
    package: str = PACKAGE
    digests: tuple[bytes, ...] = field(default_factory=lambda: (CERT_DIGEST,))
    root_of_trust: bool = True
    locked: bool = True
    boot_state: int = 0
    app_id_in_hardware_list: bool = False

    def hardware_list(self) -> bytes:
        items = [
            ctx(1, der_set(*(der_int(p) for p in self.purpose))),
            ctx(2, der_int(self.algorithm)),
        ]
        if self.key_size is not None:
            items.append(ctx(3, der_int(self.key_size)))
        if self.ec_curve is not None:
            items.append(ctx(10, der_int(self.ec_curve)))
        if self.no_auth_required:
            items.append(ctx(503, der_null()))
        if self.user_auth_type is not None:
            items.append(ctx(504, der_int(self.user_auth_type)))
        if self.auth_timeout is not None:
            items.append(ctx(505, der_int(self.auth_timeout)))
        items.append(ctx(702, der_int(self.origin)))
        if self.root_of_trust:
            items.append(
                ctx(
                    704,
                    der_seq(
                        der_octets(b"\x00" * 32),
                        der_bool(self.locked),
                        der_enum(self.boot_state),
                        der_octets(b"\x00" * 32),
                    ),
                )
            )
        if self.app_id_in_hardware_list:
            items.append(ctx(709, der_octets(self.app_id())))
        return der_seq(*items)

    def app_id(self) -> bytes:
        return der_seq(
            der_set(der_seq(der_octets(self.package.encode()), der_int(1))),
            der_set(*(der_octets(d) for d in self.digests)),
        )

    def software_list(self) -> bytes:
        if self.app_id_in_hardware_list:
            return der_seq()
        return der_seq(ctx(709, der_octets(self.app_id())))

    def key_description(self) -> bytes:
        return der_seq(
            der_int(300),
            der_enum(self.attestation_level),
            der_int(300),
            der_enum(self.key_level),
            der_octets(self.challenge),
            der_octets(b""),
            self.software_list(),
            self.hardware_list(),
        )


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _certificate(
    *,
    subject: str,
    issuer: str,
    public_key: ec.EllipticCurvePublicKey,
    signer: ec.EllipticCurvePrivateKey,
    serial: int,
    ca: bool,
    extension: bytes | None = None,
    expired: bool = False,
) -> x509.Certificate:
    start = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
    end = datetime.datetime(2021 if expired else 2099, 1, 1, tzinfo=datetime.UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(_name(subject))
        .issuer_name(_name(issuer))
        .public_key(public_key)
        .serial_number(serial)
        .not_valid_before(start)
        .not_valid_after(end)
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
    )
    if extension is not None:
        builder = builder.add_extension(
            x509.UnrecognizedExtension(ATTESTATION_OID, extension), critical=False
        )
    return builder.sign(signer, hashes.SHA256())


@dataclass
class Chain:
    """A root, an intermediate and a leaf holding the phone's key, as the Keystore would send them."""

    root: x509.Certificate
    certificates: list[x509.Certificate]

    @property
    def b64(self) -> list[str]:
        return [
            b64(c.public_bytes(serialization.Encoding.DER)) for c in self.certificates
        ]

    @property
    def root_pem(self) -> str:
        return self.root.public_bytes(serialization.Encoding.PEM).decode()

    @property
    def serials(self) -> list[str]:
        return [format(c.serial_number, "x") for c in self.certificates]


def build_chain(
    phone: Phone,
    spec: Spec | None,
    *,
    expired_leaf: bool = False,
    leaf_key: Phone | None = None,
    middle_ca: bool = True,
) -> Chain:
    """The chain for ``phone``'s key. ``spec`` is the attestation extension; ``None`` leaves it out."""
    root_key = ec.generate_private_key(ec.SECP256R1())
    middle_key = ec.generate_private_key(ec.SECP256R1())
    root = _certificate(
        subject="Test Root",
        issuer="Test Root",
        public_key=root_key.public_key(),
        signer=root_key,
        serial=0xA1,
        ca=True,
    )
    middle = _certificate(
        subject="Test Intermediate",
        issuer="Test Root",
        public_key=middle_key.public_key(),
        signer=root_key,
        serial=0xB2,
        ca=middle_ca,
    )
    leaf = _certificate(
        subject="Test Leaf",
        issuer="Test Intermediate",
        public_key=(leaf_key or phone).public,
        signer=middle_key,
        serial=0xC3,
        ca=False,
        extension=spec.key_description() if spec is not None else None,
        expired=expired_leaf,
    )
    return Chain(root=root, certificates=[leaf, middle, root])
