# Meet Transcription

Meet Transcription watches a Google Drive folder for Google Meet recordings,
sends each MP4 to [Deepgram](https://deepgram.com/), and makes a readable
plain-text transcript available for download. It has a server-rendered web UI for
signing in, connecting Google, configuring folders, and triggering/inspecting
jobs, plus a worker that does the transcription work out of band. No FFmpeg
required.

## Architecture

The deployment is three containers, with **PostgreSQL as the single source of
truth** — there is no SQLite mode:

```
            ┌──────────┐        ┌──────────┐
  browser → │   web    │        │  worker  │ → Google Drive + Deepgram
            │ (FastAPI)│        │ (jobs)   │
            └────┬─────┘        └────┬─────┘
                 │                   │
                 └─────► postgres ◄──┘
```

- **web** and **worker** run from the **same image** with different commands.
- **postgres** holds users, settings, jobs, transcripts, and encrypted Google
  tokens / per-user Deepgram keys. It stays internal.

### The flow

1. In the web UI a user connects Google, sets their **source** Drive folder
   (and optionally a **destination** folder), and saves their **own** Deepgram
   API key.
2. **Run once** validates those preconditions and creates a single `pending`
   job in PostgreSQL for the next unprocessed recording. The web request never
   downloads, transcribes, or uploads — there are no in-process background tasks.
3. The **worker** claims the job (`FOR UPDATE SKIP LOCKED`), downloads the MP4,
   transcribes it with that user's encrypted Deepgram key, and **saves the
   transcript in PostgreSQL**.
4. The UI offers a **Download TXT** link for each completed job (served from
   PostgreSQL). Google Drive is the **input** (the source folder the worker
   reads) and an **optional backup** (when `save_copy_to_drive` is on and a
   destination folder is set, the worker uploads a TXT copy and the UI links to
   it).

## Services

### web

`uvicorn app.web.main:app --host 0.0.0.0 --port 8000`

Serves the UI and OAuth flow on port **8000**. It validates input and
**enqueues** jobs; it never transcribes inside the HTTP request.

### worker

`python -m app.worker.main`

Polls PostgreSQL for pending jobs and runs download → Deepgram → save (→ optional
Drive upload), always leaving each job in a terminal state. Configured by
`WORKER_POLL_INTERVAL_SECONDS`, `WORKER_CONCURRENCY`, and
`STALE_JOB_TIMEOUT_MINUTES`.

### postgres

`postgres:16` with a healthcheck and the `postgres_data` volume. The other
services wait for it to become healthy before starting.

## Environment Variables

| Variable | Service | Notes |
|---|---|---|
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | web | Bootstrap admin login. |
| `APP_SECRET_KEY` | web, worker | Session signing **and** the encryption key for stored Google tokens / Deepgram keys. Use a long random value. |
| `SESSION_COOKIE_SECURE` | web | `true` behind HTTPS. |
| `GOOGLE_WEB_CLIENT_ID` / `GOOGLE_WEB_CLIENT_SECRET` | web | OAuth **Web application** credentials. |
| `GOOGLE_REDIRECT_URI` | web | Must equal `https://YOUR_DOMAIN/oauth/google/callback`. |
| `DATABASE_URL` | web, worker | PostgreSQL DSN, e.g. `postgresql+psycopg://meet_user:...@postgres:5432/meet_transcription`. Never a SQLite path. |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | postgres | Database credentials; must match `DATABASE_URL`. |
| `WORKER_REPOSITORY_BACKEND` | web, worker | `postgres` (production) or `memory` (dev/tests only). |
| `WORKER_POLL_INTERVAL_SECONDS` | worker | How often the worker polls for jobs. |
| `WORKER_CONCURRENCY` | worker | Parallel job workers (safe via `SKIP LOCKED`). |
| `STALE_JOB_TIMEOUT_MINUTES` | worker | When to fail a stuck `processing` job at startup. |
| `TMP_DIR` | web, worker | Scratch dir for downloads. |

The Web UI uses **per-user, encrypted** Deepgram keys — it needs **no global
`DEEPGRAM_API_KEY`**. That env var is consumed only by the legacy CLI worker (see
[Legacy Simple Worker Mode](#legacy-simple-worker-mode)). See `.env.example` for
the full list.

## PostgreSQL Setup

PostgreSQL is the single source of truth — **there is no SQLite mode**. The
`postgres` service in `docker-compose.yml` provisions PostgreSQL 16 with a named
volume and healthcheck. Set `POSTGRES_DB`, `POSTGRES_USER`, and
`POSTGRES_PASSWORD` in `.env`, and point the app at the internal service:

```env
DATABASE_URL=postgresql+psycopg://meet_user:your-db-password@postgres:5432/meet_transcription
```

The hostname is `postgres` (the Compose service name) on the internal network,
and the URL uses the psycopg 3 driver. Apply the schema before first use:

```bash
docker compose run --rm web alembic upgrade head
```

## Deepgram Key per User

Each user supplies their **own** Deepgram API key in **Settings → Deepgram**. It
is stored **encrypted** at rest (Fernet, with a key derived from
`APP_SECRET_KEY`, like Google tokens), is **required** before a job can run, and
there is **no global fallback**. The key is never shown again after saving and is
used only by the worker when processing that user's jobs.

## Drive Folder URL Setup

In **Settings → Drive folders**, paste a Google Drive **folder link** (or a bare
**folder id**) for the source and, optionally, the destination folder. A link
looks like:

```
https://drive.google.com/drive/folders/1zv32Q...tBD5?usp=sharing
```

The app extracts the id automatically. The source folder is required; the
destination is optional and only used when you enable “save a copy to Drive”.

## Running locally

```bash
git clone https://github.com/gabedsam01/meet-transcription.git
cd meet-transcription

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # fill in secrets

# Tests
.venv/bin/python -m pytest -v

# Run the web app against a local PostgreSQL (set DATABASE_URL accordingly)
.venv/bin/uvicorn app.web.main:app --reload --port 8000
```

Open `http://localhost:8000` and sign in with `ADMIN_USERNAME` /
`ADMIN_PASSWORD`.

## Running with Docker Compose

```bash
cp .env.example .env        # fill in secrets
docker compose build
docker compose run --rm web alembic upgrade head   # one-time schema setup
docker compose up -d        # starts postgres, web, worker
docker compose logs -f web
```

- Web UI: `http://localhost:8000`
- Postgres: internal only (not published)

## GHCR Image

On every push to `main`, GitHub Actions
(`.github/workflows/docker-publish.yml`) runs the tests, builds the image, and
publishes it to the GitHub Container Registry:

```
ghcr.io/gabedsam01/meet-transcription:latest
ghcr.io/gabedsam01/meet-transcription:<short-sha>
```

Use it in production by pulling instead of building — the image is already set in
the `x-app` anchor of `docker-compose.yml`:

```bash
docker compose pull
docker compose up -d
```

The image's default command is the web server; the worker overrides it with
`python -m app.worker.main`.

## Deploying on Dokploy

See **[docs/deploy/dokploy.md](docs/deploy/dokploy.md)**. In short: deploy the
Compose project, attach your domain to the **web** service only on port **8000**,
keep Postgres internal, set the environment variables, and set the Google
redirect URI to `https://YOUR_DOMAIN/oauth/google/callback`.

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

## Legacy Simple Worker Mode

The original env-driven CLI worker (`python -m app.main`) still works and is kept
for **compatibility only** — it is **not** the Compose `worker` service. It uses
a mounted OAuth `token.json` (or a Service Account), reads settings from `.env`,
stores state in `data/processed_files.json`, and needs **no database or web UI**.
It reads the global `DEEPGRAM_API_KEY`.

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

## Security

- **Never commit secrets.** `.env`, `secrets/*.json`, `token.json`, and
  `data/processed_files.json` are git-ignored and must stay that way.
- Google tokens and per-user Deepgram keys are **encrypted at rest** with Fernet,
  using a key derived from `APP_SECRET_KEY`.
- Use `SESSION_COOKIE_SECURE=true` behind HTTPS.
- The app does not make Drive files public; it downloads via the Drive API and
  sends the MP4 directly to Deepgram.
- **Privacy:** make sure all meeting participants know recordings are being
  transcribed; you are responsible for complying with applicable laws.

## Development

```bash
python -m pytest -v
python -m compileall app scripts
docker compose config        # needs a local .env (cp .env.example .env)
docker compose build
```

Database tests run against a real PostgreSQL instance. Point `TEST_DATABASE_URL`
at a disposable database (for example a `postgres:16` container); when it is
unset or unreachable those tests are skipped rather than run against SQLite.

## Roadmap

- **Local transcription** with faster-whisper as an alternative engine — see
  [docs/architecture/local-transcription.md](docs/architecture/local-transcription.md)
- **Object storage** for recordings/transcripts instead of Drive-only
- **Summaries** / meeting minutes generation
- Google Docs output, multi-user dashboard

## License

MIT
