# Google OAuth Setup

Meet Transcription reads your Google Meet recordings from a Google Drive folder
and (optionally) uploads a TXT backup to a destination folder. To do that, each
user connects their own Google account through OAuth. This document walks you
through the **Google Cloud** side (project, Drive API, OAuth client) and the
**application** side (`GOOGLE_WEB_CLIENT_ID`, `GOOGLE_WEB_CLIENT_SECRET`,
`GOOGLE_REDIRECT_URI`), then explains the most common failure,
`redirect_uri_mismatch`.

> This is for the **web + worker** deployment. The legacy CLI worker
> (`python -m app.main`) uses a different, file-based flow — see
> [Environment Variables](03-environment-variables.md) and the README
> "Legacy Simple Worker Mode" section. Per-user Deepgram setup is covered in
> [Deepgram](05-deepgram.md); the overall flow is in
> [Architecture](01-architecture.md).

## What the app requests

When a signed-in user clicks **Connect Google**, the web service
(`app/web/main.py`) builds an authorization request to
`https://accounts.google.com/o/oauth2/v2/auth` with these parameters:

| Parameter | Value |
|---|---|
| `client_id` | `GOOGLE_WEB_CLIENT_ID` |
| `redirect_uri` | `GOOGLE_REDIRECT_URI` |
| `response_type` | `code` |
| `scope` | `https://www.googleapis.com/auth/drive` |
| `access_type` | `offline` (so Google returns a refresh token) |
| `prompt` | `consent` |
| `state` | a random, per-session anti-CSRF token |

After the user approves, Google redirects back to `GOOGLE_REDIRECT_URI`, the app
validates the `state`, and exchanges the `code` at
`https://oauth2.googleapis.com/token` for an access token and refresh token.
Those tokens are **encrypted at rest** (Fernet, key derived from
`APP_SECRET_KEY`) and stored per user in PostgreSQL — see [Security](16-security.md).

### Scope note

The app requests exactly one scope:

```
https://www.googleapis.com/auth/drive
```

This is the broad Drive scope. The worker needs it to **download** the source
MP4 and to **upload** the optional TXT backup copy to a destination folder. The
app does not request any other Google scopes (a best-effort
`https://www.googleapis.com/oauth2/v3/userinfo` call is made with the access
token only to display the connected account's email/name; it is not a separate
OAuth scope request).

## Step 1 — Create or open a Google Cloud project

1. Open the [Google Cloud Console](https://console.cloud.google.com/).
2. In the project picker (top bar), click **New Project** (or select an existing
   one you want to use for this app).
3. Give it a recognizable name, e.g. `meet-transcription`, and create it.
4. Make sure that project is selected before continuing.

## Step 2 — Enable the Google Drive API

The OAuth client alone is not enough; the **Google Drive API** must be enabled in
the same project, or token exchange will succeed but Drive calls will fail.

1. Go to **APIs & Services → Library**.
2. Search for **Google Drive API**.
3. Open it and click **Enable**.

Direct link:
`https://console.cloud.google.com/apis/library/drive.googleapis.com`

## Step 3 — Configure the OAuth consent screen

Before you can create an OAuth client, Google requires a consent screen.

1. Go to **APIs & Services → OAuth consent screen**.
2. Choose a **User type**:
   - **External** — anyone with a Google account can connect (you will likely
     start in *Testing* mode, where you must add each user as a *Test user*).
   - **Internal** — only available with Google Workspace; limits the app to your
     organization's accounts.
3. Fill in the app name, support email, and developer contact email.
4. On the **Scopes** step you may add `.../auth/drive`, but it is not strictly
   required here — the app requests the scope at sign-in time. (Adding it makes
   the consent screen clearer to your users.)
5. If you selected **External / Testing**, add the Google accounts that will use
   the app under **Test users**. Without this, those users get a
   "this app is blocked / not verified" error at sign-in.

> Requesting the broad `.../auth/drive` scope is a **sensitive/restricted**
> scope. While your app is in **Testing**, only listed test users can connect.
> Publishing/verification is only needed if you intend to serve users outside
> that test list.

## Step 4 — Create the OAuth client (type: Web application)

This app is a server-rendered web app, so it needs a **Web application** OAuth
client — **not** a Desktop client.

1. Go to **APIs & Services → Credentials**.
2. Click **Create Credentials → OAuth client ID**.
3. For **Application type**, choose **Web application**.
4. Give it a name, e.g. `meet-transcription-web`.
5. Under **Authorized redirect URIs**, add the exact callback URL(s) (Step 5).
6. Click **Create**. Google shows you the **Client ID** and **Client secret** —
   copy both (Step 6).

## Step 5 — Add the exact redirect URI

The redirect URI is the single most error-prone part of OAuth. It must match,
**character for character**, the value the app sends as `redirect_uri`, which is
your `GOOGLE_REDIRECT_URI`. The app's callback route is always
`/oauth/google/callback` (defined in `app/web/main.py`).

Add the URI(s) that apply to your deployment under **Authorized redirect URIs**:

| Environment | Redirect URI to add |
|---|---|
| **Local development** | `http://localhost:8000/oauth/google/callback` |
| **Production** | `https://YOUR_DOMAIN/oauth/google/callback` |

Replace `YOUR_DOMAIN` with your real domain, e.g.
`https://meet.example.com/oauth/google/callback`.

You can add **both** to the same OAuth client (one for local, one for prod). You
must not paraphrase them — Google compares the full string including scheme
(`http`/`https`), host, **port**, and path.

Rules that trip people up:

- **Scheme matters.** Local is `http`, production must be `https`.
- **Port matters.** `http://localhost:8000/...` (the default web port) is not the
  same as `http://localhost/...`.
- **Path must be exact.** It is `/oauth/google/callback` — no trailing slash, no
  uppercase.
- **No localhost in production.** Production must use your public domain over
  HTTPS.

## Step 6 — Set the application environment variables

Put the OAuth client values and the matching redirect URI into your environment
(`.env` for local, or the platform's env settings for production — see the
[Dokploy deploy guide](13-dokploy-deploy.md)).

| Variable | What to set |
|---|---|
| `GOOGLE_WEB_CLIENT_ID` | The **Client ID** from Step 4 |
| `GOOGLE_WEB_CLIENT_SECRET` | The **Client secret** from Step 4 |
| `GOOGLE_REDIRECT_URI` | The **exact** URI you added in Step 5 |

`GOOGLE_REDIRECT_URI` must be **identical** to one of the Authorized redirect
URIs in the Google Console.

### Local example

```env
GOOGLE_WEB_CLIENT_ID=1234567890-abcdefg.apps.googleusercontent.com
GOOGLE_WEB_CLIENT_SECRET=GOCSPX-xxxxxxxxxxxxxxxxxxxx
GOOGLE_REDIRECT_URI=http://localhost:8000/oauth/google/callback
```

### Production example

```env
GOOGLE_WEB_CLIENT_ID=1234567890-abcdefg.apps.googleusercontent.com
GOOGLE_WEB_CLIENT_SECRET=GOCSPX-xxxxxxxxxxxxxxxxxxxx
GOOGLE_REDIRECT_URI=https://meet.example.com/oauth/google/callback
SESSION_COOKIE_SECURE=true
```

> Set `SESSION_COOKIE_SECURE=true` whenever the app is served over HTTPS;
> otherwise the OAuth `state` cookie that protects the callback will not behave
> correctly behind TLS.

Restart the `web` service after changing these (e.g. `docker compose up -d web`),
since they are read at startup.

## Step 7 — Connect a Google account in the app

1. Open the app (`http://localhost:8000` locally, or `https://YOUR_DOMAIN`).
2. Sign in with `ADMIN_USERNAME` / `ADMIN_PASSWORD` (or a user account).
3. Click **Connect Google** (this hits `/connect-google`).
4. Approve the Drive permission on Google's consent screen.
5. You are redirected back to `/oauth/google/callback`, the tokens are stored
   encrypted, and the dashboard's **Google** status card shows connected.

After this, set your Drive folders in **Settings → Drive folders** and your
Deepgram key (or local engine) before running a job. See
[Deepgram](05-deepgram.md) and [Local Transcription](06-local-transcription.md).

## Troubleshooting: `redirect_uri_mismatch`

This is the most common OAuth error. Google shows a page titled
**"Error 400: redirect_uri_mismatch"** and refuses to redirect back to the app.

**Cause.** The `redirect_uri` the app sent (your `GOOGLE_REDIRECT_URI`) is not in
the OAuth client's **Authorized redirect URIs** list — or it differs by even one
character.

**Fix checklist:**

1. **Compare the two strings exactly.** Open
   **APIs & Services → Credentials → your Web application client** and look at
   **Authorized redirect URIs**. It must contain a value identical to your
   `GOOGLE_REDIRECT_URI`.
2. **Check the scheme.** Local must be `http://`, production must be `https://`.
   Visiting your prod site over `http` and getting redirected to `https` can make
   the effective URI differ from what you registered.
3. **Check the host and port.** `localhost:8000` (the default port the web
   service listens on) is required locally; `localhost` without `:8000` will not
   match. In production the host must be your exact domain.
4. **Check the path.** It must be `/oauth/google/callback` — lowercase, no
   trailing slash.
5. **Check you edited the right OAuth client.** If you have several clients (or a
   Desktop client by mistake), make sure `GOOGLE_WEB_CLIENT_ID` corresponds to the
   **Web application** client whose redirect URIs you just edited.
6. **Wait and retry.** Edits to the OAuth client in Google Cloud can take a short
   time to propagate. After saving, wait a minute and try **Connect Google**
   again.
7. **Restart the web service after changing `GOOGLE_REDIRECT_URI`.** The value is
   read at startup, so a stale env var means the app still sends the old URI.

A quick way to see exactly what the app is sending: start the connect flow and
read the `redirect_uri=` query parameter in the browser's address bar on the
Google consent page — URL-decode it and confirm it byte-for-byte matches a
registered Authorized redirect URI.

### Other OAuth issues

| Symptom | Likely cause | Fix |
|---|---|---|
| `Invalid OAuth state` (HTTP 400 from the callback) | The session cookie was lost between `/connect-google` and the callback | Don't open the consent link in a different browser/incognito window; behind HTTPS set `SESSION_COOKIE_SECURE=true`. Retry **Connect Google**. |
| "App is blocked / hasn't been verified" | App is in **Testing** and the Google account isn't a listed test user, or the broad Drive scope needs verification | Add the account under **OAuth consent screen → Test users**, or publish/verify the app. |
| Drive calls fail after a successful login | **Google Drive API** not enabled in the project | Enable it (Step 2). |
| Connected, but the worker can't refresh access later | No refresh token was issued | The app already sends `access_type=offline` and `prompt=consent`; reconnect via **Connect Google** to force a fresh consent that returns a refresh token. |
| Wrong client type chosen | A **Desktop** client was created instead of **Web application** | Create a **Web application** client (Step 4) and update `GOOGLE_WEB_CLIENT_ID` / `GOOGLE_WEB_CLIENT_SECRET`. |

## Related documentation

- [Architecture](01-architecture.md) — how web, worker, Postgres, and Redis fit together.
- [Environment Variables](03-environment-variables.md) — full reference, including the legacy CLI's separate `GOOGLE_*` vars.
- [Deepgram](05-deepgram.md) — per-user, encrypted Deepgram API keys.
- [Dokploy Deploy](13-dokploy-deploy.md) — setting `GOOGLE_REDIRECT_URI` for a production domain.
- [Security](16-security.md) — how Google tokens are encrypted at rest.
