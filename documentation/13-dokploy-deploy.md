# Deploying on Dokploy

This guide deploys the full **5-service** Compose project on
[Dokploy](https://dokploy.com/): **postgres + redis + migrate + web + worker**.
The `web` and `worker` services share **one** application image
(`ghcr.io/gabedsam01/meet-transcription:latest`) and run different commands;
`migrate` reuses the same image to apply the schema once and exit.

The deployment watches a Google Drive folder for Google Meet recordings,
transcribes each MP4 (Deepgram **or** a local CPU engine), stores the transcript
in PostgreSQL, and serves a **Download TXT** from the web UI. See
[Architecture](01-architecture.md) for the full picture.

## What gets exposed

| Service    | Image / command                                     | Public domain | Internal only |
| ---------- | --------------------------------------------------- | :-----------: | :-----------: |
| `postgres` | `postgres:16` (single source of truth)              |      no       |    **yes**    |
| `redis`    | `redis:7-alpine` (queue + global lock)              |      no       |    **yes**    |
| `migrate`  | app image, `alembic upgrade head` (one-shot, exits) |      no       |       —       |
| `web`      | app image, `uvicorn app.web.main:app … --port 8000` |    **yes**    |       —       |
| `worker`   | app image, `python -m app.worker.main`              |      no       |    **yes**    |

Only `web` has an HTTP surface. Attach your domain to **`web` on port 8000** and
to nothing else. `postgres` and `redis` must stay internal — they are reachable
only over the internal Compose network, at hostnames `postgres:5432` and
`redis:6379`.

## Startup order

The Compose file enforces this sequence with `depends_on` conditions, so you do
not orchestrate anything by hand:

1. `postgres` becomes **healthy** (`pg_isready`).
2. `redis` becomes **healthy** (`redis-cli ping`).
3. `migrate` runs `alembic upgrade head` and **exits 0** (`restart: "no"`).
4. `web` and `worker` start (each waits on
   `postgres: service_healthy`, `redis: service_healthy`,
   `migrate: service_completed_successfully`).

The schema is therefore always current before any request is served or any job
is claimed. No manual migration step is required on deploy.

## 1. Create the Compose application

In Dokploy, create a **Compose** service pointing at this repository (or paste
`docker-compose.yml`). The image is published to GHCR by CI, so for production
pull the image rather than building. Edit the `x-app` anchor:

```yaml
x-app: &app
  image: ghcr.io/gabedsam01/meet-transcription:latest
  # build: .        # comment out in production; pull the GHCR image instead
  restart: unless-stopped
```

If the GHCR package is private, add a registry credential for `ghcr.io` in
Dokploy so it can pull the image.

> Local transcription engines are **build-time** options
> (`INSTALL_LOCAL_TRANSCRIPTION`, `INSTALL_FASTER_WHISPER`,
> `INSTALL_WHISPER_CPP`), not runtime env vars. The published `:latest` image is
> built with the defaults (engines **off**). To run local transcription on
> Dokploy you must build an image with those build args set, then point the
> `x-app` anchor at it. See [Local transcription](06-local-transcription.md).

## 2. Attach the domain to `web` only

- Attach your domain to the **`web`** service.
- Set the container port to **8000** (`web` listens on `0.0.0.0:8000`).
- Enable HTTPS (Let's Encrypt) on the domain.
- Do **not** attach a domain to `worker`, `postgres`, or `redis`. The worker has
  no HTTP surface; Postgres and Redis must remain internal.

When HTTPS is in front of `web`, set `SESSION_COOKIE_SECURE=true` so session
cookies are only sent over TLS.

## 3. Environment variables

Set these on the Compose application in Dokploy. Use strong, unique secrets in
production — every variable has a safe default in `docker-compose.yml` (so
`docker compose config`/`build` work without a populated `.env`), but those
defaults are **not** production-safe.

### Web / admin

```env
ADMIN_USERNAME=your-admin
ADMIN_PASSWORD=a-strong-password
APP_SECRET_KEY=a-long-random-string         # ALSO the Fernet key for token/key encryption at rest
SESSION_COOKIE_SECURE=true                   # true behind HTTPS
```

`APP_SECRET_KEY` derives the Fernet encryption key (`app/web/security.py`).
Changing it makes already-stored encrypted Google tokens and Deepgram keys
undecryptable, so set it once and keep it stable.

### Database (PostgreSQL — single source of truth, never SQLite)

```env
POSTGRES_DB=meet_transcription
POSTGRES_USER=meet_user
POSTGRES_PASSWORD=a-strong-db-password
DATABASE_URL=postgresql+psycopg://meet_user:a-strong-db-password@postgres:5432/meet_transcription
```

`DATABASE_URL` must use the `postgresql+psycopg://` scheme and point at the
internal `postgres` host. The same `DATABASE_URL` is consumed by `migrate`,
`web`, and `worker`. **Never** use a `sqlite` URL — there is no SQLite mode.

### Redis / queue (internal service)

```env
QUEUE_BACKEND=redis                          # redis | memory | none
REDIS_URL=redis://redis:6379/0
QUEUE_NAME=transcription
TRANSCRIPTION_GLOBAL_LOCK_TTL_SECONDS=14400
```

Notes on `QUEUE_BACKEND`: the code default is `none`, but the Compose file
defaults it to `redis`. Keep it `redis` in production so concurrent **Run once**
clicks queue up and only one CPU transcription runs at a time behind the global
lock. `none` falls back to the legacy poll loop
(`claim_next_pending_job`); `memory` is for dev/tests only.

### Google OAuth

```env
GOOGLE_WEB_CLIENT_ID=...
GOOGLE_WEB_CLIENT_SECRET=...
GOOGLE_REDIRECT_URI=https://YOUR_DOMAIN/oauth/google/callback
```

The requested OAuth scope is `https://www.googleapis.com/auth/drive`. See
[Google OAuth setup](04-google-oauth.md) and the redirect-URI section below.

### Worker

```env
WORKER_REPOSITORY_BACKEND=postgres           # postgres | memory (memory = dev/tests only)
WORKER_POLL_INTERVAL_SECONDS=30
WORKER_CONCURRENCY=1
STALE_JOB_TIMEOUT_MINUTES=30
TMP_DIR=/app/tmp
```

### Local CPU transcription (optional; off by default)

When `LOCAL_TRANSCRIPTION_ENABLED=false` (default) the system uses **Deepgram**
and each user must save their own encrypted Deepgram key. Enabling a **valid**
local engine drops the per-user Deepgram requirement; an **invalid**
configuration falls back to requiring Deepgram (no silent fallback) and blocks
**Run once** until a Deepgram key is set. See
[Local transcription](06-local-transcription.md) and the provider rules below.

```env
LOCAL_TRANSCRIPTION_ENABLED=false
LOCAL_TRANSCRIPTION_ENGINE=faster-whisper    # faster-whisper | whisper-cpp
LOCAL_TRANSCRIPTION_MODEL=small              # tiny|base|small|medium|large-v1|large-v2|large-v3|large-v3-turbo (multilingual only)
LOCAL_TRANSCRIPTION_LANGUAGE=auto            # auto | pt | en | ...
LOCAL_TRANSCRIPTION_THREADS=4
LOCAL_TRANSCRIPTION_MODEL_DIR=/models
# faster-whisper:
LOCAL_TRANSCRIPTION_COMPUTE_TYPE=int8        # int8 | int8_float32 | float32
LOCAL_TRANSCRIPTION_AUTO_DOWNLOAD=false      # only faster-whisper can auto-download
# whisper.cpp:
LOCAL_TRANSCRIPTION_QUANTIZATION=q4_0        # q4_0 | q4_1 | q5_0 | q5_1 | q8_0
LOCAL_TRANSCRIPTION_MODEL_PATH=              # ALWAYS required for whisper.cpp (path to the .bin)
WHISPER_CPP_BINARY=                          # path to whisper-cli (external; not compiled into the image)
LOCAL_TRANSCRIPTION_DOC_URL=https://github.com/gabedsam01/meet-transcription/blob/main/docs/architecture/local-transcription.md
```

Use **multilingual** models only — do **not** use `.en` models, since both
pt-BR and English are needed.

> **Deepgram keys are per user.** The Web UI stores each user's Deepgram API key
> **encrypted** in PostgreSQL, so there is **no global `DEEPGRAM_API_KEY`** in the
> web/worker deployment. The global `DEEPGRAM_API_KEY` env var exists only for the
> legacy CLI worker (`python -m app.main`), which is not a Compose service.

## 4. Google OAuth redirect URI

In Google Cloud → APIs & Services → Credentials, the **Web application** OAuth
client must list an authorized redirect URI that exactly matches your domain:

```
https://YOUR_DOMAIN/oauth/google/callback
```

`GOOGLE_REDIRECT_URI` in the environment must be identical, character for
character (scheme, host, path), or Google rejects the login. The callback route
is `/oauth/google/callback`.

## 5. Volumes — persist these

The Compose file declares two **named** volumes plus several **bind** mounts:

| Volume / mount       | Mounted at (web/worker)        | Persist? | Purpose                                                            |
| -------------------- | ------------------------------ | :------: | ----------------------------------------------------------------- |
| `postgres_data`      | `/var/lib/postgresql/data`     | **yes**  | The database — single source of truth. Losing it loses everything.|
| `redis_data`         | `/data`                        | **yes**  | Queue/lock state. Recoverable, but avoids re-reconciling on restart.|
| `./models` (ro)      | `/models`                      | optional | Local model files (only when `LOCAL_TRANSCRIPTION_ENABLED=true`). |
| `./tmp`              | `/app/tmp`                     |    no    | Scratch MP4/transcript files during processing (ephemeral).       |
| `./data`             | `/app/data`                    |    no    | Scratch only; the database lives in Postgres, not here.           |
| `./secrets` (ro)     | `/app/secrets`                 | optional | Used by the legacy `app.main` CLI only; the DB worker reads encrypted creds from Postgres. |

In Dokploy, ensure **`postgres_data`** and **`redis_data`** are mapped to
persistent storage. `postgres_data` is the backup target — the entire app state
(users, Google tokens, encrypted Deepgram keys, Drive settings, jobs,
transcripts) lives there.

If you run local transcription, the read-only `./models` bind mount must contain
the faster-whisper cache or the whisper.cpp `.bin`; in Dokploy provide it as a
mounted path or bake the models into a custom image.

## 6. First-run checklist

1. **Deploy** the Compose project. Wait for `postgres` and `redis` to report
   healthy; `migrate` then runs `alembic upgrade head` automatically and exits,
   and `web` + `worker` start after it succeeds. No manual migration is needed —
   you can still re-apply with
   `docker compose run --rm web alembic upgrade head`.
2. **Verify health.** Open `https://YOUR_DOMAIN/health` (the web `/health`
   route) and confirm it responds.
3. **Sign in** at `https://YOUR_DOMAIN/login` with `ADMIN_USERNAME` /
   `ADMIN_PASSWORD`.
4. **Connect Google.** Use **Connect Google** and complete OAuth; confirm the
   redirect lands back on your domain.
5. **Deepgram key** (only if local transcription is **disabled** or invalid). In
   **Settings → Deepgram**, save your personal Deepgram API key and use the test
   action (`/settings/deepgram/test`) to confirm it.
6. **Drive folders.** In **Settings → Drive**, paste the source (and optional
   destination) Drive folder links or ids. A destination is required only if you
   enable the optional TXT backup copy (`save_copy_to_drive`).
7. **Check the dashboard cards.** The dashboard shows status for Google, Drive
   source, Deepgram, Transcription (local model status), and **Queue** (Redis
   online/offline/poll). With `QUEUE_BACKEND=redis`, Queue should read online.
   If local transcription is enabled and valid, the Transcription card shows
   `Modelo local ativo: <engine model compute/quant>`; if invalid it shows
   `Modelo local inválido. Consulte a documentação de modelos locais.`
8. **Run a job.** Trigger **Jobs → Run once**. The web only validates and
   creates a `pending` job (and enqueues its id) — it never transcribes in the
   request. The `worker` dequeues, acquires the global lock, claims the job
   (`pending → processing` in Postgres), downloads the MP4, transcribes,
   optionally uploads the TXT to Drive, and marks it completed.
9. **Download the transcript.** Open the job (`/jobs/{id}`) and use **Download
   TXT** once it completes.

## Provider rules (what the UI/worker enforce)

These come from `app/transcription/provider.py` and
`app/transcription/factory.py`:

- `LOCAL_TRANSCRIPTION_ENABLED=false` → **Deepgram**; a per-user Deepgram key is
  required.
- Enabled **and valid** → the **local engine** is used; no Deepgram key is
  required. The UI shows `Modelo local ativo: <engine model compute/quant>`.
- Enabled **but invalid** → Deepgram is required again; the UI shows
  `Modelo local inválido. Consulte a documentação de modelos locais.` with a
  link to `LOCAL_TRANSCRIPTION_DOC_URL`, and **Run once** is blocked until a
  Deepgram key is set. There is **no silent fallback**.

## Operations

### Re-apply migrations manually

```bash
docker compose run --rm web alembic upgrade head
```

### Inspect the queue (Redis is internal)

The Redis keys are `transcription:queue` (FIFO list), `transcription:queued`
(dedupe set), and `transcription:global_lock` (lock token). At startup and while
idle the worker re-enqueues all Postgres `pending` jobs, so a flushed Redis
self-heals from Postgres.

```bash
docker compose exec redis redis-cli LLEN transcription:queue
docker compose exec redis redis-cli GET transcription:global_lock
```

### Back up the database

```bash
docker compose exec -T postgres \
  pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" > backup.sql
```

Always back up `postgres_data` (or `pg_dump`); it holds every user, token,
encrypted Deepgram key, Drive setting, job, and transcript.

## Legacy CLI worker (compatibility only)

The original env-driven worker is kept for compatibility and is **not** the
Compose `worker` service. It reads the global `DEEPGRAM_API_KEY`, a mounted
`token.json` or service account from `./secrets`, and stores state in
`data/processed_files.json`. Run it on demand by overriding the command:

```bash
docker compose run --rm worker python -m app.main --watch
```

See [Legacy CLI](01-architecture.md) and the README "Legacy Simple Worker Mode"
section for its environment variables (`GOOGLE_AUTH_MODE`, `GOOGLE_OAUTH_*`,
`SOURCE_DRIVE_FOLDER_ID`, `DESTINATION_DRIVE_FOLDER_ID`, `STATE_FILE`, etc.).
