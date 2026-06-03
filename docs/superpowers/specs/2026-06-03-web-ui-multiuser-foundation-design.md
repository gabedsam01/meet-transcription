# Design: Web UI And Multiuser Foundation

## Goal

Add a simple FastAPI web UI and SQLite-backed foundation for future multiuser operation without breaking the existing validated worker modes:

```bash
python -m app.main --once
python -m app.main --watch
python -m app.main --once --reprocess <file_id>
```

The web UI is an additive runtime. It wraps and extends the existing Drive -> Deepgram -> TXT -> Drive flow, but it does not replace the current CLI worker, Service Account mode, OAuth token-file mode, or JSON state behavior.

## Runtime Modes

The project will support three clearly separated modes.

### Simple Worker Mode

Current behavior remains intact. It uses environment configuration, existing `DriveClient`, `DeepgramClient`, `FileProcessor`, and `ProcessedState`. It can use Service Account or mounted OAuth `token.json` exactly as it does today.

### Web UI Single-Admin Mode

New FastAPI app served with:

```bash
uvicorn app.web.main:app --host 0.0.0.0 --port 8000
```

This mode has a single admin login controlled by:

```env
ADMIN_USERNAME=
ADMIN_PASSWORD=
APP_SECRET_KEY=
SESSION_COOKIE_SECURE=false
```

The MVP has no public signup and no user self-registration.

### Multiuser Foundation

The SQLite schema and token storage are modeled per user. The MVP creates and uses the authenticated admin user, but tables support future users and recurring multiuser workers.

## Web Routes

Public routes:

- `GET /login` renders the login form.
- `POST /login` validates `ADMIN_USERNAME` and `ADMIN_PASSWORD`, then creates a signed session cookie.
- `POST /logout` clears the session cookie.
- `GET /health` returns JSON status, for example `{ "status": "ok" }`.

Protected routes redirect unauthenticated users to `/login`:

- `GET /` dashboard with current Google connection status, settings summary, recent jobs, and basic runtime status.
- `GET /connect-google` starts Google OAuth Web Application flow.
- `GET /oauth/google/callback` validates OAuth `state`, exchanges `code` for tokens, stores encrypted tokens in SQLite, and returns the user to the dashboard.
- `GET /settings` renders editable user settings.
- `POST /settings` saves `source_drive_folder_id`, `destination_drive_folder_id`, and `poll_interval_seconds` for the logged-in user.
- `GET /jobs` lists transcription jobs with status, attempts, errors, and timestamps.
- `POST /jobs/run-once` processes pending Drive videos once for the authenticated user only.

## UI

The UI will use Jinja2 templates and local CSS only:

```txt
app/web/templates/
app/web/static/styles.css
```

No React and no Bootstrap CDN for the MVP. Pages should be simple, readable, and usable on desktop and mobile. Browser validation will happen after implementation only to confirm that FastAPI starts, pages load, forms are usable, and the basic flow makes sense.

## Authentication And Session Security

The single-admin MVP uses form login with a signed cookie.

- `ADMIN_USERNAME` and `ADMIN_PASSWORD` are required in web mode.
- `APP_SECRET_KEY` is required in web mode.
- Session cookies are signed with `APP_SECRET_KEY`.
- Cookies are `HttpOnly`.
- Cookies use `Secure` when `SESSION_COOKIE_SECURE=true`. Dokploy/HTTPS deployments should set it to `true`.
- Protected routes redirect to `/login` if no valid session exists.

The implementation may use Starlette `SessionMiddleware` or an equivalent signed-cookie mechanism. Passwords are compared against the environment value; no password database is added in the MVP.

## Google OAuth Web Flow

The web UI uses OAuth Web Application credentials, separate from the existing mounted token-file worker mode:

```env
GOOGLE_WEB_CLIENT_ID=
GOOGLE_WEB_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=https://seu-dominio.com/oauth/google/callback
```

OAuth scope:

```txt
https://www.googleapis.com/auth/drive
```

The README will mention that `https://www.googleapis.com/auth/drive.file` should be evaluated later.

The OAuth flow must use `state` for CSRF protection. The state value is stored in the signed session cookie before redirecting to Google and validated in `/oauth/google/callback` before token exchange.

## Database

SQLite database path defaults to:

```txt
/app/data/app.db
```

Access is centralized in `app/db.py`. Web code and future multiuser worker code must not open SQLite connections outside that layer.

SQLite configuration:

- `PRAGMA journal_mode=WAL`.
- `PRAGMA busy_timeout` configured on connections.
- Writes are explicit transactions.
- Connection helpers keep writes short to avoid unnecessary contention between `web` and `worker` containers.

Tables:

```txt
users:
- id
- email
- name
- created_at

google_tokens:
- id
- user_id
- access_token
- refresh_token
- token_uri
- client_id
- client_secret
- scopes
- expiry
- created_at
- updated_at

settings:
- id
- user_id
- source_drive_folder_id
- destination_drive_folder_id
- poll_interval_seconds
- created_at
- updated_at

transcription_jobs:
- id
- user_id
- source_file_id
- source_file_name
- transcript_drive_file_id
- status
- error_message
- attempts
- created_at
- updated_at
- processed_at
```

Job statuses:

```txt
pending
processing
completed
failed
skipped
```

## Token Storage

OAuth tokens are stored in SQLite, not in `token.json`, for web mode.

`access_token`, `refresh_token`, and `client_secret` are encrypted with Fernet using a key derived from `APP_SECRET_KEY`. The implementation derives the Fernet key with SHA-256 over the UTF-8 `APP_SECRET_KEY` value and URL-safe base64 encoding, so deployers can provide a strong secret string instead of a pre-generated Fernet key. This behavior must be documented in README.

If `APP_SECRET_KEY` is absent in web mode, startup fails. Tokens must not be persisted in plaintext.

Token storage is isolated behind a `TokenStore` abstraction so future storage changes do not affect route handlers or processing code.

## Settings

The MVP uses a global `DEEPGRAM_API_KEY` from environment. Per-user Deepgram keys are out of scope unless added later with encryption.

Per-user settings stored in SQLite:

- `source_drive_folder_id`
- `destination_drive_folder_id`
- `poll_interval_seconds`
- `created_at`
- `updated_at`

## Manual Processing From UI

`POST /jobs/run-once` processes only the authenticated user.

Flow:

1. Validate the user is logged in.
2. Load user Google tokens from `TokenStore` and decrypt them.
3. Load user settings from SQLite.
4. Build Google Drive credentials from stored OAuth token data.
5. Build a Drive client scoped to the user's folders.
6. Use global `DEEPGRAM_API_KEY` and existing Deepgram client.
7. Reuse the existing transcript formatting and processing logic where possible.
8. Record jobs in `transcription_jobs` as pending, processing, completed, failed, or skipped.

This route runs synchronously in the MVP. Long-running processing may block the request; background jobs and a recurring multiuser worker are roadmap items.

## Compatibility With Existing Worker

The current worker remains independent:

- It keeps using `STATE_FILE=/app/data/processed_files.json`.
- It keeps supporting Service Account and mounted OAuth `token.json`.
- It does not require SQLite.
- It does not require admin login variables.
- Its CLI flags and behavior remain unchanged.

The web UI uses `/app/data/app.db` for web state and `/app/tmp` for temporary files. Sharing `/app/data` between `web` and `worker` is supported, but the worker does not depend on the DB in this MVP.

## Docker Compose

The Dockerfile remains a single image capable of running either process.

Compose will expose two services:

```yaml
services:
  worker:
    build: .
    command: python -m app.main --watch
    volumes:
      - ./data:/app/data
      - ./tmp:/app/tmp
      - ./secrets:/app/secrets:ro

  web:
    build: .
    command: uvicorn app.web.main:app --host 0.0.0.0 --port 8000
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data
      - ./tmp:/app/tmp
      - ./secrets:/app/secrets:ro
```

The exact service names can be `worker` and `web`. README will explain Simple Worker Mode and Web UI Mode separately.

## README Updates

README must add sections:

- Simple Worker Mode
- Web UI Mode
- Single User
- Multi User Roadmap
- Google OAuth Setup
- Dokploy Deployment

It must explain:

- Simple worker mode uses mounted `token.json` or Service Account as today.
- Web UI mode uses OAuth Web Application credentials.
- Google Cloud redirect URI must match `GOOGLE_REDIRECT_URI`.
- Example redirect URI: `https://seu-dominio.com/oauth/google/callback`.
- Dokploy web deployments need `GOOGLE_WEB_CLIENT_ID`, `GOOGLE_WEB_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`, and `APP_SECRET_KEY`.

## Out Of Scope

- React.
- External queue.
- PostgreSQL.
- Payments.
- Public signup.
- Advanced dashboard.
- Google Picker.
- AI summaries.
- Email delivery.
- Google Docs output.
- Recurring multiuser worker.

## Testing And Validation

Automated tests should cover:

- Web config parsing and required variables.
- SQLite schema creation, `PRAGMA journal_mode=WAL`, and a non-zero `PRAGMA busy_timeout`.
- Token encryption/decryption through `TokenStore`.
- User/settings/job persistence.
- Job creation/status transitions.
- Existing transcript formatting behavior.
- Existing CLI worker tests must continue passing.

Required validation commands:

```bash
python -m pytest -v
python -m compileall app scripts
docker compose config
docker compose build
```

Browser validation happens after the web app is implemented and running locally at `http://localhost:8000`. It is limited to checking page loading, basic navigation, forms, health, and simple usability.

## Success Criteria

- Existing CLI worker modes still work.
- FastAPI UI starts and serves `/`.
- `/health` returns JSON status ok.
- Admin login works with signed HttpOnly cookie.
- Protected routes redirect unauthenticated users to `/login`.
- Google OAuth web flow starts and validates `state` on callback.
- Callback stores encrypted OAuth tokens in SQLite.
- Settings page saves per-user Drive folder IDs and polling interval.
- Jobs page lists user jobs.
- `POST /jobs/run-once` processes only the authenticated user.
- TXT upload to Google Drive still works through the existing Deepgram/Drive flow.
- SQLite persists users, tokens, settings, and jobs under `/app/data/app.db`.
