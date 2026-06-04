# Overview

**meet-transcription** watches a Google Drive folder for Google Meet recordings,
transcribes each MP4 (with **Deepgram** or a **local CPU engine**), saves the
transcript in **PostgreSQL**, and serves a **Download TXT** from a server-rendered
web UI. Google Drive is the input; an optional `.txt` backup copy can be uploaded
to a destination Drive folder.

This page is the starting point. It explains what the project is, who it is for,
the end-to-end flow, the two transcription providers at a glance, and where to go
next in the `documentation/` folder.

## What it is

meet-transcription ships as one container image run as two long-lived services
plus supporting infrastructure:

- a **web app** (`uvicorn app.web.main:app`, FastAPI) for signing in, connecting
  Google, configuring Drive folders, saving a per-user Deepgram key, and
  triggering / inspecting jobs; and
- a **worker** (`python -m app.worker.main`) that processes transcription jobs
  out of band.

**PostgreSQL is the single source of truth — there is no SQLite mode.** Users,
settings, jobs, and transcripts live in Postgres. Google tokens and per-user
Deepgram API keys are **encrypted at rest** (Fernet, with the key derived from
`APP_SECRET_KEY`).

The UI is server-rendered **Jinja2** (`app/web/templates/`) with local CSS
(`app/web/static/styles.css`) — **no React/SPA and no build step**. Some
user-facing strings are in Portuguese, as in the running app.

## Who it is for

- **Teams that record meetings in Google Meet** and want a searchable,
  downloadable plain-text transcript of each recording without manual steps.
- **Self-hosters / operators** who want to run the stack with Docker Compose,
  keep Postgres and Redis internal, and expose only the web service.
- **Privacy-conscious users** who prefer to transcribe **locally on CPU** (no
  third-party API, no audio leaving the host) using faster-whisper or whisper.cpp
  instead of Deepgram.

Each user supplies their **own** Deepgram key (encrypted, **no global
fallback**) and configures their **own** source/destination Drive folders, so a
single deployment can serve multiple users.

## End-to-end flow

```
 Google Meet records ──► Google Drive (source folder)
                                │
                                ▼
        web UI: user clicks "Run once" ──► validate + create a `pending` job in Postgres (+ enqueue id)
                                │
                                ▼
        worker: dequeue ──► global lock ──► claim job (pending → processing)
                                │
                                ▼
   download MP4 from Drive ──► transcribe (Deepgram OR local CPU) ──► save transcript in Postgres
                                │
                                ├─(optional)─► upload .txt backup to destination Drive folder
                                ▼
        web UI: "Download TXT" for the completed job (served from Postgres)
```

Step by step:

1. **Meet records to Drive.** Google Meet saves the recording MP4 into the user's
   Google Drive **source folder**.
2. **App detects the video.** In the web UI the user connects Google, sets the
   **source** Drive folder (and optionally a **destination** folder), and saves
   their Deepgram key (unless a valid local engine is active). Clicking **Run
   once** validates these preconditions and creates a single `pending`
   `transcription_jobs` row for the next unprocessed recording. **The HTTP
   request never downloads, transcribes, or uploads** — it only validates and
   enqueues the job id.
3. **Worker transcribes.** The worker dequeues the job id, acquires a **global
   Redis lock** (so only one CPU transcription runs at a time), atomically claims
   the job (`pending → processing` in Postgres), downloads the MP4 from Drive,
   and runs the resolved provider.
4. **Saves in Postgres.** The transcript is written to the `transcripts` table
   (`transcript_text` for the human-readable `.txt`, plus a normalized
   `transcript_json`), and the job is marked `completed`. If `save_copy_to_drive`
   is on **and** a destination folder is set, the worker also uploads a `.txt`
   copy to Drive and the UI links to it.
5. **User downloads TXT in the UI.** The Jobs page shows a status badge and a
   **Download TXT** link for each completed job, served straight from Postgres.

On any failure the job is marked `failed` with a friendly, secret-free
`error_message` (tracebacks stay in logs only).

## Services at a glance

The deployment (`docker-compose.yml`) is five services; **web** and **worker**
share one image (built from `Dockerfile`, base `python:3.11-slim`):

| Service | Command / role | Notes |
|---|---|---|
| `postgres` | `postgres:16` database | Single source of truth; `postgres_data` volume; `pg_isready` healthcheck. |
| `redis` | `redis:7-alpine` | Queue + global transcription lock; `redis_data` volume. |
| `migrate` | `alembic upgrade head` (one-shot) | Runs the schema migration and exits (`restart: "no"`). |
| `web` | `uvicorn app.web.main:app --host 0.0.0.0 --port 8000` | UI + OAuth on port **8000**. Never transcribes in-request. |
| `worker` | `python -m app.worker.main` | DB-driven job processor (download → transcribe → save). |

**Startup order:** `postgres` healthy → `redis` healthy → `migrate` runs
`alembic upgrade head` and exits 0 → `web` and `worker` start.

A **legacy CLI worker** (`python -m app.main`, with `--once` / `--watch` /
`--reprocess`) still exists for **compatibility only**. It is env-driven, uses a
mounted `token.json` (or a Service Account), stores state in
`data/processed_files.json`, reads the global `DEEPGRAM_API_KEY`, and is **not** a
Compose service.

## Transcription providers at a glance

The provider is resolved per job (`app/transcription/factory.py` `resolve_provider`,
`app/transcription/provider.py` `get_transcription_provider_status`):

| Provider | When it is used | Key requirement |
|---|---|---|
| **Deepgram** (cloud) | `LOCAL_TRANSCRIPTION_ENABLED=false` (default) | Per-user encrypted Deepgram key **required**. No global fallback. |
| **Local CPU** (`faster-whisper` or `whisper-cpp`) | `LOCAL_TRANSCRIPTION_ENABLED=true` **and** valid config | **No Deepgram key required.** UI shows *"Modelo local ativo: …"*. |

Rules:

- **disabled** → Deepgram, per-user key required.
- **enabled + valid** → local engine used; no Deepgram key required.
- **enabled + invalid** → Deepgram required again; the UI shows *"Modelo local
  inválido. Consulte a documentação de modelos locais."* with a docs link, and
  **Run once is blocked** unless a Deepgram key is set. **There is no silent
  fallback.**

Both engines support multilingual models (`tiny`, `base`, `small`, `medium`,
`large-v1`, `large-v2`, `large-v3`, `large-v3-turbo`); do **not** use `.en`
models. Local engines are heavy and are **not** in the base image — opt in at
build time (e.g. `--build-arg INSTALL_FASTER_WHISPER=true`) or provide an
external `WHISPER_CPP_BINARY`.

## Where to go next

The `documentation/` folder is organized by `NN-*.md` files:

| File | What it covers |
|---|---|
| [00-overview.md](00-overview.md) | This page: what the project is, the end-to-end flow, providers, and this map. |
| [01-architecture.md](01-architecture.md) | The 5-service topology (postgres, redis, migrate, web, worker), the shared image, and startup order. |
| [02-installation.md](02-installation.md) | Cloning, `.env` setup, running locally and with Docker Compose, migrations, and first login. |
| [03-environment-variables.md](03-environment-variables.md) | Every environment variable and Docker build arg, grouped by block, with risk-if-wrong. |
| [04-google-oauth.md](04-google-oauth.md) | Google Cloud OAuth setup: Drive API, Web application client, the redirect URI, and `redirect_uri_mismatch`. |
| [05-deepgram.md](05-deepgram.md) | The per-user, encrypted Deepgram key, the Test button, and common Deepgram errors. |
| [06-local-transcription.md](06-local-transcription.md) | Local CPU transcription: the provider-selection rule, models, languages, limitations, and trade-offs. |
| [07-faster-whisper.md](07-faster-whisper.md) | The faster-whisper engine: CPU/int8, models, `local_files_only`, validation, and common problems. |
| [08-whisper-cpp.md](08-whisper-cpp.md) | The whisper.cpp engine: external `whisper-cli`, `MODEL_PATH`, quantizations, ffmpeg WAV, JSON parsing. |
| [09-redis-queue.md](09-redis-queue.md) | The Redis queue + global lock: FIFO list, dedupe set, `requeue_pending_jobs`, and `QUEUE_BACKEND` modes. |
| [10-postgres-and-migrations.md](10-postgres-and-migrations.md) | Postgres tables, the migrate service, the `JobRepository` contract, backups, and the no-SQLite rule. |
| [11-worker-flow.md](11-worker-flow.md) | The worker job lifecycle: stale recovery, queue vs. poll loops, `JobProcessor.process`, scratch handling. |
| [12-web-ui.md](12-web-ui.md) | The FastAPI web service: routes, session auth, dashboard status cards, settings, Run once / Download TXT. |
| [13-dokploy-deploy.md](13-dokploy-deploy.md) | Production deployment on Dokploy: domain on web only, internal Postgres/Redis, env, volumes, first run. |
| [14-ghcr.md](14-ghcr.md) | The GitHub Actions CI/CD: triggers, test + build jobs, and the GHCR image tags. |
| [15-troubleshooting.md](15-troubleshooting.md) | A symptom / cause / fix / where-to-look catalog of common failures. |
| [16-security.md](16-security.md) | Fernet encryption of tokens/keys, secret hygiene, session cookies, internal-only services, and backups. |
| [17-development.md](17-development.md) | Code layout, the repository-interface pattern, conventions, and the validation commands. |
| [18-testing.md](18-testing.md) | Unit fakes, skipped PostgreSQL tests, how local engines are mocked, and running the suite. |
| [19-roadmap.md](19-roadmap.md) | What is done vs planned: whisper.cpp multiarch, auto-download, diarization, summaries, and more. |
