# The sign-in bot challenge (Cloudflare Turnstile)

Decision and reasons: [ADR 0013](adr/0013-sign-in-bot-challenge.md). Sign-in asks for a challenge only from a client
address that has already failed 5 sign-ins in 15 minutes. Everyone else never sees it.

## The owner: create the widget and set the key

Free, no card needed.

1. Sign in to the [Cloudflare dashboard](https://dash.cloudflare.com/) (a free account) and open **Turnstile**.
2. **Add widget**. Name it `money-scribe-staging`. Under **Hostnames** add only the staging API host
   (`money-scribe-staging.onrender.com`). Do not add `localhost`: anyone can run a page on their own machine, so it would
   let them get valid tokens with the public site key. Develop locally with the test keys below. Choose the **Managed**
   widget mode. Production gets its own widget later, with its own keys and its own hostname.
3. Copy the **Site key** (public; it goes into the app) and the **Secret key** (private).
4. Put the secret key in the password manager, and into the Render service as `TURNSTILE_SECRET_KEY`
   ([`staging-render.md`](staging-render.md), step 5). Never commit it or paste it in an issue. Set
   `TURNSTILE_HOSTNAMES` to the widget's hostnames from step 2 (public, comma separated): the server then refuses a
   token whose `hostname` is another one, as Cloudflare's server-side validation guide advises.
5. After the deploy, `python manage.py check --deploy` no longer shows `users.W001`.

Rotate the secret key from the same dashboard page if it ever leaks; the site key is public and stays.

## The client developer (Flutter and web)

Sign-in works as before. Only when it answers `400` with the error code `captcha_required` (or `captcha_invalid`, for a
token that did not verify), the app must:

1. Show a Turnstile widget created with the **site key** and the **action `sign_in`**.
2. When the widget gives a token, send the same sign-in request again with `"captcha_token": "<token>"`.
3. Treat each token as single use, valid for five minutes: for the next attempt, reset the widget and get a new one.
4. Show the message for the error code (English and Arabic); its `attr` is `captcha_token`, not a form field.
5. On `503 service_unavailable` show the usual "try again shortly" and honour `Retry-After`. Never retry automatically.

Do not ask for the challenge up front: the server decides, and the app only reacts to the codes.

**Packages.** Cloudflare publishes no Flutter package. Community packages exist on pub.dev (for example
`cloudflare_turnstile`, which supports Android and iOS; read its README for the web build and for the `baseUrl`
setting, which must be one of the widget's hostnames). They are free. If a package does not fit, a web view that loads the
Turnstile widget from `https://challenges.cloudflare.com/turnstile/v0/api.js` and hands the token back to Flutter works on
every platform. The web build uses the JavaScript widget directly.

**Test keys** (Cloudflare's own, for development and tests; they never reach a real user):

| Site key | Behaviour |
| --- | --- |
| `1x00000000000000000000AA` | visible, always passes |
| `2x00000000000000000000AB` | visible, always fails |
| `1x00000000000000000000BB` | invisible, always passes |
| `3x00000000000000000000FF` | forces an interactive challenge |

With the backend's `TURNSTILE_SECRET_KEY` set to the dummy secret `1x0000000000000000000000000000000AA` every token
verifies; with `2x0000000000000000000000000000000AA` none does. On staging use the real site key from the owner.

To see it locally: set the dummy secret in your local environment file (copied from the example file), fail five
sign-ins with any emails, and the sixth answers `captcha_required`.
