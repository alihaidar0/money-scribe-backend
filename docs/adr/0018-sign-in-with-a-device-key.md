# ADR 0018: Sign-in with a device key pair and a signed challenge

- Status: accepted
- Date: 2026-10-24

## Context

After a sign-out the phone needs a way to prove who it is without the password. A secret stored on the phone and released
by a fingerprint check alone could be taken by a program that skips the check. A key pair created in the phone's
hardware, usable only after a fingerprint and invalidated when the fingerprints change, cannot be copied out, and the
server stores only the public half, so nothing on the server is secret and nothing can be replayed. This is the model
passkeys use.

Real passkeys (WebAuthn) are the standard, but they bind the credential to a domain (the relying-party id, and the
asset-links file on Android), and the project has no domain yet (`docs/steps.md`, section 12). So the protocol below is
custom, small and versioned: the signed texts begin with `v1`, and it moves to passkeys when the shared domain exists.
The request came from the Flutter developer and was agreed in the request issue.

## Decision

### Calls

All under `/api/v1/auth/device-keys/`. Only the key and the challenge are new data; the session that results is the
ordinary one (ADR 0006, ADR 0009).

| Operation | Auth | Answer |
| --- | --- | --- |
| `POST challenge/` with `device_id` | none | `200` `{challenge_id, challenge, expires_in}`; the same shape for any `device_id` |
| `PUT current/` with `current_password`, `public_key`, `algorithm`, `challenge_id`, `signature`, `attestation_chain` | bearer | `201` or `200` with the key entry; the device is the session's own `device_id` |
| `POST sign-in/` with `challenge_id`, `signature`, `device`, `language`, `time_zone` | none | the token pair and body of `sign-in` |
| `GET /` | bearer | the person's keys, `-created_at`, `-id`, in the envelope of every list (`next`, `previous`, `results`) |
| `DELETE {device_id}/` (`current` is this session's device) | bearer | `204`, also when nothing is there; another user's `device_id` is also `204` and changes nothing |

A key entry: `id`, `device_id`, `device_name`, `platform`, `attested`, `is_current`, `created_at`, `last_used_at`,
`expires_at`. The list holds at most 5 entries, so it is always one page: `next` and `previous` are `null`, and the
cursor class of ADR 0003 (step 8.4) replaces this one-page answer later without a change of shape. A `DELETE` of a
`device_id` that is not the caller's answers `204` and changes nothing, so it is the one place that does not answer
`404` for another user's object: it must not tell whether the device has a key.
The keys have their own list, because a signed-out phone has no session but its key must stay visible and removable
from another device. The `device_id` is the random install id of ADR 0009.

### The challenge

32 random bytes, sent base64url, valid 2 minutes, single use. It is held in the cache only (an atomic get-and-delete),
never in the database, and never logged. It is stored with the `device_id` it was asked for; sign-in with another
`device_id` fails. A request for any `device_id`, known or not, gets the same shape, so the endpoint reveals nothing. If
the cache is down, the calls fail closed with `503 service_unavailable`. A person who takes longer than 2 minutes at the
fingerprint prompt gets `challenge_invalid` and starts again with a new challenge and a new key.

### Signing

ECDSA on P-256 with SHA-256; the signature is DER (X9.62). `public_key` is SPKI DER; the raw 65-byte uncompressed point
(what the iOS Secure Enclave exports) is also accepted and stored as SPKI. Both `public_key` and `signature` are standard
base64 with padding, decoded strictly. `algorithm` is `ES256` only: the Secure Enclave has no RSA, and a second algorithm
only adds code. The signed text is UTF-8, lines joined by `\n`, with no trailing newline; the `challenge` line is the
base64url text exactly as sent:

- sign-in: `money-scribe/device-sign-in/v1`, `challenge_id`, `challenge`, `device_id`
- registration: `money-scribe/device-key-registration/v1`, `challenge_id`, `challenge`, `device_id`, `user_id`

The first line separates the two uses, so a signature for one can never be accepted for the other. `user_id` is the
account id in lower case with hyphens.

### Registration

- Bearer token and `current_password`. A wrong one is `400 invalid_password` on `current_password`, with the lock shared
  with the password change and the account deletion (`429 password_attempts_exceeded`, ADR 0014).
- The signature must verify against the submitted public key, so the phone proves it holds the private key, even without
  attestation (`400 challenge_invalid` for an unknown, used or expired challenge or a bad proof; `400 public_key_invalid`
  for a key that is not on the P-256 curve).
- One key per `device_id`. A new key replaces the old one, also when another account held it (a phone changes hands).
  At most 5 keys per account; the sixth is `409 device_key_limit_reached`.
- A `web` session is refused with `400 platform_not_supported`.
- The platform and device name come from the session's device record, not from the request.
- The owner is emailed, in their language and with no link, that fingerprint sign-in was turned on for the device.

### Android attestation

Required for an Android session. The attestation challenge is the 32 raw bytes of the registration `challenge` (decoded
from base64url), compared with the attested challenge as bytes. The server accepts the key when:

1. the chain leads to a trusted Google attestation root, no certificate is on Google's published revocation list, and the
   leaf key is the submitted `public_key`;
2. the attested challenge equals the server's bytes;
3. the key is hardware-backed (TEE or StrongBox), generated on the device, for signing only, EC P-256;
4. user authentication is required for every use (no timeout, not `noAuthRequired`) by a strong biometric only, with no
   device credential;
5. the attested application id and signing certificates match the allowed package and certificate list of the
   environment;
6. the verified boot state is Verified and the device is locked. A self-signed or unlocked device is refused: for a
   finance app the boot chain is part of what the attestation vouches for.

Anything else is `400 attestation_invalid` on `attestation_chain`, with no reason in the answer: the person cannot fix it,
and the reason would help only someone probing the check. The server logs the failed rule number, never the chain.

Configuration, per environment, never in the repository (`.env.example` lists the names with empty values):

- the allowed package (one per environment: the production package, and the staging flavor's own package) and a **list**
  of allowed signing-certificate SHA-256 digests; production needs the upload key and, once the app is on Google Play, the Play
  app-signing key;
- the trusted roots are a public PEM file in the repository, updated by pull request when Google rotates them;
- Google's revocation list is fetched at registration and cached for an hour, after the chain itself has checked out;
  if it cannot be fetched and nothing is cached, registration answers `503 service_unavailable`. Certificate validity
  dates are not checked, because Google states that the attestation keys of devices launched before 2021 have expired
  certificates and are still trustworthy unless revoked;
- `DEVICE_KEY_REQUIRE_ATTESTATION` defaults to true. It may be false only in development and tests, so an emulator with a
  software key can be used there; a settings check refuses false when `APP_ENVIRONMENT` is staging or production,
  as for the outbox (ADR 0010).

### iOS

There is no key attestation. After the proof of possession the key is accepted with `attested: false`. Registration still
needs the password and a signature, so the weakest case is a phone whose owner knows the password and registers a software
key; the privacy text says that iOS keys are not verified as hardware-bound. App Attest and Play Integrity belong to the
app-attestation decision for the whole API (`docs/steps.md`, section 11), not to this feature.

### Sign-in

- No password and no email; the key identifies the account, which must be verified and active.
- Every failure is one `401 device_sign_in_failed`: unknown device, wrong, used or expired challenge, a challenge asked
  for another device, bad signature, expired key, inactive account. For an unknown device the server verifies against a
  fixed dummy key so the time does not tell the cases apart.
- Own limits, independent of the password sign-in lock and the bot challenge (neither feeds the other): 5 failures per
  `device_id` and 20 per client address in 15 minutes, then `429 sign_in_locked` with `Retry-After`. The limit store being
  down is `503 service_unavailable`.
- A success creates a normal session, with the `device` object, `language` and `time_zone` as at sign-in, and renews the
  key's `expires_at`.

### Lifetime and removal

- A key expires 30 days after its last use or its registration. Expiry is checked at sign-in, so correctness does not
  depend on a job; a daily purge joins the Celery Beat jobs when a worker runs.
- All of a person's keys are deleted in the same transaction when the password is changed or reset, when the email is
  changed, and when the account is deleted. A key is deleted when the person turns the switch off or removes it from
  another device.
- Signing out and ending a session do not delete the key: that is the point of the feature.

### Privacy and logging

The server keeps the public key, the `device_id`, the device name, the platform, the `attested` flag and the timestamps
until the key is removed, and at most 30 days after its last use (listed in ADR 0009's privacy table). A challenge lives
at most 2 minutes and is never stored. A challenge, a signature, a public key and an attestation chain are never written
to a log.

### Error codes

New codes, added to ADR 0004: `device_sign_in_failed` (401), `challenge_invalid` (400), `public_key_invalid` (400),
`attestation_invalid` (400), `platform_not_supported` (400), `device_key_limit_reached` (409).

## Consequences

- The `users` app gains a `DeviceKey` model (an owner, a unique `device_id`, the SPKI bytes, the algorithm, the platform,
  the device name, `attested` and the timestamps) and a service module for the checks. The verification uses the
  `cryptography` package, which the project did not have and now lists as a production dependency; the attestation
  extension is read with a small strict DER reader (`users/der.py`) rather than another dependency.
- Moving to passkeys later is a new set of operations with their own record; `v1` keeps the old signatures valid until
  the keys are retired.
- The Flutter developer sends the package name and the signing-certificate digests of each flavor; the owner sets them in
  the host's environment.
- The tests cover a valid registration and sign-in with a generated key, each rule of the attestation (with a fixture
  chain), a replayed challenge, a challenge for another device, a signature for the other text, the removal on password
  change, reset, email change and deletion, the limits, and the isolation helper for the list and the removal.
- Rejected: RS256, a password-free fallback for web, and keeping the key after a password change (a password change is
  the person's answer to "my account may be compromised", so every key goes with it).
