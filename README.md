# Meet Transcription

Meet Transcription watches a Google Drive folder for Google Meet recordings,
sends each MP4 to [Deepgram](https://deepgram.com/), and uploads a readable
plain-text transcript back to Google Drive. It has a server-rendered web UI for
signing in, connecting Google, configuring folders, and triggering/inspecting
jobs, plus a worker that does the transcription work out of band. No FFmpeg
required.

> **Branch status — `feat/ui-devops-polish`.** This is the **UI / DevOps / docs**
> layer of a multi-branch effort. It is **not runnable end-to-end on its own** and
> awaits integration with:
> - `feat/postgres-core` — PostgreSQL + SQLAlchemy repositories (replaces the
>   legacy `app/db.py`);
> - `feat/auth-users-settings` — users/roles, per-user Google OAuth, **per-user
>   encrypted Deepgram key**, and the Drive settings UI;
> - `feat/postgres-worker` — the real `app.worker.main` job processor.

## Architecture

The target deployment is three containers:

```
            ┌──────────┐        ┌──────────┐
  browser → │   web    │        │  worker  │ → Google Drive + Deepgram
            │ (FastAPI)│        │ (jobs)   │
            └────┬─────┘        └────┬─────┘
                 │                   │
                 └─────► postgres ◄──┘
```

- **web** and **worker** run from the **same image** with different commands.
- **postgres** is the production database and stays internal.

> **Current state.** The architecture is **PostgreSQL-only — no SQLite.** The
> Postgres + SQLAlchemy repository layer is owned by a separate branch
> (`feat/postgres-core`) and is not merged here yet, so this branch cannot run
> end-to-end against Postgres. The legacy `app/db.py` (SQLite) remains only as a
> temporary bridge for local UI work — see [PostgreSQL Setup](#postgresql-setup).
> The DB-driven `worker` (`app.worker.main`) also lands with that work; until
> then, run the [legacy worker](#legacy-simple-worker-mode).

## Services

### web

`uvicorn app.web.main:app --host 0.0.0.0 --port 8000`

Serves the UI and OAuth flow on port **8000**. It validates input and **enqueues**
jobs; it never transcribes inside the HTTP request. (For the MVP it can also run
the work in a local background task — see the run-once flow — but the production
path is the worker.)

### worker

`python -m app.worker.main`

Polls the database for pending jobs and runs download → Deepgram → upload,
always leaving each job in a terminal state. Configured by
`WORKER_POLL_INTERVAL_SECONDS` and `STALE_JOB_TIMEOUT_MINUTES`.

### postgres

`postgres:16` with a healthcheck and the `postgres_data` volume. Other services
wait for it to become healthy before starting.

## Environment Variables

| Variable | Service | Notes |
|---|---|---|
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | web | Admin login. |
| `APP_SECRET_KEY` | web, worker | Session signing **and** the encryption key for stored tokens/keys. Use a long random value. |
| `SESSION_COOKIE_SECURE` | web | `true` behind HTTPS. |
| `GOOGLE_WEB_CLIENT_ID` / `GOOGLE_WEB_CLIENT_SECRET` | web | OAuth **Web application** credentials. |
| `GOOGLE_REDIRECT_URI` | web | Must equal `https://YOUR_DOMAIN/oauth/google/callback`. |
| `DEEPGRAM_API_KEY` | web | **Temporary bridge only** — global key the current code reads. The final architecture is per-user encrypted keys with no global fallback (see [Deepgram Key per User](#deepgram-key-per-user)). |
| `DATABASE_URL` | web, worker | PostgreSQL DSN, e.g. `postgresql://meet:...@postgres:5432/meet`. |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | postgres | Database credentials. |
| `WORKER_POLL_INTERVAL_SECONDS` | worker | How often the worker polls for jobs. |
| `STALE_JOB_TIMEOUT_MINUTES` | worker | When to consider a stuck `processing` job stale. |
| `TMP_DIR` | web, worker | Scratch dir for downloads. |

See `.env.example` for the full list, including the legacy worker variables.

## PostgreSQL Setup

PostgreSQL is the single source of truth — **there is no SQLite mode** in the
architecture. The `postgres` service in `docker-compose.yml` provisions
PostgreSQL 16 with a named volume and healthcheck. Set `POSTGRES_DB`,
`POSTGRES_USER`, and `POSTGRES_PASSWORD` in `.env`, and point the app at the
internal service:

```env
DATABASE_URL=postgresql://meet:your-db-password@postgres:5432/meet
```

The hostname is `postgres` (the Compose service name) on the internal network.

> **This branch only.** The Postgres + SQLAlchemy repository layer is owned by
> `feat/postgres-core` and is not merged here yet, so the code still ships the
> legacy SQLite `app/db.py`. To run the UI locally on this branch *before* that
> merge, temporarily override `DATABASE_URL` with a SQLite path
> (`DATABASE_URL=/app/data/app.db`). This is scaffolding, not a supported mode.

## Google OAuth Setup

The web app needs OAuth **Web application** credentials (not Desktop):

1. Create/open a Google Cloud project and **enable the Google Drive API**.
2. `APIs & Services` → `Credentials` → create an **OAuth client ID** of type
   **Web application**.
3. Add an authorized redirect URI that exactly matches `GOOGLE_REDIRECT_URI`,
   e.g. `https://YOUR_DOMAIN/oauth/google/callback` (or
   `http://localhost:8000/oauth/google/callback` for local dev).
4. Put the client id/secret in `GOOGLE_WEB_CLIENT_ID` / `GOOGLE_WEB_CLIENT_SECRET`.

The current scope requests full Drive access (`.../auth/drive`); the narrower
`drive.file` scope is a future consideration.

## Deepgram Key per User

**Final architecture:** each user supplies their **own** Deepgram API key, stored
**encrypted** (like Google tokens). A key is **required** to run a job and there
is **no global fallback**. This is owned by `feat/auth-users-settings`.

**On this branch (temporary bridge only):** the current code still reads a single
global `DEEPGRAM_API_KEY` from the environment so the app can run before the
per-user storage lands. This is scaffolding, **not** the supported end state. The
**Settings → Deepgram** page already reflects the per-user / no-fallback target.

## Drive Folder URL Setup

In **Settings → Drive folders**, paste either a Google Drive **folder link** or a
bare **folder id** for the source and destination folders. A link looks like:

```
https://drive.google.com/drive/folders/1zv32Q...tBD5?usp=sharing
```

The app extracts the id automatically, so you can paste straight from the Drive
address bar.

> This Settings UI is a shell on this branch; the per-user Drive settings are
> owned by `feat/auth-users-settings` and reconciled on integration.

## Running locally

```bash
git clone https://github.com/gabedsam01/meet-transcription.git
cd meet-transcription

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # fill in secrets

# Tests
.venv/bin/python -m pytest -v

# Run the web app
.venv/bin/uvicorn app.web.main:app --reload --port 8000
```

Open `http://localhost:8000` and sign in with `ADMIN_USERNAME` / `ADMIN_PASSWORD`.

> On this branch, set `DATABASE_URL=./data/app.db` in `.env` for local non-Docker
> runs (the temporary SQLite bridge) until `feat/postgres-core` merges.

## Running with Docker Compose

```bash
cp .env.example .env        # fill in secrets
docker compose build
docker compose up -d        # starts postgres, web, worker
docker compose logs -f web
```

- Web UI: `http://localhost:8000`
- Postgres: internal only (not published)

To run the legacy worker instead of the new one, override its command (see
[below](#legacy-simple-worker-mode)).

## Deploying on Dokploy

See **[docs/deploy/dokploy.md](docs/deploy/dokploy.md)**. In short: deploy the
Compose project, attach your domain to the **web** service only on port **8000**,
keep Postgres internal, set the environment variables, and set the Google
redirect URI to `https://YOUR_DOMAIN/oauth/google/callback`.

## GHCR Image

On every push to `main`, GitHub Actions
(`.github/workflows/docker-publish.yml`) runs the tests, builds the image, and
publishes it to the GitHub Container Registry:

```
ghcr.io/gabedsam01/meet-transcription:latest
ghcr.io/gabedsam01/meet-transcription:<short-sha>
```

Use it in production by pulling instead of building — set the image in the
`x-app` anchor of `docker-compose.yml` (already set to the GHCR tag) and:

```bash
docker compose pull
docker compose up -d
```

```bash
# Or run a one-off container directly:
docker run --rm -p 8000:8000 --env-file .env \
  ghcr.io/gabedsam01/meet-transcription:latest
```

The image's default command is the web server; the worker overrides it with
`python -m app.worker.main`.

## Legacy Simple Worker Mode

The original env-driven CLI worker (`python -m app.main`) still works and is
fully supported. It uses a mounted OAuth `token.json` (or a Service Account),
reads settings from `.env`, and stores state in `data/processed_files.json` —
no database or web UI required.

Generate an OAuth token locally:

```bash
python scripts/generate_google_oauth_token.py \
  --client-secrets secrets/oauth-client.json \
  --token-file secrets/token.json
```

Run it:

```bash
docker compose run --rm worker python -m app.main --once     # process once
docker compose run --rm worker python -m app.main --watch    # poll continuously
docker compose run --rm worker python -m app.main --once --reprocess DRIVE_FILE_ID
```

For personal Google accounts, OAuth is recommended over a Service Account
(Service Accounts have no Drive storage quota and cannot own files in a personal
`My Drive`).

## Security Notes

- **Never commit secrets.** `.env`, `secrets/*.json`, `token.json`,
  `data/app.db`, and `data/processed_files.json` are git-ignored.
- Google tokens (and, later, Deepgram keys) are **encrypted at rest** with
  Fernet, using a key derived from `APP_SECRET_KEY`.
- Use `SESSION_COOKIE_SECURE=true` behind HTTPS.
- The app does not make Drive files public; it downloads via the Drive API and
  sends the MP4 directly to Deepgram.
- **Privacy:** make sure all meeting participants know recordings are being
  transcribed; you are responsible for complying with applicable laws.

## Roadmap

- **Local transcription** with faster-whisper as an alternative engine — see
  [docs/architecture/local-transcription.md](docs/architecture/local-transcription.md)
- **Object storage** for recordings/transcripts instead of Drive-only
- **Summaries** / meeting minutes generation
- Per-user Deepgram keys, multi-user dashboard, Google Docs output

## License

MIT
