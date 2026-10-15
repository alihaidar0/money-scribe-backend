# Consuming the API contract

For the **client developer** (Flutter, web or any other client). The backend publishes its API
as one OpenAPI file, `openapi/schema.yaml`; this page explains how a client repository gets a
**pinned** version of it, generates a typed client from it, and keeps the two in step. The working
agreement between the two developers is in [`api-collaboration.md`](api-collaboration.md).

In the commands, `OWNER/BACKEND_REPO` is the backend repository.

## What you consume

Every **published** release of the backend (`vX.Y.Z`) carries the contract as a release asset named
**`openapi.yaml`**, identical to `openapi/schema.yaml` at that tag. The backend keeps the next release as
a draft until its owner publishes it, and drafts, like the rolling "Staging" draft kept for `develop`, are
visible to the backend's maintainers only. A client therefore pins a published tag, or a commit (below). The backend repository must have
**release immutability** turned on (Settings → General → Releases): the release is then immutable, so the
file for a tag never changes, and a client can check it with `gh release verify-asset`. Without that
setting the contract can still be downloaded, but not verified. Use the release notes to see what changed: breaking changes carry the
`breaking change` label.

## Get and pin a version

Keep the file in the client repository (for example `api/openapi.yaml`) and note which release it
came from, so every build uses a known contract:

```bash
gh release download v1.4.0 --repo OWNER/BACKEND_REPO --pattern openapi.yaml --dir api --clobber
echo "v1.4.0" > api/BACKEND_VERSION
```

Commit both files. A pull request that changes `api/openapi.yaml` is the only way the client moves to a new contract.

- **A private backend repository:** run `gh auth login` with an account that can read it. In CI use a
  fine-grained token with read-only access to that repository's contents, stored as a secret.
- **Before the first release exists:** pin a commit instead. Download the file as it is at that
  commit (`gh api repos/OWNER/BACKEND_REPO/contents/openapi/schema.yaml?ref=<commit> -H "Accept: application/vnd.github.raw+json" > api/openapi.yaml`)
  and note the commit in `api/BACKEND_VERSION`. Move to a tag at the first release.
- **An endpoint still in a draft pull request:** the schema diff in that pull request is the agreed
  shape; build against it only after both developers approved it.

## Generate the client

Generate code from the pinned file; do not write request and response types by hand.

### Flutter

Add [`swagger_parser`](https://pub.dev/packages/swagger_parser) as a dev dependency (it generates
Retrofit-style clients on top of Dio; add the companion packages its README lists), then configure it in
`swagger_parser.yaml`:

```yaml
swagger_parser:
  schema_path: api/openapi.yaml
  output_directory: lib/api
  language: dart
  json_serializer: json_serializable   # or freezed, dart_mappable
  root_client: true
```

```bash
dart run swagger_parser
```

### Web (TypeScript)

[`openapi-typescript`](https://openapi-ts.dev/) generates the types (it needs a current Node.js,
20 or newer), and [`openapi-fetch`](https://openapi-ts.dev/openapi-fetch/) is a small fetch client typed from them:

```bash
npm i -D openapi-typescript typescript
npm i openapi-fetch
npx openapi-typescript api/openapi.yaml -o src/api/schema.d.ts
```

```ts
import createClient from "openapi-fetch";
import type { paths } from "./api/schema";

const client = createClient<paths>({ baseUrl: import.meta.env.VITE_API_BASE_URL });
const { data, error } = await client.GET("/api/v1/items/");
```

`data` is typed from the contract and is only present on a `2xx` response; `error` is only present on
`4xx` or `5xx`. Another generator (Orval, or `openapi-generator`) works the same way: the pinned file is the input.

### What the generated client looks like

Checked with a sample API built on the template's defaults (a model viewset with pagination, an enum field, a
nullable field and a decimal field): `swagger_parser` generated one REST client with its requests and the data
classes, including a `Paginated…List` class (`count`, `next`, `previous`, `results`) for every list endpoint and a
separate class for each enum; `build_runner` built it and `dart analyze` reported no issues. Two shapes to know:

- A DRF decimal field is described as `type: string` with `format: decimal`, so it arrives as a string:
  parse it where you need a number.
- A nullable field is marked `nullable: true` in the contract and is nullable in the generated class.

Errors come out as a few shared classes, not one per endpoint: `Error400Response` (`type`, `errors`) and its
`Error400` items (`code`, `detail`, `attr`), plus `ErrorResponse401`, `ErrorResponse404` and so on. With
these defaults the whole sample API generated 13 data classes. `type` is an enum, `attr` is nullable.
Decode an error response body into these classes when the status is `4xx` and the body is JSON.

## Check that the client matches the contract

Commit the generated code, so a contract change shows up as a reviewable diff, and add a CI step that
regenerates it and fails if it differs from what is committed:

```bash
dart run swagger_parser && git diff --exit-code lib/api          # Flutter
npx openapi-typescript api/openapi.yaml -o src/api/schema.d.ts && git diff --exit-code src/api   # web
```

## Move to a new backend release

1. Read the release notes and look for `breaking change` entries.
2. Download the new `openapi.yaml`, update `api/BACKEND_VERSION`, regenerate the client.
3. Let the compiler tell you what changed, fix it, and open a pull request in the client repository.
4. Merge when the app builds and its tests pass against the new contract.

Check for a new release regularly (the weekly sync is a good moment); the backend developer announces
removals in the issue and the release notes before they happen.

## Running the client against a local backend

The dev stack publishes the Django dev server on the host at port 8000 (start it with `pmr`, which runs
`runserver 0.0.0.0:8000`). How each client reaches it:

| Client | Base URL | What the backend already allows |
| --- | --- | --- |
| Android emulator | `http://10.0.2.2:8000` | `ALLOWED_HOSTS` includes `10.0.2.2` (the emulator's alias for the host) |
| Flutter web (`flutter run -d web-server`, port 8080) or any browser app | `http://localhost:8000` | `CORS_ALLOWED_ORIGINS` includes `http://localhost:8080` and `http://127.0.0.1:8080` |
| iOS simulator, desktop | `http://localhost:8000` | nothing extra |
| A physical phone | not reachable directly: the port is published on the host's loopback only | with a USB cable, `adb reverse tcp:8000 tcp:8000` makes `localhost:8000` on the phone reach the host (check the current `adb` documentation) |

Both settings come from environment variables with these development defaults in `docker-compose.yml`;
**production allows no cross-origin browser calls** until the project lists its own origins in
`CORS_ALLOWED_ORIGINS`. Native mobile apps do not use CORS.

## Configuration to keep out of the generated code

- **Base URL per environment** (local, staging, production): a build-time setting, for example
  `--dart-define=API_BASE_URL=...` for Flutter or an environment variable for the web build. Never hard-code it.
- **Authentication:** when the backend declares a security scheme the contract says so, but the token
  handling (a Dio interceptor, a fetch middleware) is the client's own code and lives outside the generated folder.
- **Errors:** every error follows the same body, see [`api-conventions.md`](api-conventions.md).

## Checklist for a client change

- [ ] the contract file comes from a release (or an approved commit) and `api/BACKEND_VERSION` says which
- [ ] the client was regenerated, not edited by hand
- [ ] the CI check for generated code passes
- [ ] the base URL comes from configuration
- [ ] no real data in tests or fixtures: invented values only
