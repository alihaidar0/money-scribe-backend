# ADR 0010: The admin panel and the email outbox

- Status: accepted
- Date: 2026-10-15

## Context

Registering and verifying an account sends a 6-digit code by email. Real email delivery is a separate step that needs a
provider and a domain, but the sign-in and registration flows have to be tested first, on a developer machine and on
staging, by copying the code from somewhere. The codes are stored hashed (ADR 0006), so no screen can show a stored one.
Mailpit shows emails in development, but staging has no such service.

## Decision

**An email outbox.** A small app, `outbox` (ADR 0005), provides an email backend that **saves each email to a database
table instead of sending it**. The admin lists the table, so the owner opens an email and copies the code from its body.
The auth flow is the real one (register, code email, verify); only the transport differs.

- `outbox.backends.DatabaseEmailBackend` is chosen by the `EMAIL_BACKEND` setting. The model `OutgoingEmail` stores the
  recipients, the subject, the text body and the creation time.
- Development keeps SMTP to Mailpit by default and can switch to the outbox; staging uses the outbox until real delivery
  exists.
- **Production never uses it.** Staging and production run the same settings module and differ only in their
  environment variables, so the deployment's name is the variable `APP_ENVIRONMENT` (`development`, `staging` or
  `production`; the production settings default to `production`). A system check, run by `manage.py check` and so by
  every start and by the CI job `Django deploy check`, fails with `outbox.E001` when the environment is `production` and
  the outbox is configured. A second check, `outbox.E002`, fails on any other name, so a typo such as `prod` cannot slip
  past the first.
- The admin for the outbox is read-only, visible to superuser accounts only, and can delete messages. A scheduled task
  (Celery Beat, daily) deletes messages older than `OUTBOX_RETENTION_HOURS` (default 24).
- The outbox keeps verification codes in readable form, which is acceptable only on development and staging, where the
  data is invented. The code tables stay hashed, and codes are never written to logs.

**The admin panel.** Django's admin is the control panel.

- The path comes from `ADMIN_URL` (default `admin/`); staging and production use a different path.
- Access is staff only. The first account is made with `createsuperuser`, which asks for the email and the name.
- Each app registers its models when they exist: users with their email-based admin, the outbox, and the sessions (read-only
  with an action that ends one, in step 8.2). Models that hold a user's financial data are registered read-only, or not at
  all, and that choice is made with each feature.
- **Before production** the admin gets login throttling or a network restriction (an allow-list or a private network at the
  proxy) and two-factor sign-in with a one-time code from an authenticator app. Staging, with invented data, does not need
  them. This is a step of the production deployment (`docs/steps.md`).

**Real email delivery** stays a separate, later step: an SMTP provider behind the same `EMAIL_BACKEND` setting, with the
sending domain authenticated (SPF, DKIM and DMARC) so that mail is not marked as spam.

## Alternatives considered

- **A field in the admin that shows the code:** needs the code stored in readable form in the auth tables and weakens an
  agreed rule; removing it later is risky.
- **Mailpit on staging:** another service, and a mail inbox that anyone who finds its address can read.
- **Real SMTP now:** needs the provider and the domain first, which would delay testing.

## Consequences

- The auth steps need no change to be testable before real email exists, and the switch to real delivery is a setting and a
  provider, not new code in the auth flow.
- A table of readable emails exists on staging. It holds invented data only, for at most a day, and only superuser
  accounts can read it.
- Step 8.1 builds the outbox and the admin foundation, with tests (`send_mail` lands in the table, the purge removes old
  rows, the production check fails). Step 9.2 becomes "codes on staging through the outbox", and real delivery moves to the
  production deployment.
