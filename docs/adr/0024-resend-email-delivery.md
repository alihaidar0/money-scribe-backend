# ADR 0024: Real email delivery with Resend

- Status: accepted
- Date: 2026-10-28

## Context

Registration, password reset, email change and the new-device notice send an email, and the code in it is the only way
to finish the flow. Until now staging has used the database outbox ([ADR 0010](0010-admin-panel-and-email-outbox.md)),
where the owner copies each code from the admin panel. That cannot serve a tester and cannot serve production. The
backend hosts the API on a free Render web service, which blocks outbound traffic on the ports 25, 465 and 587
([ADR 0020](0020-scheduled-calls-instead-of-a-free-worker.md)), so SMTP is not an option and the provider must be
reached over HTTPS. The email is sent inside the request that asks for it (`users/emails.py`), and the service is awake
at that moment because the request woke it; no worker is involved.

Facts read at their source on 2026-10-28 (a provider's plans and rules change; check the page before relying on one):

- Resend ([pricing](https://resend.com/pricing)): the Free plan is 3,000 emails a month, limited to 100 a day, with 3
  domains.
- Resend ([domains](https://resend.com/docs/dashboard/domains/introduction), [errors](https://resend.com/docs/api-reference/errors)):
  it sends from "a domain you own (i.e., not a shared or public domain)" that is verified with DNS records (`TXT`, and
  `MX` or `CNAME`). Without a verified domain the `resend.dev` sandbox sends only to the address of the Resend account.
  A subdomain is recommended.
- Resend ([Django guide](https://resend.com/docs/send-with-django)): the integration is the package
  [`django-anymail`](https://anymail.dev/en/stable/esps/resend/), with the backend
  `anymail.backends.resend.EmailBackend` and the key in `ANYMAIL["RESEND_API_KEY"]`.

## Decision

- **Resend over its HTTPS API, through `django-anymail`.** The package is added without its `resend` extra, which only
  pulls in the webhook signature library; this backend never receives webhooks. The Resend backend is selected with
  `EMAIL_BACKEND=anymail.backends.resend.EmailBackend`, like the outbox, so the auth code does not change.
- **Settings.** `DEFAULT_FROM_EMAIL` (the sender; it must be an address on a domain verified in Resend) and
  `RESEND_API_KEY`, both read from the environment. The key is a secret held only in the host's environment; the
  repository holds an empty value. The API call has a 10 second timeout, so a slow provider cannot hold a request for the
  package's default of 30 seconds.
- **A failed send is still only logged** (`users.emails`), with no address, code or key, as today; the user asks for a
  new code. A system check, `outbox.E003`, fails when the Resend backend is selected and the key is empty, because that
  mistake would otherwise show as codes that never arrive.
- **Where.** Staging and production use Resend once a sending domain is verified. The outbox stays for development
  and as a fallback on staging (for example when the daily limit is reached); `outbox.E001` still refuses it in
  production. Development keeps Mailpit.
- **The sending domain** belongs to the owner. Staging may use a free subdomain whose DNS records the owner can edit
  (the owner applies for one on is-a.dev; `docs/staging.md`). Production gets its own decision with the shared domain of
  the web app and the API, which is still open.

## Alternatives considered

- **SMTP to Resend:** blocked on the free Render instance (ports 25, 465, 587).
- **Our own backend on `requests` for the one endpoint:** less to install, but it would duplicate error handling,
  address formatting and attachments that a maintained package already covers.
- **The Resend SDK:** not the integration Resend documents for Django, and it needs an adapter to be a Django backend.
- **Keeping the outbox on staging:** a tester cannot see the admin panel, so the owner would copy every code by hand.

## Consequences

- One new dependency, `django-anymail`, and one new secret per environment.
- The free plan allows 100 emails a day. Every sign-up, reset and new-device notice counts, which is enough for the owner
  and a few testers, not for launch. Production needs a plan sized to its users.
- Email can reach only addresses on the Resend account until the sending domain is verified.
- The delivery path is untested against the real service in CI: tests stand in for the HTTP call. The owner checks it
  once per environment by registering with a readable address (`docs/staging.md`).
