# ADR 0013: A bot challenge for sign-in after repeated failures

- Status: accepted
- Date: 2026-10-21

## Context

[ADR 0012](0012-sign-in-names-the-field-at-fault.md) lets sign-in say whether an email has an account. The limit per
client address slows one address but not a client that rotates addresses, and ADR 0012 named a bot challenge as the real
mitigation. The challenge has to be free, cost real users nothing, and work in a Flutter app on Android, iOS and the web.

## Decision

**Cloudflare Turnstile**, checked on the server.

- **Why this one.** The free plan has no limit on challenges (Cloudflare's plans page), it needs no payment, and it shows
  most people no puzzle. Google reCAPTCHA cut its free tier to 10,000 assessments a month in 2026, and hCaptcha's free tier
  shows visible challenges. Flutter has community packages for Turnstile (Cloudflare publishes none), so the client
  developer can build it at no cost; where a package does not fit, a web view of the widget works on every platform.
- **Adaptive, not always.** A client address is asked for a challenge only after it has failed 5 sign-ins in the window
  (`SIGN_IN_CHALLENGE_AFTER_FAILURES`), so a person who types their password right never sees one. The hard stop at 15
  failures (ADR 0012) stays behind it: 5 failures, then 10 more with a solved challenge each, then the address lock.
- **The contract.** `POST /api/v1/auth/sign-in/` takes an optional `captcha_token`. When the address must be challenged:
  no token is `400 captcha_required` (`attr` `captcha_token`) and costs nothing; a token that does not verify is
  `400 captcha_invalid` and counts as a failure of the address, so a bot cannot spin on it. A token sent when none is
  needed is ignored and not sent to Cloudflare. Both are field errors on `400`, as ADR 0004 says for a rule that depends on
  state.
- **Order.** The lock, then the challenge, then the email. The challenge is judged before the email, so it tells
  nothing about which emails exist.
- **Verified by the server.** The token is posted with the secret key to Cloudflare's `siteverify` endpoint, with the
  client address. Tokens are single use and live five minutes, which Cloudflare enforces. A token that names an action
  other than `sign_in` is refused; the widget is created with that action, and a token without any action is accepted
  because the site key belongs to sign-in alone. With `TURNSTILE_HOSTNAMES` set (the widget's hostnames), a token whose
  `hostname` is not one of them is refused too; empty accepts any, which Cloudflare's dummy test keys need.
- **Fail closed.** If Cloudflare cannot be reached, or refuses our secret key, a client that must be challenged gets
  `503 service_unavailable` with `Retry-After`, like the other safety controls (ADR 0006). Clients that need no challenge
  are not affected, so an outage never blocks the people who type their password right.
- **Off without a key.** With no `TURNSTILE_SECRET_KEY` the challenge is off (development, tests). The system check
  `users.W001` warns when staging or production has none. The key is a secret: Render's environment, never the repository.
- **Not logged.** The secret, the token and the client address are never logged; a failure to reach the service logs a
  fixed line.

## Consequences

- The Flutter developer must show the widget when sign-in answers `captcha_required` (or `captcha_invalid`), and send the
  new token with the same request. The steps, the test keys and the owner's setup are in
  [`../sign-in-challenge.md`](../sign-in-challenge.md). The change is additive (an optional field, two codes), so a client
  built before it works until its address is challenged, and then it sees an unknown `400` code.
- The owner creates the Turnstile widget (a free Cloudflare account) and sets `TURNSTILE_SECRET_KEY` on staging by hand.
- Sign-in depends on a third party for the clients that are challenged. Cloudflare sees the visitor's address and the
  browser's signals for those requests only.
- The challenge applies to sign-in only. Register, resend and verify keep their own limits; add it to them if the logs
  show abuse there.
- A person behind a shared address (an office, a mobile carrier) may be challenged because of what others did there.
