# Extension-first architecture

The Meet Transcription backend is **extension-first**: recordings are captured
by the **Meet Audio Recorder** Chrome extension and POSTed directly to the
backend. **Google Drive is no longer required.**

This document covers:

- The new per-user extension token model
- How the upload endpoint authenticates a chrome-extension origin
- How the UI exposes token management (`/extensao`)
- How to run the backend without any Google envs

See also: [27-chrome-extension.md](27-chrome-extension.md) for the end-to-end
extension protocol and [00-overview.md](00-overview.md) for the bigger picture.

## Why extension-first

Google Meet only writes an MP4 to Drive when the org has cloud recording
enabled and someone presses *Record*. Many users (and especially small teams)
do not have that, and even when they do they have to wait for the recording to
finish processing before the file appears. The Chrome extension captures the
**tab audio** directly in the browser and ships it to the backend as soon as
the user clicks *Stop* — no Google account, no Drive folder, no MP4 to wait
for. The same worker pipeline that handles Drive recordings transcribes
extension uploads; the rest of the system does not change.

Concretely, with this release:

- **Google envs are optional.** The app boots cleanly with empty
  `GOOGLE_WEB_CLIENT_ID` / `GOOGLE_WEB_CLIENT_SECRET` / `GOOGLE_REDIRECT_URI`.
  The UI shows a *"Google Drive desativado"* banner; `/connect-google`
  becomes a friendly redirect.
- **Tokens are per-user.** Each user mints their own token from `/extensao`.
  Tokens are stored hashed (SHA-256, peppered with `APP_SECRET_KEY`) — only the
  masked prefix is persisted, and the raw token is shown exactly once at
  creation.
- **CORS is locked to chrome-extension origins.** Only requests with an
  `Origin: chrome-extension://<32-char-id>` header get `Access-Control-Allow-Origin`
  on `/api/recordings/*`. Drive, downloads, and the rest of the app stay
  locked to the app origin.
- **The legacy `EXTENSION_UPLOAD_TOKEN` env is still honored** as a fallback
  (so a deployment can migrate from the old single-token model without
  breaking existing extensions). Per-user tokens always win.

## Boot without Google

```bash
ADMIN_USERNAME=admin \
ADMIN_PASSWORD=secret \
APP_SECRET_KEY=$(python -c 'import secrets; print(secrets.token_urlsafe(48))') \
DATABASE_URL=postgresql+psycopg://meet_user:change_me@postgres:5432/meet_transcription \
uvicorn app.web.main:app --host 0.0.0.0 --port 8000
```

That's it. The app starts, the dashboard renders, and the only transcription
path available is "extension upload" — there is no Drive to point at. To enable
Drive later, set the three `GOOGLE_*` envs and restart; the same data and
same users keep working.

The `WebSettings.google_enabled` boolean (read by the UI) is `True` only when
**all three** of those envs are non-empty. Partial envs disable Drive (we
never build a half-broken OAuth URL).

## Per-user tokens

A token is a 32-character URL-safe random string, prefixed `mtrec_` so the
intent is unmistakable in logs (e.g. `mtrec_aBcDeF12…`).

### Minting a token

1. Log in to the web UI.
2. Visit `/extensao`.
3. Type a device name (e.g. *"Macbook do João"*) and click **Gerar token**.
4. The raw token is displayed **once** with a copy button. **Copy it now** —
   the server only stores the SHA-256 hash and the first 8 characters of the
   mask (`mtrec_aBcD…`). The raw value cannot be recovered.

The same page lists every active token for the user with:

- the device name;
- a masked prefix;
- the creation timestamp;
- the last-used timestamp (updated on every successful ping/upload);
- a **Revogar** button.

A revoked token:

- is no longer accepted by `POST /api/recordings/upload` (401);
- is no longer accepted by `POST /api/recordings/ping` (401);
- remains visible in the user's list with a *"revoked"* badge so they know
  which devices they have turned off.

### How the token is stored

```python
# app/web/extension_tokens.py
TOKEN_PREFIX = "mtrec_"
RAW_TOKEN_BYTES = 24  # 192 bits of entropy → 32 base64-url chars

def new_raw_token(secret_key: str) -> tuple[str, str, str]:
    raw = secrets.token_urlsafe(RAW_TOKEN_BYTES)
    return raw, hash_token(secret_key, raw), raw[: len(TOKEN_PREFIX) + 8]
```

The hash is `sha256(pepper + ":" + token)` with `pepper = hmac.new(APP_SECRET_KEY, b"extension-token-pepper", sha256).hexdigest()[:32]`. The pepper is computed at
request time from `APP_SECRET_KEY` and **never persisted** — rotate
`APP_SECRET_KEY` and all existing tokens are invalidated.

### CORS for the extension origin

```python
# app/web/cors.py
_CHROME_EXTENSION_ORIGIN = re.compile(r"^chrome-extension://[a-z]{32}$")
```

Only requests whose `Origin` matches that regex get the
`Access-Control-Allow-Origin` header. The middleware also short-circuits the
preflight (`OPTIONS`) for `/api/recordings/*` and responds with 204. All
non-matching origins (including `https://evil.example.com` and
`chrome-extension://<not-32-letters>`) get no CORS headers — the browser
blocks the response.

The middleware is **scoped** to `/api/recordings/*`. Drive, downloads, and
the rest of the app stay locked to the app origin even from a
chrome-extension origin.

## How the upload endpoint authenticates

```python
# app/web/main.py — _resolve_extension_token()
1. If store is present:
     digest = hash_token(settings.app_secret_key, raw_token)
     row   = store.find_by_hash(digest)
     if row is not None and row.revoked_at is None and owner.is_active:
         return owner, row                  # per-user token wins
2. Else, if settings.extension_upload_token:
     if secrets.compare_digest(settings.extension_upload_token, raw_token):
         return owner_from_email, _LegacySentinel()   # legacy env fallback
3. Else:
     return None → 401
```

The raw token is **never** logged. The masked prefix is logged on creation
(revocation event logs the same prefix). A successful upload logs
`Recording upload accepted: job_id=… user_id=… bytes=…` — no token, ever.

## The `/extensao` page

`/extensao` is the only UI surface for the token model. It is in Portuguese
(BR) and shows:

- A "como usar" card with the three steps the user follows in the extension
  popup (open the extension → settings → paste the token).
- The **Gerar token** form (device name → new token shown once).
- The list of active tokens (masked prefix, last used, revoke button).
- The list of revoked tokens (so the user can see which devices they
  turned off).

The page is hidden from the nav when the user has the admin role and
`AUTO_POLL_ENABLED=true` and **no** token — it shows a *"Recomendamos criar
um token para a extensão"* hint. The nav link is *"Extensão"*.

## `/api/recordings/ping`

`POST /api/recordings/ping` lets the extension verify its token without
uploading a recording. It accepts the same `Authorization: Bearer` /
`X-Upload-Token` headers as `/api/recordings/upload` and returns:

```json
{ "ok": true, "user_id": 1, "user_email": "admin", "client_name": "Macbook do João", "extension_version": "1.2.3" }
```

It is CORS-friendly: the chrome-extension origin gets the right
`Access-Control-Allow-Origin` header so the extension can read the response
from a content script. The endpoint is **CSRF-exempt** because the only
authentication is the token itself.

## Migration from the legacy single-token model

1. Deploy the new release with `EXTENSION_UPLOAD_TOKEN` still set — the old
   extensions keep working with no change.
2. Each user visits `/extensao` and mints a per-user token.
3. They paste it into the extension's settings page.
4. When every user has a per-user token, remove `EXTENSION_UPLOAD_TOKEN` from
   the env. The legacy path stops being reachable; per-user tokens are the
   only path.

There is no automatic migration: tokens are *new* secrets and the user has
to explicitly mint them. The old token does not have a per-user mapping and
so cannot be silently re-keyed.

## Operational notes

- **Backup/restore**: tokens are in the `user_extension_tokens` table. If
  the table is restored from a backup, the same tokens still work (the hash
  is the only thing stored).
- **`APP_SECRET_KEY` rotation**: invalidates **all** per-user tokens. Users
  must re-mint. There is no recovery path — the design is "the user owns
  the secret, not us."
- **Rate limiting**: the upload endpoint enforces `EXTENSION_UPLOAD_MAX_MB`
  on body size; the per-user key is looked up on every request but is
  indexed, so a few thousand users are no problem.
- **Observability**: each request logs `user_id`, never the token or its
  hash. The token prefix (e.g. `mtrec_aBcD…`) is logged on creation and
  revocation, never on use.
