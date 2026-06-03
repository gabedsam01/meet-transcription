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
- Web UI mode uses SQLite for users, settings, jobs, and encrypted Google tokens
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
POLL_INTERVAL_SECONDS=300
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

Web UI Mode runs FastAPI with Uvicorn and stores per-user settings, jobs, and Google OAuth tokens in SQLite at `DATABASE_URL`. Google tokens are encrypted before storage with a key derived from `APP_SECRET_KEY`.

Start the Web UI service:

```bash
docker compose up -d web
```

Open `http://localhost:8000`, sign in with `ADMIN_USERNAME` and `ADMIN_PASSWORD`, connect Google, and configure the Drive folders in the UI.

The `worker` service is still the legacy env-driven worker. It does not read Web UI SQLite settings or Web UI OAuth tokens. Run `worker` alongside `web` only if you intentionally want the separate `.env`-configured worker processing its own configured folders.

### Run Once And Background Processing

The Web UI **Run once** button does not block the HTTP request. When you click it:

1. The request validates your settings and Google connection, creates a `pending` job, and returns to `/jobs` immediately (well under a second).
2. The transcription itself — download, Deepgram, and upload — runs in a local **FastAPI background task** inside the same web container.
3. Refresh `/jobs` to follow the job through `pending` → `processing` → `completed`/`failed`.

Each Run once transcribes the next recording found in the source folder as a single background job. If a job is already `pending` or `processing` for your user, a second Run once is rejected with "There is already a job running." This is what avoids the Cloudflare `524` timeout that happened when the request processed the whole transcription synchronously.

This background task is local to one container and is intentionally simple for the MVP. It does not survive a process restart and does not scale across multiple web replicas. For production with many users, evolve it into a dedicated worker or queue (for example the legacy CLI worker, or a real job queue) instead of in-process background tasks.

### Required Web Env Vars

```env
ADMIN_USERNAME=
ADMIN_PASSWORD=
APP_SECRET_KEY=
SESSION_COOKIE_SECURE=false
GOOGLE_WEB_CLIENT_ID=
GOOGLE_WEB_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=http://localhost:8000/oauth/google/callback
DATABASE_URL=/app/data/app.db
```

`DATABASE_URL` is a SQLite file path for this app, not a SQLAlchemy URL or Postgres connection string.

`DEEPGRAM_API_KEY` remains global and is used by both worker and Web UI-triggered transcription flows.

### Single User

The current Web UI is intended for a single admin user. `ADMIN_USERNAME` and `ADMIN_PASSWORD` gate access, and the connected Google account is stored for that admin user in SQLite.

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
- Selecting `postgres` before the PostgreSQL adapter (feat/postgres-core) is
  integrated fails fast with a clear error.

### Worker configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `WORKER_REPOSITORY_BACKEND` | `postgres` | `postgres` (prod) or `memory` (dev/test only) |
| `WORKER_POLL_INTERVAL_SECONDS` | `10` | Idle poll interval |
| `WORKER_CONCURRENCY` | `1` | Parallel job workers (safe via SKIP LOCKED) |
| `STALE_JOB_TIMEOUT_MINUTES` | `60` | `processing` jobs older than this are failed at startup |
| `DATABASE_URL` | — | PostgreSQL DSN (used by the postgres adapter) |

### Integration status

This branch (`feat/postgres-worker`) delivers the worker, job/download services,
repository ports, and in-memory fakes. The real PostgreSQL repositories, schema,
`db` service, and `SQLAlchemy`/`psycopg` dependencies are delivered by
`feat/postgres-core`; per-user settings (incl. the encrypted Deepgram key and
`save_copy_to_drive`) and OAuth-to-PostgreSQL repointing by
`feat/auth-users-settings`.

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
DATABASE_URL=/app/data/app.db
DEEPGRAM_API_KEY=
```

The Google Cloud authorized redirect URI must exactly match `GOOGLE_REDIRECT_URI`.
`DATABASE_URL` must be a SQLite file path such as `/app/data/app.db`.

## Security

Never commit:

```txt
.env
service-account.json
oauth-client.json
token.json
tmp/
data/processed_files.json
data/app.db
```

The app does not make Drive files public. It downloads files through the Google Drive API and sends the MP4 binary directly to Deepgram.

## Privacy Notice

Make sure all meeting participants know that the meeting is being recorded and transcribed. You are responsible for complying with privacy laws and internal policies.

## Development

```bash
python -m pip install -r requirements.txt
python -m pytest -v
python -m compileall app
docker compose config
```

`docker compose config` requires a local `.env` file. Create it first with `cp .env.example .env`.

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
