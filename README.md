# Meet Transcription

Python Docker worker and optional Web UI that watch a Google Drive folder for Google Meet recordings, send MP4 files to Deepgram, and upload plain text transcripts back to Google Drive.

## Features

- Google Drive polling worker
- OAuth authentication for personal Google accounts
- Service Account authentication for compatible Google Workspace setups
- MP4 download from Drive
- Direct MP4 transcription with Deepgram
- TXT transcript generation
- Upload transcript to Google Drive
- Persistent processed-file state
- Docker Compose support
- Worker mode does not require a database or Web UI
- Web UI mode uses PostgreSQL for users, settings, jobs, and encrypted Google tokens
- No FFmpeg required

## How It Works

1. Record a Google Meet meeting.
2. Wait for Google to process the MP4.
3. Move or copy the MP4 to a shared Google Drive input folder.
4. The worker detects the video.
5. The worker downloads the MP4 temporarily.
6. The worker sends it to Deepgram.
7. A readable TXT transcript is generated.
8. The TXT is uploaded to a Google Drive output folder.
9. Temporary local files are removed.
10. The file is marked as processed.

## Requirements

- Docker and Docker Compose
- Deepgram API key
- Google Cloud project
- Google Drive API enabled
- Google OAuth Client ID for personal Gmail/Google One accounts
- Google Service Account JSON key for compatible Workspace setups
- Two Google Drive folders accessible by the chosen Google identity

## Simple Worker Mode

Simple Worker Mode is the existing CLI-compatible deployment. It uses the mounted `token.json` OAuth token or Service Account JSON exactly as before, reads worker settings from `.env`, stores processing state in `/app/data/processed_files.json`, and does not require the Web UI database variables.

Use this mode when one Google identity and one pair of Drive folders is enough.

### Quick Start

```bash
git clone https://github.com/gabedsam01/meet-transcription.git
cd meet-transcription

cp .env.example .env
mkdir -p secrets data tmp
```

For personal Gmail or Google One accounts, OAuth is recommended. Place your OAuth client JSON file at:

```bash
secrets/oauth-client.json
```

Generate `token.json` locally:

```bash
python -m pip install -r requirements.txt
python scripts/generate_google_oauth_token.py \
  --client-secrets secrets/oauth-client.json \
  --token-file secrets/token.json
```

Edit `.env`:

```env
DEEPGRAM_API_KEY=your_deepgram_api_key
GOOGLE_AUTH_MODE=oauth
GOOGLE_OAUTH_CLIENT_SECRETS_FILE=/app/secrets/oauth-client.json
GOOGLE_OAUTH_TOKEN_FILE=/app/secrets/token.json
GOOGLE_SERVICE_ACCOUNT_FILE=/app/secrets/service-account.json
SOURCE_DRIVE_FOLDER_ID=your_source_drive_folder_id
DESTINATION_DRIVE_FOLDER_ID=your_destination_drive_folder_id
WORKER_POLL_INTERVAL_SECONDS=300
TMP_DIR=/app/tmp
STATE_FILE=/app/data/processed_files.json
MAX_PROCESSING_ATTEMPTS=2
FAILED_RETRY_AFTER_SECONDS=86400
DEEPGRAM_MODEL=nova-3
DEEPGRAM_LANGUAGE=pt-BR
DEEPGRAM_SMART_FORMAT=true
DEEPGRAM_PUNCTUATE=true
DEEPGRAM_DIARIZE=true
DEEPGRAM_UTTERANCES=true
```

### Run Once

```bash
docker compose build
docker compose run --rm worker python -m app.main --once
```

### Run Continuously

```bash
docker compose up -d worker
docker compose logs -f worker
```

### Reprocess A File

```bash
docker compose run --rm worker python -m app.main --once --reprocess GOOGLE_DRIVE_FILE_ID
```

The same CLI commands still work outside Docker:

```bash
python -m app.main --once
python -m app.main --watch
python -m app.main --once --reprocess GOOGLE_DRIVE_FILE_ID
```

## Web UI Mode

Web UI Mode runs FastAPI with Uvicorn and stores per-user settings, jobs, and Google OAuth tokens in PostgreSQL via SQLAlchemy, with the schema managed by Alembic. Google tokens are encrypted before storage with a key derived from `APP_SECRET_KEY`.

Start the Web UI service:

```bash
docker compose up -d web
```

Open `http://localhost:8000`, sign in with `ADMIN_USERNAME` and `ADMIN_PASSWORD`, connect Google, and configure the Drive folders in the UI.

### Compose Architecture

The final architecture is three services, with **PostgreSQL as the single source of truth** for users, settings, jobs, transcripts, and encrypted Google tokens:

- **postgres** — PostgreSQL; the only datastore. There is no SQLite path.
- **web** — `uvicorn app.web.main:app --host 0.0.0.0 --port 8000`. Creates `pending` jobs only; it never downloads, transcribes, or uploads.
- **worker** — `python -m app.worker.main`. Owns all processing: claims `pending` jobs, downloads the MP4, transcribes with the user's own (encrypted, per-user) Deepgram key, saves the transcript to PostgreSQL, and optionally uploads a backup copy to Drive.

The legacy CLI worker (`python -m app.main --watch`) still exists for the standalone "Simple Worker Mode" above, but it is **no longer the main Compose worker** — the Compose `worker` service runs `python -m app.worker.main`.

### Run Once And Job Processing

The Web UI **Run once** button does not process anything itself — the web container never downloads, transcribes, or uploads, and there are no in-process background tasks. When you click it:

1. The web request validates your Drive settings, Google connection, and per-user Deepgram key, then creates a single `pending` job in PostgreSQL for the next unprocessed recording in your source folder and returns to `/jobs` immediately.
2. The **worker** (`python -m app.worker.main`) claims the job safely (`FOR UPDATE SKIP LOCKED`), downloads the MP4, transcribes it with your encrypted per-user Deepgram key, and saves the transcript to PostgreSQL.
3. Refresh `/jobs` to follow the job through `pending` → `processing` → `completed`/`failed`. A **Download TXT** link appears in the UI for each completed job.

Google Drive is the **input** (the source folder the worker reads) and an optional **backup** (when `save_copy_to_drive` is enabled and a destination folder is set, the worker uploads a TXT copy and the UI links to it). Each Run once enqueues the next unprocessed recording; videos already `pending`, `processing`, or `completed` for your user are skipped, so a repeated Run once never duplicates a job.

### Required Web Env Vars

```env
ADMIN_USERNAME=
ADMIN_PASSWORD=
APP_SECRET_KEY=
SESSION_COOKIE_SECURE=false
GOOGLE_WEB_CLIENT_ID=
GOOGLE_WEB_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=http://localhost:8000/oauth/google/callback
POSTGRES_DB=meet_transcription
POSTGRES_USER=meet_user
POSTGRES_PASSWORD=change_me
DATABASE_URL=postgresql+psycopg://meet_user:change_me@postgres:5432/meet_transcription
```

`DATABASE_URL` is a required PostgreSQL SQLAlchemy URL using the psycopg 3 driver. Inside Docker Compose use host `postgres`, and make its user/password/database match the `POSTGRES_*` values. Create the schema once before first use with `docker compose run --rm web alembic upgrade head`.

The Web UI does **not** use a global Deepgram key. Each user saves their own Deepgram API key in the UI; it is encrypted at rest (Fernet key derived from `APP_SECRET_KEY`) and used only by the worker when processing that user's jobs. The `DEEPGRAM_API_KEY` env var is consumed solely by the legacy CLI worker (`python -m app.main`), never by the Web UI flow.

### Single User

The current Web UI is intended for a single admin user. `ADMIN_USERNAME` and `ADMIN_PASSWORD` gate access, and the connected Google account is stored for that admin user in PostgreSQL.

### Multi User Roadmap

Multi-user support is planned but not complete. Future work should add user provisioning, per-user authorization boundaries, background job isolation, and operational controls before exposing this to multiple independent users.

## PostgreSQL Multiuser Worker

`python -m app.worker.main` runs a standalone worker that processes transcription
jobs created by the web UI. The UI creates a `pending` job; the worker claims it
safely (`FOR UPDATE SKIP LOCKED`), transcribes with the user's own Deepgram key,
stores the transcript in PostgreSQL, and optionally uploads a copy to Drive. The
UI offers a TXT download for completed jobs.

### Backend selection

- `WORKER_REPOSITORY_BACKEND` defaults to `postgres` (production).
- `WORKER_REPOSITORY_BACKEND=memory` is for local development and tests ONLY and
  is **forbidden in production** — it is in-memory and non-persistent.
- The PostgreSQL adapter is integrated; selecting `postgres` without a valid
  `DATABASE_URL` fails fast with a clear configuration error.

### Worker configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `WORKER_REPOSITORY_BACKEND` | `postgres` | `postgres` (prod) or `memory` (dev/test only) |
| `WORKER_POLL_INTERVAL_SECONDS` | `10` | Idle poll interval |
| `WORKER_CONCURRENCY` | `1` | Parallel job workers (safe via SKIP LOCKED) |
| `STALE_JOB_TIMEOUT_MINUTES` | `60` | `processing` jobs older than this are failed at startup |
| `DATABASE_URL` | — | PostgreSQL DSN (used by the postgres adapter) |

### Integration status

Fully integrated on `integration/postgres-platform`: the worker, job/download
services, and repository ports run against the real PostgreSQL repositories and
schema, alongside the auth layer — per-user settings, the encrypted per-user
Deepgram key, `save_copy_to_drive`, and OAuth tokens, all stored in PostgreSQL as
the single source of truth.

## Google OAuth Setup

Simple Worker Mode can use the existing Desktop OAuth flow with mounted `secrets/oauth-client.json` and `secrets/token.json`, or a Service Account for compatible Workspace setups.

Web UI Mode requires Google OAuth **Web Application** credentials, not Desktop credentials. In Google Cloud:

1. Create or open a Google Cloud project.
2. Enable Google Drive API.
3. Go to `APIs & Services` > `Credentials`.
4. Create an OAuth client ID with application type `Web application`.
5. Add an authorized redirect URI that exactly matches `GOOGLE_REDIRECT_URI`, for example `https://seu-dominio.com/oauth/google/callback`.
6. Put the generated client ID and secret in `GOOGLE_WEB_CLIENT_ID` and `GOOGLE_WEB_CLIENT_SECRET`.

The current scope requests full Drive access with `https://www.googleapis.com/auth/drive`. The narrower `drive.file` scope should be evaluated later; it is outside the current scope.

## Google Drive Authentication For Worker Mode

### Recommended: OAuth For Personal Google Accounts

Use OAuth when the destination folder is in a personal Google Drive account. Service Accounts can read folders shared with them, but uploads to a personal `My Drive` can fail with:

```txt
Service Accounts do not have storage quota. Service accounts can't own files.
```

OAuth uploads the transcript as the human Google user and uses that user's Drive quota.

### Create OAuth Credentials

1. Create or open a Google Cloud project.
2. Enable Google Drive API.
3. Go to `APIs & Services` > `Credentials`.
4. Click `Create Credentials` > `OAuth client ID`.
5. Choose `Desktop app`.
6. Download the JSON file.
7. Save it locally as `secrets/oauth-client.json`.
8. Generate the token:

```bash
python scripts/generate_google_oauth_token.py \
  --client-secrets secrets/oauth-client.json \
  --token-file secrets/token.json
```

The script opens a local browser, asks for Google consent, and writes `secrets/token.json`.

### Optional: Service Account

Service Account mode can still work for Google Workspace setups, especially with Shared Drives or folders where the Service Account is allowed to create files without personal Drive ownership problems.

Use:

```env
GOOGLE_AUTH_MODE=service_account
GOOGLE_SERVICE_ACCOUNT_FILE=/app/secrets/service-account.json
```

Then:

1. Create a Google Cloud project.
2. Enable Google Drive API.
3. Create a Service Account.
4. Create and download a JSON key.
5. Save it as `secrets/service-account.json`.
6. Share your input and output Drive folders with the Service Account email.

## Dokploy Deployment

### Worker Deployment

For OAuth deployments, create two file mounts:

```txt
/app/secrets/oauth-client.json
/app/secrets/token.json
```

Use these environment variables:

```env
GOOGLE_AUTH_MODE=oauth
GOOGLE_OAUTH_CLIENT_SECRETS_FILE=/app/secrets/oauth-client.json
GOOGLE_OAUTH_TOKEN_FILE=/app/secrets/token.json
```

Keep the container volume mount for `/app/data` persistent so `processed_files.json` preserves successful processing and failure-attempt state. The worker OAuth token file should be writable by the container so refreshed Google tokens can be persisted.

### Web UI Deployment

Use the same Docker image and run the Web UI command:

```bash
uvicorn app.web.main:app --host 0.0.0.0 --port 8000
```

Publish port `8000`, keep `/app/data` and `/app/tmp` persistent/shared with the worker, and mount `./secrets:/app/secrets:ro` if the worker also needs local Google credential files.

Dokploy Web UI deployments need these environment variables:

```env
GOOGLE_WEB_CLIENT_ID=
GOOGLE_WEB_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=https://seu-dominio.com/oauth/google/callback
ADMIN_USERNAME=
ADMIN_PASSWORD=
APP_SECRET_KEY=
SESSION_COOKIE_SECURE=true
POSTGRES_DB=meet_transcription
POSTGRES_USER=meet_user
POSTGRES_PASSWORD=change_me
DATABASE_URL=postgresql+psycopg://meet_user:change_me@postgres:5432/meet_transcription
```

The Web UI uses per-user Deepgram keys saved (encrypted) in the app, so it needs no global `DEEPGRAM_API_KEY`. The Google Cloud authorized redirect URI must exactly match `GOOGLE_REDIRECT_URI`.
Run a PostgreSQL service (the bundled `postgres` Compose service or a managed database), point `DATABASE_URL` at it, and apply migrations with `docker compose run --rm web alembic upgrade head`.

## Security

Never commit:

```txt
.env
service-account.json
oauth-client.json
token.json
tmp/
data/processed_files.json
```

The app does not make Drive files public. It downloads files through the Google Drive API and sends the MP4 binary directly to Deepgram.

## Privacy Notice

Make sure all meeting participants know that the meeting is being recorded and transcribed. You are responsible for complying with privacy laws and internal policies.

## Development

```bash
python -m pip install -r requirements.txt
python -m pytest -v
python -m compileall app scripts
docker compose config
docker compose build
```

Database tests run against a real PostgreSQL instance. Point `TEST_DATABASE_URL` at a disposable database (for example a `postgres:16` container); when it is unset or unreachable those tests are skipped rather than run against SQLite.

## Roadmap

- Google Docs output
- AI summary generation
- Meeting minutes
- Email delivery
- Webhook mode
- Queue support
- Multi-user dashboard

## License

MIT
