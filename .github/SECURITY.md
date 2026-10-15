# Security Policy

## Reporting a vulnerability

Report security issues **privately** through GitHub's
**Security → Advisories → Report a vulnerability** form on this repository.

Please do **not** open a public issue or pull request for anything
security-sensitive. We aim to acknowledge a report within 3 business days and
to agree a disclosure timeline with you.

The form is available when **Settings → Advanced Security → Private
vulnerability reporting** is enabled.

## Supported versions

Only the latest release on `main` is supported.

| Version | Supported |
| --- | --- |
| `main` (latest release) | ✅ |
| older releases / tags | ❌ — upgrade to the latest release |

## Scope notes

- The development stack (`docker-compose.yml`, the dev container) is for local
  use only: its default credentials and `DEBUG=True` are intentional there and
  are not vulnerabilities.
- Dependency and container-image CVEs are tracked automatically by Dependabot,
  `pip-audit` in CI, and the Trivy scan of each published image (Security →
  Code scanning). File those only with a working exploit path.
- Never attach `.env` contents, tokens, or other credentials to a report.
