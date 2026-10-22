# ADR 0006: Authentication with a short-lived access token and a rotating refresh token

- Status: accepted
- Date: 2026-10-15

## Context

The client is a mobile and web app that signs in with an email address and a password, stays signed in for weeks, shows
the user where they are signed in, and can end one session or all of them. The server holds all financial data, so a
stolen token must be short-lived or revocable. This follows the current OAuth guidance for tokens (RFC 9700), the JWT
best practices (RFC 8725) and the JWT access token profile (RFC 9068) where they apply, and the guidance for browser
apps, which keeps long-lived tokens out of reach of page scripts.

## Decision

Two tokens, both JSON Web Tokens (JWT), issued after sign-in and after the email code is accepted.

| | Access token | Refresh token |
| --- | --- | --- |
| Use | `Authorization: Bearer <token>` on every API call | only to obtain a new pair, `POST /api/v1/auth/refresh` |
| Lifetime | 10 minutes | 30 days, renewed by each rotation, with an absolute limit of 90 days per session |
| State | checked by signature, claims and its session | tracked on the server, one row per session |
| Reuse | any number of times until it expires | **once**: every refresh returns a new access and refresh token and retires the old one |

All lifetimes are settings, set through environment variables.

**Claims.** Access token: `iss`, `aud`, `sub` (the user id), `iat`, `exp`, `jti` and `sid` (the session id), with the header
type `at+jwt`. Refresh token: the same plus `typ: refresh` in the payload. A token of one kind is rejected where the
other is expected.

**Signing.** The algorithm is pinned and never read from the token (RFC 8725): HS256 with a dedicated signing key, at
least 256 random bits, kept in the environment and separate from Django's `SECRET_KEY`. The header carries a `kid`, so a
new key can be introduced while the previous one still verifies. Switch to an asymmetric algorithm such as EdDSA if another
service ever needs to verify tokens.

**Sessions.** A session row (model `Session` in `users`, detailed in ADR 0009) stores the session id, the user, the SHA-256
hash of the current refresh token (never the token), the device fields, and when it was created, last active, expires and
ended. Every API call loads the session together with the user, in one query by `sid`. A request is rejected with `401`
and the matching `session_*` code (ADR 0004) when the session is missing, ended or expired, and when the user is missing
or inactive. A session that is ended therefore loses access **at once**, not when the access token expires.

**Rotation, replay window and reuse.**

- A refresh with the current refresh token rotates it: the session stores the hash of the new token, and the answer is a
  new access token and a new refresh token.
- **A lost response must not end the session.** A mobile app can lose the answer after the server rotated the token and
  then retries with the old one. A refresh token that the server rotated less than 30 seconds ago (`REFRESH_REPLAY_WINDOW`,
  counted from the moment of that rotation, not from the client's first attempt) is accepted again as a retry and returns
  the **same** successor refresh token. The successor is derived deterministically
  from the session and its rotation, so it can be produced again without storing the token. Two browser tabs that refresh
  at the same moment therefore converge on the same token.
- A refresh token that is older than the window and already used is theft or a bug: the **whole session is ended** with
  the reason `token_reuse`, the answer is `401` with `session_token_reuse`, and the user signs in again.
- A client still sends one refresh at a time inside a process.

**Ending sessions.**

| Event | Sessions ended |
| --- | --- |
| Sign out | that session |
| Password reset (forgotten password) | all of the user's sessions |
| Password change while signed in | all other sessions; the current one continues with rotated tokens |
| Email change | all other sessions; the current one continues |
| The user ends one session or all others | the chosen ones |
| Replay of a used refresh token | that session |
| Account deletion | all, with the account |

Each ended session keeps its `end_reason` (ADR 0009), and its push token is removed (ADR 0005).

**Clients.** The mode of a client is decided by **how a token arrives**, never by a header the page could omit.

- **Mobile (Android, iOS).** The refresh token travels in the JSON body and is kept in the platform's secure storage. The
  access token stays in memory.
- **Web.** The server sets the refresh token as a cookie that script cannot read: `HttpOnly`, `Secure`, `SameSite=Strict`,
  host-only (no `Domain`), limited to the auth paths (`/api/v1/auth/`) and named with the `__Secure-` prefix. It never
  appears in a JSON body. Sign-in and the email-code verification choose this mode when the request carries the header
  `X-Client: web`; **refresh and sign-out follow the channel**: a refresh token that arrived in the cookie is answered
  with a cookie and never with a body. A script that omits the header therefore cannot make the server hand it the
  long-lived token. The access token stays in memory; after a page reload the app refreshes through the cookie.
- **Cookie requests are protected against cross-site use** by `SameSite=Strict`, an `Origin` check against the allowed
  list, and a required custom header (`X-Client: web`, which also forces a CORS preflight).
- **CORS** allows credentials and the `X-Client` header for an explicit list of origins (`CORS_ALLOWED_ORIGINS`), never `*`.
- **Same site.** The cookie works when the web app and the API share one registrable domain (for example `app.<domain>`
  and `api.<domain>`); `localhost` works in development. The domain for staging and production is chosen with the
  deployment step (`docs/steps.md`) and is needed before the web app is deployed.

**Sign-in protection.** Sign-in is throttled per account and per client address, with counters in Redis. Failures lock the
pair of account and client address, so someone who only knows an email address cannot lock its owner out from another
network; failures from all addresses together lock the account for everyone only far above that (NIST allows up to 100
consecutive failures). The response is `429` with `Retry-After` (`sign_in_locked`, ADR 0004). The limits are set in the
implementation. The 6-digit email codes (NIST asks for at least six) are stored hashed, expire (10 minutes), allow a limited
number of attempts, and have a resend cooldown and an hourly limit that also answer `429` with `Retry-After`.

**Fail closed.** The limits live in Redis. If it is down, the calls that depend on a limit (register, resend, verify,
sign-in) answer `503 service_unavailable` with `Retry-After` instead of running without their brake. Calls that need no limit
keep working: a refresh, sign-out and every request with an access token.

**What the API reveals.** As little as the client can live with (OWASP). Registration answers the same whether the address
is new, pending or already registered: a verified address is not changed, and its owner gets an email that says someone
tried, with no code; a pending one is registered again. Registering is throttled per address and per client, and a verified
address spends the same time and the same budget as a new one. Requesting a password reset reveals nothing: one fixed
answer whether the address exists or not. Signing in does tell an unknown email from a wrong password
([ADR 0012](0012-sign-in-names-the-field-at-fault.md)), with a limit per client address as its brake. Changing
the email, which needs a signed-in session and the password, still answers `409 email_already_registered`.

**Passwords.** Argon2id is the first entry of `PASSWORD_HASHERS` (the `argon2-cffi` package); PBKDF2 stays in the list so
that existing hashes still verify and are upgraded at the next sign-in. The password rules are in ADR 0001.

**Not chosen.** Server sessions with cookies for the mobile apps, and DRF's `TokenAuthentication` (tokens that never
expire, one per user).

## Implementation (step 8.2)

- **Library: PyJWT**, with our own `Session` model in `users`. `djangorestframework-simplejwt` was not chosen: its token
  blacklist covers neither the replay window nor ending the session on reuse. The algorithm is pinned to HS256 in
  `users/tokens.py` and the key ring is read by `kid`; expiry is checked against `timezone.now()` rather than inside the
  library, so one clock drives the sessions and the tests.
- **Deterministic refresh token.** Its `jti` and `iat` come from the session id, the rotation counter and the time of the
  rotation, so a retry inside the replay window rebuilds the same successor without storing it. Only its SHA-256 hash is
  stored. Its life is 30 days from the rotation, never past the session's 90 day limit.
- **Key rotation.** `JWT_KEY_ID` names the current key and `JWT_PREVIOUS_KEYS` (`kid:key,kid:key`) lists the keys that
  still verify. The system check `users.E001` refuses a missing or shorter than 32 byte `JWT_SIGNING_KEY`.
- **A missing session or an inactive user** answers `401 token_invalid`: there is no ended session to name a reason (an
  account deletion removes its sessions). An ended session answers its own `session_*` code.
- **Sign-out** accepts the bearer access token, or the refresh token in the body when the access token has expired, so a
  device can end its session without refreshing first. With neither valid it answers `401 token_invalid`.
- **A code that is dead or unknown.** After too many wrong tries a code is dead and the answer is `429
  code_attempts_exceeded`; `Retry-After` is the wait before a new code may be requested. An address with nothing to verify
  is answered by the same counter, so it cannot be told apart from a real one.
- **Limits** are counted in the cache (Redis; in-memory in tests) by normalized email, whether or not an account exists,
  and per client address through an atomic throttle (`NUM_PROXIES` must be set behind a proxy). Counts are one atomic
  increment and a cooldown is one `SET NX`, never a read followed by a write, so parallel requests cannot slip past a limit
  together; the windows are fixed windows, which allow a short burst at a window boundary. Five failed sign-ins from one
  address lock that account for that address for 15 minutes, 50 from all addresses lock it for everyone, 15 from one
  address over all accounts lock the address (ADR 0012), and a locked account or address answers `429 sign_in_locked` even for the right password. A code has 6 digits, lives 10 minutes, allows 5 wrong
  tries, may be sent again after 60 seconds and at most 5 times an hour; a refused attempt does not count as a send.
- **The client behind a limit** is an IPv4 address or an IPv6 /64 (one customer owns the whole /64, so choosing another
  address inside it must not start the count again). The address comes from `REMOTE_ADDR`, or from `X-Forwarded-For` when
  `NUM_PROXIES` is set to the number of trusted reverse proxies in front of the app (DRF counts that many from the right,
  so a spoofed header is ignored). It is 0 in development; the hosting step sets it, and the proxy must append to the
  header. Many users behind one shared address (an office, a mobile carrier) share its limits: an attacker there can lock
  an account only for that address, never for users on other networks.
- **Verifying cannot tell who has registered.** For every address the server keeps the same state: when a code was last
  sent, and the wrong tries since. A real pending account, an unknown address and an already registered one therefore
  answer alike at every step: `code_expired` when no code was sent in the last ten minutes, `code_invalid` with
  `attempts_left` counting down, `429 code_attempts_exceeded` with the same `Retry-After`, and a new code starts over
  (a test runs one long story against all three). Register and resend draw on one budget keyed by the address, so their
  `429` answers are the same for every address too. A residual difference is time: resend sends an email only for a
  pending account. It disappears when email delivery becomes asynchronous with the real delivery step (docs/steps.md), which
  must keep the plain code out of task arguments and logs.
- **Redis down** is `503` for the limited calls (see Fail closed). A refresh, a sign-out and every request with an access
  token read only the database and never answer it. The event is reported like any server error.
- **Registration** takes the same time for a new, a pending and a verified address: all three hash the password, and all
  three send one email.
- **Data minimisation.** An unverified account is deleted with its codes after seven days of inactivity (ADR 0001).
- **Browsers** read `Retry-After` because it is listed in `CORS_EXPOSE_HEADERS`.

## Consequences

- The OpenAPI schema declares one HTTP bearer security scheme, and the sign-in, refresh and sign-out operations show both
  client forms (body and cookie). The operations are specified in the endpoint request issues (`docs/api-workflow.md`).
- Every request costs one indexed query for the session and the user, which an ended session needs to be refused at once.
- `users` gains the `Session` model and the signing-key, lifetime and replay-window settings, which go into
  `.env.example` and `docker-compose.yml`, and the web origins and cookie settings.
- A token leak is bounded: a stolen access token stops working when its session ends or after 10 minutes, and a stolen
  refresh token can be used once; a later presentation of the same token, by the thief or by the app, ends the session.
