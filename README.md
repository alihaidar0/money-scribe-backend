# Money Scribe Backend

Django REST backend for Money Scribe, a personal finance tracker.

[![CI](https://github.com/alihaidar0/money-scribe-backend/actions/workflows/ci.yml/badge.svg?branch=develop)](https://github.com/alihaidar0/money-scribe-backend/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> **Status:** in development.

## Stack

| Component | Version |
| --- | --- |
| Python | 3.14.8 |
| Django | 6.0.9 |
| uv | 0.13.0 |
| PostgreSQL | 18 |
| Redis | 8 |
| Dev container image | `alihaidar199527/django-devcontainer:python-3.14.8.1` |

Application libraries and their exact versions are pinned in `pyproject.toml` and
`uv.lock`.

## Getting started

Install Docker Desktop and VS Code with the Dev Containers extension, then:

```bash
git clone git@github.com:alihaidar0/money-scribe-backend.git
code money-scribe-backend
```

Click **Reopen in Container**. The first start installs the dependencies and the git
hooks (`uv sync --locked`, `pre-commit install`); the Celery worker and Beat start by
themselves. Then:

```bash
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

| Service | URL |
| --- | --- |
| Django | <http://localhost:8000> |
| Mailpit | <http://localhost:8025> |
| Flower | <http://localhost:5555> (`celery -A config flower --port=5555`) |

## Development

`develop` and `main` are protected: work happens on topic branches and pull requests
into `develop`; only `develop` opens a pull request into `main` (the release). The git hooks
(Conventional Commits, no commits or pushes on `develop`/`main`) are installed with the container;
the single required check is **CI passed**.

- Branches, hooks, SSH keys, CI and releases: [`.github/CONTRIBUTING.md`](.github/CONTRIBUTING.md)
- GitHub settings, Actions permissions, environments, security and rulesets: [`docs/github-setup.md`](docs/github-setup.md)
- The dev image is pinned in `docker-compose.yml`; Dependabot proposes updates as pull requests.
- CI builds and smoke-tests the production image on every pull request and pushes nothing. To publish it, set the repository variable `PUBLISH_IMAGE` (`ghcr` or `dockerhub`), see [`docs/github-setup.md`](docs/github-setup.md) section 3.

## Template

Created from [`alihaidar0/django-template`](https://github.com/alihaidar0/django-template)
release [`v2026.10.15.1`](https://github.com/alihaidar0/django-template/releases/tag/v2026.10.15.1).

## License

[MIT](LICENSE) © 2026 Ali Haidar
