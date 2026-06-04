# Installation

This guide walks through two supported ways to install **meet-transcription**:

1. **Local development install** — a Python virtualenv to run the test suite and
   the web app with hot reload against a PostgreSQL you provide.
2. **VPS / server install** — the full five-service stack via **Docker Compose**
   (`postgres`, `redis`, `migrate`, `web`, `worker`).

For the moving parts behind these commands, see
[Overview](00-overview.md) and [Architecture](01-architecture.md). The
authoritative list of environment variables lives in `.env.example` at the repo
root.

> **PostgreSQL is the single source of truth — there is no SQLite mode.**
> Both install paths require a reachable PostgreSQL database. The application
> stores users, settings, jobs, and transcripts there; never point
> `DATABASE_URL` at a SQLite file.

---

## Prerequisites

| Path | Required |
|---|---|
| Local development | Python **3.11** (the image base is `python:3.11-slim`), `pip`, `git`, and a reachable PostgreSQL 16 instance |
| VPS / server | A Linux host with **Docker** and the **Docker Compose v2** plugin (`docker compose ...`), plus `git` |

The application image is built from `Dockerfile` (base `python:3.11-slim`) and is
shared by the `web` and `worker` services. Runtime dependencies are pinned in
`requirements.txt` (FastAPI, Starlette, Uvicorn, Jinja2, SQLAlchemy 2 +
Alembic + `psycopg[binary]`, `redis`, Google API client, `cryptography`,
`bcrypt`/`passlib`, `pytest`).

> Local CPU transcription engines (faster-whisper / whisper.cpp) are **not**
> installed in the base image — they are heavy and CPU/arch-specific. They are
> opt-in at image build time. See [Local transcription configuration](#local-cpu-transcription-optional)
> below.

---

## Part 1 — Local development install

This path is for running the test suite and iterating on the web UI locally. The
worker and queue are not required to run the tests.

### 1. Clone the repository

```bash
git clone https://github.com/gabedsam01/meet-transcription.git
cd meet-transcription
```

### 2. Create a virtualenv and install dependencies

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

### 3. Create your `.env`

```bash
cp .env.example .env
```

Open `.env` and fill in the secrets (at minimum `APP_SECRET_KEY`, the
`ADMIN_*` credentials, and a `DATABASE_URL` that points at your local
PostgreSQL). To generate a strong `APP_SECRET_KEY`, see
[Generating `APP_SECRET_KEY`](#generating-app_secret_key).

For a **local** PostgreSQL, the DSN host is `localhost` (inside Docker it is the
service name `postgres`):

```env
DATABASE_URL=postgresql+psycopg://meet_user:your-db-password@localhost:5432/meet_transcription
```

The DSN must use the `postgresql+psycopg://` (psycopg 3) driver. The user,
password, and database in `DATABASE_URL` must match the actual PostgreSQL you
run against.

### 4. Run the test suite

The tests use dict-backed in-memory fakes for the repository layer, so they do
**not** require a database for most cases:

```bash
.venv/bin/python -m pytest -v
```

PostgreSQL integration tests are **skipped automatically** when
`TEST_DATABASE_URL` (or `DATABASE_URL`) is unset or unreachable — they never
fall back to SQLite. To exercise them, point `TEST_DATABASE_URL` at a disposable
PostgreSQL 16 database:

```bash
TEST_DATABASE_URL=postgresql+psycopg://meet_user:pass@localhost:5432/meet_test \
  .venv/bin/python -m pytest -v
```

You can also run the syntax/compile check used in CI:

```bash
.venv/bin/python -m compileall app scripts
```

### 5. Apply the database schema

Before the web app can serve anything against a real database, apply the Alembic
migrations (the initial migration is
`alembic/versions/0001_create_initial_postgres_schema.py`):

```bash
.venv/bin/alembic upgrade head
```

Alembic reads `DATABASE_URL` from your environment / `.env`.

### 6. Run the web app

```bash
.venv/bin/uvicorn app.web.main:app --reload --port 8000
```

Open `http://localhost:8000` and sign in with your `ADMIN_USERNAME` /
`ADMIN_PASSWORD`. See [First login](#first-login).

> The web service only **validates and enqueues** work — it never transcribes in
> an HTTP request. To actually process a job locally you also need the worker
> (`python -m app.worker.main`) and, for the Redis queue, a running Redis. For a
> single-process dev run without Redis, set `QUEUE_BACKEND=memory` in `.env`.

---

## Part 2 — VPS / server install (Docker Compose)

This is the recommended production-style deployment. `docker-compose.yml`
defines **five services**:

| Service | Image / command | Role |
|---|---|---|
| `postgres` | `postgres:16` | Single source of truth; `pg_isready` healthcheck; `postgres_data` volume |
| `redis` | `redis:7-alpine` | Transcription queue + global execution lock; `redis-cli ping` healthcheck; `redis_data` volume |
| `migrate` | app image, `alembic upgrade head` | One-shot schema migration (`restart: "no"`); exits after applying migrations |
| `web` | app image, `uvicorn app.web.main:app --host 0.0.0.0 --port 8000` | UI + OAuth on port **8000** |
| `worker` | app image, `python -m app.worker.main` | Claims jobs, downloads, transcribes, saves transcripts |

`web` and `worker` run from the **same image** (`x-app` anchor:
`ghcr.io/gabedsam01/meet-transcription:latest`, `build: .`) with different
commands.

### Startup order

Compose enforces this ordering via healthchecks and
`service_completed_successfully`:

```
postgres (healthy) ─┐
                    ├─► migrate (runs `alembic upgrade head`, exits 0) ─► web + worker start
redis    (healthy) ─┘
```

The `web` and `worker` services depend on `postgres` healthy, `redis` healthy,
**and** `migrate` completed successfully, so the schema is always current before
any request is served or any job is claimed.

### 1. Clone and create `.env`

```bash
git clone https://github.com/gabedsam01/meet-transcription.git
cd meet-transcription
cp .env.example .env
```

Compose reads `.env` for `${VAR}` substitution. Every variable in
`docker-compose.yml` has a safe default so `docker compose config`/`build`
succeed even with an empty `.env`, but **production must override the secrets**.

### 2. Generating `APP_SECRET_KEY`

`APP_SECRET_KEY` is used both for session signing **and** to derive the Fernet
key that encrypts Google tokens and per-user Deepgram keys at rest. Generate a
strong random value with this Python one-liner and paste it into `.env`:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(64))"
```

```env
APP_SECRET_KEY=<paste-the-generated-value-here>
```

> Do not reuse the placeholder `change-me-in-production`. Changing
> `APP_SECRET_KEY` later invalidates all previously stored encrypted tokens and
> Deepgram keys, so set it once before first run and keep it stable.

### 3. Fill in the rest of `.env`

At minimum, set the following for a server deployment:

| Variable | Purpose |
|---|---|
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | Bootstrap admin login for the web UI |
| `APP_SECRET_KEY` | Session signing + Fernet encryption key (see above) |
| `SESSION_COOKIE_SECURE` | Set `true` when serving over HTTPS |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | PostgreSQL credentials |
| `DATABASE_URL` | Must match the `POSTGRES_*` values; host is `postgres` inside Compose |
| `GOOGLE_WEB_CLIENT_ID`, `GOOGLE_WEB_CLIENT_SECRET` | OAuth **Web application** credentials |
| `GOOGLE_REDIRECT_URI` | Must exactly equal `https://YOUR_DOMAIN/oauth/google/callback` (or `http://localhost:8000/oauth/google/callback` for local) |

Inside Compose, the database DSN uses the **service name** `postgres` as the
host:

```env
DATABASE_URL=postgresql+psycopg://meet_user:your-db-password@postgres:5432/meet_transcription
POSTGRES_DB=meet_transcription
POSTGRES_USER=meet_user
POSTGRES_PASSWORD=your-db-password
```

Queue defaults in Compose: `QUEUE_BACKEND=redis`,
`REDIS_URL=redis://redis:6379/0`, `QUEUE_NAME=transcription`,
`TRANSCRIPTION_GLOBAL_LOCK_TTL_SECONDS=14400`. Worker defaults:
`WORKER_REPOSITORY_BACKEND=postgres`, `WORKER_POLL_INTERVAL_SECONDS=30`,
`WORKER_CONCURRENCY=1`, `STALE_JOB_TIMEOUT_MINUTES=30`. Keep
`WORKER_REPOSITORY_BACKEND=postgres` in production — `memory` is dev/tests only.

The Web UI uses **per-user, encrypted** Deepgram keys, so the server deployment
needs **no global `DEEPGRAM_API_KEY`**. That variable is consumed only by the
legacy CLI (`python -m app.main`), which is not a Compose service.

> Validate your composition before building:
> ```bash
> docker compose config
> ```

### 4. Build the image

```bash
docker compose build
```

This builds `ghcr.io/gabedsam01/meet-transcription:latest` locally from
`Dockerfile`. Alternatively, in production you can pull the published image
instead of building it:

```bash
docker compose pull
```

### 5. Run the database migration

The `migrate` service runs `alembic upgrade head` and exits. Run it explicitly
first so the schema is applied before anything else starts:

```bash
docker compose run --rm migrate
```

This is equivalent to the one-time setup `docker compose run --rm web alembic
upgrade head`; either creates the tables defined in
`app/database/models.py` via `alembic/versions/0001_create_initial_postgres_schema.py`.

> If you start the whole stack with `docker compose up -d` without running
> migrate first, Compose still runs the `migrate` service as a dependency of
> `web`/`worker` (and waits for it to exit successfully) before they start. The
> explicit step above is useful to confirm migrations succeed on their own.

### 6. Start the stack

```bash
docker compose up -d
```

This starts `postgres`, `redis`, `migrate` (one-shot), `web`, and `worker` in
the correct order. The web UI is published on host port **8000**
(`http://localhost:8000` or `https://YOUR_DOMAIN`). PostgreSQL and Redis are
**internal only** — they are not published to the host.

### 7. View logs per service

```bash
docker compose ps                  # see service status + which exited (migrate)
docker compose logs -f web         # web (UI/OAuth) — follow
docker compose logs -f worker      # worker (job processing) — follow
docker compose logs migrate        # one-shot migration output (it has exited)
docker compose logs -f postgres    # database
docker compose logs -f redis       # queue + lock
```

To follow web and worker together:

```bash
docker compose logs -f web worker
```

### Stopping and updating

```bash
docker compose down                # stop the stack (keeps named volumes/data)
docker compose pull && docker compose up -d   # update to a newer image
```

The `postgres_data` and `redis_data` named volumes persist across `down`/`up`;
your database (the single source of truth) and its backup live in
`postgres_data`.

---

## First login

After `docker compose up -d` (or the local `uvicorn` run), open the web UI on
port **8000** and sign in with the bootstrap credentials you set in `.env`:

- **Username:** `ADMIN_USERNAME`
- **Password:** `ADMIN_PASSWORD`

This admin account is created from those environment variables. After signing
in, the typical first-run sequence is:

1. **Connect Google** — start the OAuth flow (`Connect Google` → Google consent
   → `/oauth/google/callback`). The requested scope is
   `https://www.googleapis.com/auth/drive`. The returned tokens are stored
   **encrypted** in PostgreSQL.
2. **Settings → Drive folders** — paste the Google Drive **source** folder link
   (or bare folder id), and optionally a **destination** folder for the optional
   TXT backup copy.
3. **Settings → Deepgram** — save your **own** Deepgram API key (stored
   encrypted; required unless a valid local engine is active). Use
   **Settings → Deepgram → test** to verify it.
4. **Run once** — from Jobs, create a single `pending` job for the next
   unprocessed recording. The worker picks it up, transcribes it, saves the
   transcript, and the Jobs page offers **Download TXT** when the job is
   `completed`.

> The dashboard shows status cards for Google, Drive source, Deepgram,
> Transcription (local model status), Queue (Redis online/offline/poll), total
> jobs, and the last job — use these to confirm each precondition is satisfied
> before clicking **Run once**.

For the Google Cloud side of OAuth setup (creating a **Web application** OAuth
client and registering the redirect URI), see the project `README.md` section
**Google OAuth Setup**.

---

## Local CPU transcription (optional)

By default `LOCAL_TRANSCRIPTION_ENABLED=false`, so transcription uses Deepgram
with a per-user key. To transcribe **locally on CPU** instead, you must both
**build the engine into the image** and **enable it at runtime**.

### Build the engine in (Docker build args)

These are **build-time** args (`INSTALL_*`), not runtime env vars:

```bash
# faster-whisper (pip-installs faster-whisper)
docker compose build --build-arg INSTALL_FASTER_WHISPER=true

# whisper.cpp prerequisites (apt-installs ffmpeg; the whisper-cli binary itself
# is external — provide it via WHISPER_CPP_BINARY, it is NOT compiled into the image)
docker compose build --build-arg INSTALL_WHISPER_CPP=true
```

`INSTALL_LOCAL_TRANSCRIPTION=true` installs both. All three default to `false`.

### Enable and configure it (runtime env)

Set in `.env`:

```env
LOCAL_TRANSCRIPTION_ENABLED=true
LOCAL_TRANSCRIPTION_ENGINE=faster-whisper   # or whisper-cpp
LOCAL_TRANSCRIPTION_MODEL=small             # tiny|base|small|medium|large-v1|large-v2|large-v3|large-v3-turbo
LOCAL_TRANSCRIPTION_LANGUAGE=auto           # auto|pt|en|...
```

Model files are mounted read-only at `/models` (`./models:/models:ro` on `web`
and `worker`; `LOCAL_TRANSCRIPTION_MODEL_DIR=/models`). Use **multilingual**
models only — do **not** use `.en` models (pt-BR + English are both needed).

- **faster-whisper:** CPU compute type via `LOCAL_TRANSCRIPTION_COMPUTE_TYPE`
  (`int8` default, `int8_float32`, `float32`); only this engine can auto-download
  when `LOCAL_TRANSCRIPTION_AUTO_DOWNLOAD=true`.
- **whisper.cpp:** set `WHISPER_CPP_BINARY` (path to `whisper-cli`),
  `LOCAL_TRANSCRIPTION_QUANTIZATION` (`q4_0`|`q4_1`|`q5_0`|`q5_1`|`q8_0`), and
  always `LOCAL_TRANSCRIPTION_MODEL_PATH` (required for whisper.cpp).

Provider selection rule (no silent fallback):

| State | Result |
|---|---|
| `LOCAL_TRANSCRIPTION_ENABLED=false` | Deepgram; per-user key required |
| enabled + **valid** | Local engine used; **no** Deepgram key required. UI shows *"Modelo local ativo: …"* |
| enabled + **invalid** | UI shows *"Modelo local inválido. Consulte a documentação de modelos locais."* with a link to `LOCAL_TRANSCRIPTION_DOC_URL`; **Run once** is blocked unless a Deepgram key is set |

Full configuration, model/quantization details, and VPS sizing live in
`docs/architecture/local-transcription.md`.

---

## Validation checklist

After installing, confirm the deployment is healthy:

```bash
docker compose ps                       # postgres+redis healthy; migrate exited 0; web+worker up
docker compose logs migrate             # shows alembic upgrade head completing
curl -fsS http://localhost:8000/health  # web health endpoint
docker compose logs -f web worker       # watch for errors
```

For local development, the equivalent checks are `python -m pytest -v`,
`python -m compileall app scripts`, and opening `http://localhost:8000/health`.
