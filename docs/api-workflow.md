# API contract workflow

A project that exposes an HTTP API usually has a separate client (a web or mobile frontend,
an integration) built by someone else. The two sides agree on the API through **one written
contract**, not through chat: `openapi/schema.yaml`, an OpenAPI file that is **generated from
the code and committed**. Nobody edits it by hand.

The workflow is contract-first and deliberately light: one issue form, one generated file,
three automatic checks. How two developers (backend and client) talk to each other, with
diagrams, is in [`api-collaboration.md`](api-collaboration.md). It switches on by itself the first time a project commits
`openapi/schema.yaml`; until then nothing in CI changes.

## The loop

```text
request issue ─▶ agree the shape ─▶ implement + regenerate ─▶ pull request ─▶ CI gates ─▶ merge ─▶ client adopts
```

1. **Request.** Whoever needs an endpoint opens an issue with the **API request** form
   (label `api-request`): the need, the screen or feature name, a request and a response
   example, the error and empty cases and the priority. Examples use **invented values**,
   never real data.
2. **Agree the shape.** Discuss the examples in the issue until both sides accept them.
   Change the examples, not the code, while you still disagree.
3. **Implement and regenerate.** The backend developer implements it on a topic branch,
   then runs `bash scripts/schema.sh generate` and commits the regenerated
   `openapi/schema.yaml` **in the same pull request**. Add the `api-change` label, fill in the
   "API changes" section of the pull request template and link the issue with `Closes #N`.
4. **CI gates** (below) must pass before the merge.
5. **Close the loop.** Comment on the issue with a short summary of what was built (path,
   fields, error cases, any difference from the request) and close it.
6. **The client adopts.** The consumer pins the contract of a release (the `openapi.yaml` asset
   of the release tag), generates or updates its client from it and builds against it; see
   [`consuming-the-api.md`](consuming-the-api.md).

## Who does what

| Role | Does |
| --- | --- |
| Client developer | opens the request issue, reviews the agreed examples, adopts the merged schema |
| Backend developer | implements, regenerates the schema, writes the pull request's API section |
| Owner | approves breaking changes (below), merges the release pull request |

In a project, put both the backend and the client owners on `/openapi/` and `/.spectral.yaml`
in `.github/CODEOWNERS`, so every change to the contract is seen by both sides.

## What CI checks

Three jobs, part of the single required **CI passed** check. Each is skipped while
`openapi/schema.yaml` does not exist, so a fresh template and a project without an API stay green.

| Job | Fails when |
| --- | --- |
| **API schema is current** | the committed file differs from what the code generates, or the schema is invalid, or generation emits a warning (run `bash scripts/schema.sh generate`) |
| **API style (Spectral)** | an operation lacks an `operationId` or a description, or a `200`/`201` response declares no content (rules in `.spectral.yaml`) |
| **API breaking changes (oasdiff)** | the schema on the pull request's base commit and the pull request's schema differ in a breaking way, unless the pull request carries the `breaking change` label |

The oasdiff changelog is always written to the job's summary; paste it into the pull request.

## Breaking changes

A change is breaking when an existing client can stop working: removing or renaming a path,
field or enum value, making an optional field required, narrowing a type, or changing a status code.

- **Before the first release** a breaking change needs the owner's approval. When approved,
  the pull request carries the `breaking change` label (a `feat!:` title adds it) and the gate passes.
- **After the first release** do not break a published path. Add a **new versioned path**
  (`/api/v2/...`) next to the old one, or keep the old behaviour for a **deprecation period**:
  mark the operation `deprecated=True` with `@extend_schema`, announce the removal date in the
  issue and in the release notes, and remove it only after that date.

## Running it locally

```bash
bash scripts/schema.sh generate      # write openapi/schema.yaml from the code
bash scripts/schema.sh check         # fail if the committed file is out of date

# the two CI tools run as pinned containers, nothing to install
docker compose -f .github/api-tools/docker-compose.yml run --rm spectral \
  lint openapi/schema.yaml --ruleset .spectral.yaml
git show origin/develop:openapi/schema.yaml > /tmp/base.yaml
docker compose -f .github/api-tools/docker-compose.yml run --rm -v /tmp:/base:ro oasdiff \
  changelog /base/base.yaml openapi/schema.yaml --format markdown
```

`scripts/schema.sh` needs no database. The running application serves the schema and Swagger UI **only outside
production** and only to staff accounts (`/api/schema/` and `/api/docs/`: sign in through the admin panel first, anyone
else gets a `404` so that the secret admin path is never revealed; use
**Authorize** in Swagger UI with an access token to try the authenticated operations). Production serves neither: its
contract is the `openapi.yaml` attached to each release. The routes are in `config/api_docs.py`.

## Not part of this workflow

Consumer-driven contract tests, a contract broker, mock servers and a service catalog are
deliberately left out; add them when the team and the number of clients outgrow one file.
