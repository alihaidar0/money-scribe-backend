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

**Sign-in protection.** Sign-in is throttled per account and per client address, with counters in Redis. After too many
failures the account is locked for a time, and the response is `429` with `Retry-After` (`sign_in_locked`, ADR 0004). The
limits are set in the implementation. The 4-digit email codes are stored hashed, expire (10 minutes), allow a limited
number of attempts, and have a resend cooldown that also answers `429` with `Retry-After`.

**What the API reveals.** Registration, and changing the email, answer `409 email_already_registered` when the address is
taken, because the client must show a clear message; both operations are throttled per address and per client. Signing in
and requesting a password reset reveal nothing: `invalid_credentials` for any failure, and one fixed answer for a reset
request whether the address exists or not.

**Passwords.** Argon2id is the first entry of `PASSWORD_HASHERS` (the `argon2-cffi` package); PBKDF2 stays in the list so
that existing hashes still verify and are upgraded at the next sign-in. The password rules are in ADR 0001.

**Not chosen.** Server sessions with cookies for the mobile apps, and DRF's `TokenAuthentication` (tokens that never
expire, one per user).

## Open implementation choice

The library is chosen in the authentication pull request and recorded there. It must meet every rule above: a pinned
algorithm, single-use refresh tokens with the replay window and session-ending reuse detection, hashed storage, the
`at+jwt` type, and the channel rule for cookies. The candidate is PyJWT with the `Session` model in `users`.
`djangorestframework-simplejwt` is acceptable only if it can do all of it; as far as we know, its token blacklist alone
does not cover the replay window or end the session on reuse, so that would need to be checked first.

## Consequences

- The OpenAPI schema declares one HTTP bearer security scheme, and the sign-in, refresh and sign-out operations show both
  client forms (body and cookie). The operations are specified in the endpoint request issues (`docs/api-workflow.md`).
- Every request costs one indexed query for the session and the user, which an ended session needs to be refused at once.
- `users` gains the `Session` model and the signing-key, lifetime and replay-window settings, which go into
  `.env.example` and `docker-compose.yml`, and the web origins and cookie settings.
- A token leak is bounded: a stolen access token stops working when its session ends or after 10 minutes, and a stolen
  refresh token can be used once; a later presentation of the same token, by the thief or by the app, ends the session.
