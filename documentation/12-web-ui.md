# Web UI

The web application is the operator-facing surface of meet-transcription. It is a
**FastAPI** app (`app/web/main.py`) that signs users in, connects their Google
account, configures Drive folders and a Deepgram key, and lists/inspects
transcription jobs. It serves the **Download TXT** of every completed transcript.

This page documents each screen and route. For the moving parts behind them see
[Architecture](01-architecture.md), the [Worker](11-worker-flow.md), the
[Queue](09-redis-queue.md), and [Local transcription](06-local-transcription.md).

## Design constraints

These are hard rules, not preferences:

- **Server-rendered Jinja2 — no SPA.** Templates live in
  `app/web/templates/` and are rendered via `Jinja2Templates`. Styling is a
  single local stylesheet, `app/web/static/styles.css`, mounted at `/static`.
  There is **no React, no client framework, no CDN asset, and no build step**.
- **Session-cookie auth.** Authentication uses Starlette's
  `SessionMiddleware`, signed with `APP_SECRET_KEY`. The cookie is `lax`
  same-site and `https_only` when `SESSION_COOKIE_SECURE` is set. Every
  protected route depends on `require_user`; admin routes depend on
  `require_admin`.
- **The UI never transcribes in-request.** `Run once` only *validates and
  creates a pending job* (and best-effort enqueues its id). Download/Deepgram or
  local-engine/Drive-upload all happen in the worker. No HTTP handler ever
  downloads an MP4 or calls a transcription provider.

The Jinja environment registers three layout helpers (`app/web/helpers.py`):

| Filter      | Purpose                                                        |
| ----------- | ------------------------------------------------------------- |
| `mid`       | Middle-truncates long Drive ids so they do not break layout.  |
| `dt`        | Renders ISO timestamps compactly (`short_datetime`).          |
| `drive_dl`  | Builds a Drive download URL (`drive_download_url`).            |

## Route map

All routes are defined in `create_app()` inside `app/web/main.py`.

| Method | Path                                  | Auth          | Purpose                                              |
| ------ | ------------------------------------- | ------------- | ---------------------------------------------------- |
| GET    | `/health`                             | none          | Liveness probe, returns `{"status": "ok"}`.          |
| GET    | `/login`                              | none          | Login form.                                          |
| POST   | `/login`                              | none          | Verify credentials, start session.                   |
| POST   | `/logout`                             | session       | Clear session, redirect to `/login`.                 |
| GET    | `/`                                   | `require_user`| Dashboard with status cards.                         |
| GET    | `/settings`                           | `require_user`| Settings landing page.                               |
| GET    | `/settings/drive`                     | `require_user`| Drive folder settings form.                          |
| POST   | `/settings/drive`                     | `require_user`| Save source/destination folders (by URL).            |
| GET    | `/settings/deepgram`                  | `require_user`| Deepgram key management.                             |
| POST   | `/settings/deepgram`                  | `require_user`| Save (encrypted) Deepgram key.                       |
| POST   | `/settings/deepgram/test`             | `require_user`| Validate the saved key against Deepgram.             |
| GET    | `/jobs`                               | `require_user`| Jobs list + `Run once` button.                       |
| POST   | `/jobs/run-once`                      | `require_user`| Create a pending job and enqueue its id.             |
| GET    | `/jobs/{job_id}`                      | `require_user`| Job detail (owner-scoped).                           |
| GET    | `/jobs/{job_id}/download`             | `require_user`| Download the transcript as `.txt`.                   |
| GET    | `/admin/users`                        | `require_admin`| User administration list.                           |
| POST   | `/admin/users`                        | `require_admin`| Create a user.                                       |
| POST   | `/admin/users/{user_id}/disable`      | `require_admin`| Deactivate a user.                                   |
| POST   | `/admin/users/{user_id}/enable`       | `require_admin`| Reactivate a user.                                   |
| POST   | `/admin/users/{user_id}/reset-password` | `require_admin`| Set a new password for a user.                    |
| GET    | `/connect-google`                     | `require_user`| Start the Google OAuth flow.                         |
| GET    | `/oauth/google/callback`              | `require_user`| OAuth callback: exchange code, store tokens.         |

### Authentication dependencies

`require_user` reads `user_id` from the session, loads the user from the auth
repository, and rejects anyone missing or inactive by raising a `303` redirect to
`/login` (and clearing the session). `require_admin` builds on `require_user` and
returns `403 Admin access required` unless `user.role == "admin"`.

Flash messages are passed across the redirect-after-POST pattern with
`_set_flash` / `_pop_flash`, which store a single `flash` string in the session.

## Screens

### Login (`GET/POST /login`, `POST /logout`)

The login page (`login.html`) is the only unauthenticated screen besides
`/health`. `POST /login` looks up the user by email, verifies the password with
`verify_password`, and checks `is_active`. On success it stores `user_id` and
`user_email` in the session and redirects to `/` (303). On failure it re-renders
the form with `Invalid email or password` and `401`.

The first administrator is seeded at startup: the app's `lifespan` calls
`repos.users.ensure_admin(...)` using `ADMIN_USERNAME` / `ADMIN_PASSWORD` so you
can always sign in after a fresh deploy.

```bash
# Open the app and sign in with the seeded admin
open http://localhost:8000/login   # ADMIN_USERNAME / ADMIN_PASSWORD
```

`POST /logout` clears the session and redirects to `/login`.

### Dashboard (`GET /`)

`dashboard.html` is the control panel. It loads the user's jobs via
`worker_repos.jobs.list_jobs_for_user(user.id)` (an empty list if the worker
repository backend is unavailable) and renders **status cards**:

| Card             | Source                                              | States shown                                                                 |
| ---------------- | --------------------------------------------------- | ---------------------------------------------------------------------------- |
| **Google**       | `repos.google_tokens.get_for_user(...)` is present  | `Connected` / `Not connected` (with a `Connect now` link).                   |
| **Drive source** | `settings.source_drive_folder_id`                   | `Configured` (truncated id via `mid`) / `Not configured` (`Set folder`).     |
| **Deepgram**     | `deepgram_store.has_key(user.id)`                   | `Configured` / `Not configured`; always links to `/settings/deepgram`.       |
| **Transcription**| `app.state.transcription_status`                    | Local model status — see below.                                              |
| **Queue**        | `_queue_status()`                                   | `Polling (sem Redis)` / `Online` / `Indisponível` — see below.               |

Below the cards: **Total jobs** (count), **Last job** (status badge + file name
or `Manual run` + `created_at` via `dt`), a **Quick actions** row (`Jobs`,
`Settings`, `Deepgram`), and a **Recent jobs** list (the five most recent,
linking to each job detail).

The header shows a `Connect Google` button only when Google is not yet
connected.

#### Transcription card states

The card reflects the provider posture computed by
`get_transcription_provider_status` (see [Local transcription](06-local-transcription.md)):

- **Local engine valid** (`transcription_status.local_valid`): shows the green
  status `message` (e.g. `Modelo local ativo: faster-whisper small int8`) with
  the hint `Deepgram opcional, modelo local será usado.`
- **Local enabled but invalid** (`enabled` and not `local_valid`): shows the
  off-state `message` (`Modelo local inválido. Consulte a documentação de
  modelos locais.`) and, when set, a link to `LOCAL_TRANSCRIPTION_DOC_URL`
  (`Documentação de modelos locais`).
- **Local disabled** (default): shows the hint `Deepgram (transcrição local
  desativada).` In this mode a per-user Deepgram key is required.

#### Queue card states

`_queue_status()` never lets a status probe `500` the page:

- **`poll`** — `QUEUE_BACKEND=none`; no Redis. Renders `Polling (sem Redis)`.
- **`queue` + available** — Redis reachable. Renders `Online` /
  `Redis · processamento sequencial`.
- **`queue` + unavailable** — Redis configured but `queue.health()` failed or
  raised. Renders `Indisponível` / `Jobs ficam pendentes até o Redis voltar.`

### Settings landing (`GET /settings`)

`settings.html` is a simple hub with two options, each a `Configure` link:
**Drive folders** (`/settings/drive`) and **Deepgram API key**
(`/settings/deepgram`).

### Drive settings (`GET/POST /settings/drive`)

`settings_drive.html` collects three fields and posts them to
`POST /settings/drive`:

| Field                          | Form name                        | Required |
| ------------------------------ | -------------------------------- | -------- |
| Source folder URL              | `source_drive_folder_url`        | yes      |
| Destination folder URL         | `destination_drive_folder_url`   | no       |
| Save a TXT copy to Drive       | `save_copy_to_drive` (checkbox)  | no       |

Folders are entered **as Google Drive URLs**, not raw ids. The handler runs
`extract_google_drive_folder_id(...)` (`app/web/drive_links.py`) on each URL; on
a bad URL it re-renders the form with the `ValueError` message and `400`. On
success it persists a `DriveSettings` record (URL + extracted id for both
source and destination, plus the `save_copy_to_drive` flag), flashes `Drive
settings salvos.`, and redirects (303).

The destination folder and `save_copy_to_drive` together gate the optional Drive
TXT backup the worker performs after a transcript is stored in PostgreSQL — the
backup happens only when both are present.

### Deepgram settings (`GET/POST /settings/deepgram`, `POST /settings/deepgram/test`)

`settings_deepgram.html` shows whether a key is `configured`, its `masked`
preview, and a form to save a new key plus a `Test` action.

- **Save** (`POST /settings/deepgram`, field `deepgram_api_key`): an empty value
  flashes `Deepgram API Key não pode ser vazia.`; otherwise the key is stored
  through `DeepgramKeyStore.save_for_user` (**encrypted at rest** with the Fernet
  key derived from `APP_SECRET_KEY`) and flashes `Deepgram API Key salva.`
- **Test** (`POST /settings/deepgram/test`): if no key is saved it flashes
  `Configure sua Deepgram API Key antes de iniciar uma transcrição.`; otherwise
  `verify_deepgram_key(key)` returns `valid` / `invalid` / `unverifiable`, mapped
  to `Deepgram API Key válida.` / `Deepgram API Key inválida.` / `Não foi
  possível verificar agora.`

Keys are **per-user**. The web/worker deployment never reads the global
`DEEPGRAM_API_KEY` (that variable belongs to the legacy CLI only). When a valid
local engine is active, a Deepgram key is not required.

### Connect Google (`GET /connect-google`, `GET /oauth/google/callback`)

`GET /connect-google` starts the OAuth 2.0 flow. It generates a CSRF `state`,
stores it in the session, and redirects to
`https://accounts.google.com/o/oauth2/v2/auth` with:

| Param           | Value                                              |
| --------------- | -------------------------------------------------- |
| `client_id`     | `GOOGLE_WEB_CLIENT_ID`                              |
| `redirect_uri`  | `GOOGLE_REDIRECT_URI`                               |
| `response_type` | `code`                                             |
| `scope`         | `https://www.googleapis.com/auth/drive`            |
| `access_type`   | `offline` (to obtain a refresh token)              |
| `prompt`        | `consent`                                          |
| `state`         | random CSRF token                                  |

`GET /oauth/google/callback` validates `state` against the session (rejecting a
mismatch with `400 Invalid OAuth state`), exchanges the `code` for tokens via
`exchange_google_code(...)` against `https://oauth2.googleapis.com/token`, and
persists them through `token_store.save_for_user` — **encrypted access and
refresh tokens** in `google_tokens`. It then best-effort fetches the account's
email/name (`fetch_google_userinfo`) to label the user and redirects to `/`.

`GOOGLE_REDIRECT_URI` must exactly match what is registered in Google Cloud,
e.g. `http://localhost:8000/oauth/google/callback` locally or
`https://DOMAIN/oauth/google/callback` in production.

### Admin users (`/admin/users`)

`admin_users.html` (admin-only) lists every user (`repos.users.list_all()`) and
exposes management actions, each a POST that flashes a result and redirects back:

| Action             | Route                                      | Notes                                                       |
| ------------------ | ------------------------------------------ | ----------------------------------------------------------- |
| Create user        | `POST /admin/users`                        | Fields `email`, `password`, `role` (`user` or `admin`). Rejects empty fields and duplicate emails. Password hashed with `hash_password`. |
| Disable user       | `POST /admin/users/{user_id}/disable`      | `set_active(user_id, False)` → `Usuário desativado.`        |
| Enable user        | `POST /admin/users/{user_id}/enable`       | `set_active(user_id, True)` → `Usuário ativado.`            |
| Reset password     | `POST /admin/users/{user_id}/reset-password` | Field `new_password`; empty value rejected. → `Senha redefinida.` |

A disabled user is rejected at the next `require_user` check and bounced to
`/login`.

### Jobs list (`GET /jobs`, `POST /jobs/run-once`)

`jobs.html` is the operational hub. A `Run once` button posts to
`/jobs/run-once`. The page renders contextual notices above the table:

- **Queue unavailable** (`queue_status.mode == 'queue' and not available`):
  warns that new transcriptions are recorded but will only run once Redis
  returns.
- **Local invalid** (`enabled` and not `local_valid`): warns the local engine is
  disabled until configuration is fixed, links to `LOCAL_TRANSCRIPTION_DOC_URL`
  when set, and tells the user to configure a Deepgram key.
- **Local valid**: an info notice `... — Deepgram opcional.`
- Any **flash** message and any **`backend_error`** (worker repository backend
  unavailable) are shown as notices.

The table lists, per job: **File** (`source_file_name` or `Manual run`, linking
to job detail), **Source** (`source_file_id`, truncated via `mid`), **Status**
(a `badge badge-<status>`), **Transcript** (for completed jobs a `Download TXT`
button and, when `transcript_drive_file_id` is set, a `Drive` link to the backup
copy), and **Created** (`created_at` via `dt`).

Jobs are **user-scoped** via `list_jobs_for_user(user.id)` — you only ever see
your own jobs.

#### Run once (`POST /jobs/run-once`)

This is the only "start work" action, and it **never transcribes**. It calls
`create_next_pending_job(...)` (`app/services/job_service.py`), passing
`deepgram_required` from the live transcription status (a valid local engine
drops the Deepgram requirement). The result `status` maps to a flash message:

| `status`          | Flash message                                                          |
| ----------------- | --------------------------------------------------------------------- |
| `no_settings`     | `Configure a pasta de origem em Drive Settings primeiro.`             |
| `not_connected`   | `Conecte o Google antes de rodar uma transcrição.`                   |
| `no_deepgram_key` | `Configure sua Deepgram API Key antes de iniciar uma transcrição.`   |
| `no_new_videos`   | `Nenhum vídeo novo para transcrever.`                                 |
| `created`         | `Job enfileirado; o worker fará o processamento.`                     |

When a job is `created` and a queue is configured, the handler best-effort
`enqueue`s the new `job.id`. If Redis is down the enqueue fails *gracefully*: the
job stays `pending` in PostgreSQL (the source of truth), the worker reconciles it
on startup/idle, and the flash becomes `Fila indisponível no momento: a
transcrição foi registrada e será processada assim que a fila voltar.` Any
unexpected Drive/credential error is caught and surfaced as `Não foi possível
iniciar a transcrição agora. Tente novamente.` — never a `500`.

### Job detail (`GET /jobs/{job_id}`)

`job_detail.html` shows a single job with its attempts, error message, and full
(untruncated) timestamps and ids. Access is **strictly owner-scoped**: the
handler loads the job and renders a `404` (`error.html`, `Job not found.`) when
the id is unknown *or* `job.user_id != user.id`. Even an admin gets a `404` for
another user's job, so the existence of other users' jobs never leaks.

### Download TXT (`GET /jobs/{job_id}/download`)

Returns the human-readable transcript as a downloadable `.txt`
(`PlainTextResponse` with `Content-Disposition: attachment`). The text is the
`transcript_text` stored in the `transcripts` table — the same value regardless
of provider.

It calls `get_downloadable_transcript(worker_repos, job_id, user.id,
is_admin=False)` (`app/services/download_service.py`). `DownloadError` codes map
to HTTP status:

| `DownloadError.code` | HTTP status | Meaning                                  |
| -------------------- | ----------- | ---------------------------------------- |
| `not_found`          | `404`       | No such job for this user.               |
| `not_completed`      | `409`       | Job exists but is not `completed`.       |
| `no_transcript`      | `404`       | Completed but no stored transcript text. |
| (other)              | `400`       | Any other download error.                |

If the worker repository backend is unavailable the route returns `503` with the
backend error detail. As with job detail, ownership is enforced strictly, so a
download cannot leak another user's transcript.

## Operating the UI

```bash
# Bring the stack up (postgres → redis → migrate → web + worker)
docker compose up -d

# Health check
curl -s http://localhost:8000/health     # {"status":"ok"}

# Then, in a browser:
#   1. http://localhost:8000/login            sign in (ADMIN_USERNAME/PASSWORD)
#   2. /connect-google                         authorize Google Drive
#   3. /settings/drive                         paste source (and optional dest) folder URLs
#   4. /settings/deepgram                      save + test your key (skip if a local engine is active)
#   5. /jobs  →  Run once                      enqueue the next new recording
#   6. refresh /jobs  →  Download TXT          grab the transcript when completed
```

The web container runs `uvicorn app.web.main:app --host 0.0.0.0 --port 8000` and
shares its image with the worker; it starts only after `postgres` is healthy,
`redis` is healthy, and the one-shot `migrate` service has run `alembic upgrade
head`. See [Deployment](13-dokploy-deploy.md) for the full compose topology.
