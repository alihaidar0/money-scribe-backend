<!-- markdownlint-disable-file MD041 — the PR title is the page heading -->
<!-- Branch flow: topic branch → develop → main. Topic branches target develop; only the develop → main release PR targets main. The PR title follows Conventional Commits (feat:, fix:, build:, ci:, docs:, chore:, …): labels and release notes are generated from it. -->

## What & why

<!-- What does this change do, and what problem does it solve? Link issues with "Closes #123". -->

## How to verify

<!-- Commands run, tests added, screenshots, or sample output. -->

## API changes

<!-- Only when this pull request changes openapi/schema.yaml. Otherwise write "None". -->

- Schema regenerated (`bash scripts/schema.sh generate`): yes / no
- Breaking for clients: yes / no (a breaking change needs the `breaking change` label and the owner's approval; see `docs/api-workflow.md`)
- Request issue: Closes #

oasdiff changelog (paste it from the "API breaking changes" job summary):

```text
```

## Breaking changes

- [ ] This change forces projects generated from the template to adapt (use a `feat!:` or `fix!:` title and a `BREAKING CHANGE:` footer; the `breaking change` label is added automatically)

## Checklist

- [ ] This PR targets `develop` (only the `develop` → `main` release PR targets `main`)
- [ ] **CI passed** is green
- [ ] Tests added or updated for behaviour changes
- [ ] Migrations included and reviewed (if models changed)
- [ ] `README.md` / docs updated if setup, configuration, or behaviour changed
- [ ] New environment variables added to `.env.example` and `docker-compose.yml`
- [ ] The quickstart in `README.md`, `scripts/welcome.sh` and the CI scaffold still match (template changes)
- [ ] GitHub Actions are pinned to a full commit SHA with a `# vX.Y.Z` comment
- [ ] No secrets, keys, or `.env` values committed
- [ ] The title is a Conventional Commit, or the PR carries `skip-changelog` to stay out of the release notes
