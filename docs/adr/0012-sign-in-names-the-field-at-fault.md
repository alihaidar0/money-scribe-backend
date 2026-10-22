# ADR 0012: Sign-in names the field at fault

- Status: accepted
- Date: 2026-10-21
- Supersedes: the sign-in sentences of [ADR 0004](0004-error-status-codes.md) ("Sign-in never says which part was
  wrong") and [ADR 0006](0006-authentication-tokens.md) ("Signing in ... reveal nothing")

## Context

OWASP and CWE-204 advise one generic answer for a failed sign-in, so the API cannot be used to learn which addresses have
an account. The Flutter app found the price of that: a person who mistyped the email, or never registered, cannot tell it
from a wrong password, and tries more emails and more passwords. The app owner has been told that an explicit answer
reveals which emails have an account and still wants it (issue #35). The large identity providers make the same choice:
the email is judged first, with limits, bot checks and monitoring around it.

## Decision

`POST /api/v1/auth/sign-in/` looks the email up first.

- No account has the email: `401 email_not_registered` with `attr` `email`, whatever the password is, even blank.
- The email has an account and the password is wrong: `401 wrong_password` with `attr` `password`.
- Unchanged: `403 email_not_verified` only after the right password; `429 sign_in_locked` and `throttled` with
  `Retry-After`; no password policy on sign-in; the `503` when the limit store is down.
- `invalid_credentials` stays for any other failed sign-in, which today is a deactivated account (right or wrong
  password, so it is not named). An unknown email and a wrong password no longer answer it.
- Registration is **unchanged**. It answers the same for a new, a pending and a registered address, and a pending
  address is registered again. Password reset stays uniform too. Sign-in is the one place that reveals an account, and
  only to a client that has not been locked.

**What stands in for the lost secrecy.**

- **A limit per client address over every account.** Fifteen failed sign-ins from one address in 15 minutes, an unknown
  email or a wrong password, lock that address with `429 sign_in_locked` for 15 minutes (`SIGN_IN_ADDRESS_MAX_FAILURES`).
  Wrong passwords count too: a list of registered emails, each tried once, would never reach the five failures that lock
  one account for one address. A success does not clear the count, or a list-tester could reset it with an account of
  their own. The per-account limits stay (five from one address lock the pair, 50 from all addresses lock the account).
- **Nothing is stored per unknown email**, so an address cannot fill the cache with keys of its choosing.
- **The same cost for every answer.** An unknown email is checked against a dummy hash, so the answers take the same
  time and timing is no second channel.
- **The lock is checked first**, so a locked client learns nothing from a wrong email or password.
- **A log line** when an address is locked, with no address or email in it, so probing can be seen and alerted on.
- The existing per-client throttle (20 a minute) and the fail-closed `503` stay.

**The challenge.** A bot challenge after repeated failures is decided in [ADR 0013](0013-sign-in-bot-challenge.md). A
client must not retry automatically.

## Consequences

- Anyone who can make 15 attempts per 15 minutes per address can learn whether an email has an account, about 1,400
  probes a day per address. The accepted risks are cleaning a list of emails for credential stuffing, and targeted
  phishing of known users. The address limit slows one address; it is **not** a defence against a client that rotates
  addresses (IPv4 addresses, IPv6 /64 networks, a botnet). The real mitigation for that is the challenge of ADR 0013,
which asks for it after 5 failures from one address, but a rotating client stays under that too, so the log line below
is what shows it.
- Revisit when the log line `A client address was locked after repeated failed sign-ins.` appears in staging or
  production more than occasionally, and in any case before production launch (`docs/steps.md`, section 12): alert on that
  line, and add the challenge if it shows list-testing.
- An unknown email is never counted against an account, so the 50-failure account lock cannot be aimed at an address
  that does not exist. It can be aimed at a known one, now more cheaply because an email can be confirmed first; that
  was already true, and it needs 50 failures spread over several addresses.
- Many users behind one shared address (an office, a mobile carrier) share the address limit: an attacker there can lock
  sign-in for that address, never for users on other networks.
- Two codes join the registry: `email_not_registered` (401, `attr` `email`) and `wrong_password` (401, `attr` `password`).
  The error item's `attr` is now set on these two `401` answers; it is `null` on every other `401`. Adding a code and a
  value of an existing field is not a breaking change.
- A client built before this change gets a `401` code it does not know for an unknown email or a wrong password, and
  must treat it as a generic failure of the status (the `code` field of the schema already says so). No contract release
  has been published yet, so no pinned consumer is broken; after the first release the same change would need the owner's
  approval and the `breaking change` label.
- The app maps both codes, checking the email first, and shows each message under its own field.
