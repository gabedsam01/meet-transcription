# Troubleshooting

This is an operational troubleshooting catalog for **meet-transcription**. Each
entry follows the same shape:

- **Symptom** — what you observe (a crash, a UI message, a stuck job).
- **Likely cause** — the configuration or runtime condition behind it.
- **Fix** — the concrete steps to resolve it.
- **Where to look** — the logs, routes, or files that confirm the diagnosis.

The stack is five Docker Compose services (`postgres`, `redis`, `migrate`,
`web`, `worker`). See [Architecture](01-architecture.md) for the full picture,
[Configuration](03-environment-variables.md) for every environment variable, and
[Local Transcription](06-local-transcription.md) for the local-engine setup.

## How to read the logs first

Before diving into a specific symptom, learn where each service speaks:

```bash
# All services, follow mode
docker compose logs -f

# One service at a time (the usual starting points)
docker compose logs -f web
docker compose logs -f worker
docker compose logs migrate          # one-shot; check its exit
docker compose logs postgres
docker compose logs redis

# Service state and exit codes
docker compose ps
```

Two design facts shape what you will see:

1. **The worker never prints a traceback as the job error.** Every domain
   failure is an `AppError` (`app/errors.py`) carrying a friendly, secret-free
   `user_message`. The worker stores that `user_message` as the job's
   `error_message`, and logs the full traceback separately via
   `LOGGER.exception("Transcription failed: job_id=%s ...")`
   (`app/worker/processor.py`). So the UI shows the friendly line; the
   **stack trace lives only in `docker compose logs worker`**.
2. **Secrets are never logged.** Tokens and Deepgram keys are encrypted at rest
   and stripped from messages, so do not expect to find a key value in any log.

---

## Startup and configuration failures

These typically prevent `web` and/or `worker` from booting at all. The required
web variables are validated in `WebSettings.from_env` (`app/web/config.py`),
which raises `ValueError: Missing required environment variable: <KEY>` for any
empty/absent value.

### Missing `GOOGLE_WEB_CLIENT_ID`

| | |
|---|---|
| **Symptom** | The `web` container exits immediately on startup. Logs show `ValueError: Missing required environment variable: GOOGLE_WEB_CLIENT_ID`. `docker compose ps` shows `web` as `Exited`. |
| **Likely cause** | `GOOGLE_WEB_CLIENT_ID` is unset or blank in `.env`. It is a `_required` field in `WebSettings.from_env`. |
| **Fix** | Set `GOOGLE_WEB_CLIENT_ID` (and its partner `GOOGLE_WEB_CLIENT_SECRET` and `GOOGLE_REDIRECT_URI`) in `.env` to the OAuth **Web application** client credentials from Google Cloud Console. Then `docker compose up -d web`. |
| **Where to look** | `docker compose logs web`; the field is defined at `app/web/config.py:31`. |

### Missing `APP_SECRET_KEY`

| | |
|---|---|
| **Symptom** | `web` exits with `ValueError: Missing required environment variable: APP_SECRET_KEY`. If it were ignored, session cookies and Fernet decryption would fail. |
| **Likely cause** | `APP_SECRET_KEY` is unset. It does double duty: it signs the session cookie (`SessionMiddleware`) **and** derives the Fernet key that encrypts Google tokens and Deepgram keys (`fernet_from_secret`, `app/web/security.py`). |
| **Fix** | Generate a strong, stable secret and put it in `.env`:<br>`python -c "import secrets; print(secrets.token_urlsafe(48))"`<br>Use the **same** value for `web` and `worker`. **Never rotate it casually**: changing `APP_SECRET_KEY` makes every previously-encrypted Google token and Deepgram key undecryptable, so users must reconnect Google and re-enter their key. |
| **Where to look** | `docker compose logs web`; `app/web/config.py:29`; encryption in `app/web/security.py`. |

### Invalid or missing `DATABASE_URL`

| | |
|---|---|
| **Symptom** | `web` exits with `Missing required environment variable: DATABASE_URL`, or every DB call raises a connection/driver error, or the **Jobs** page shows a backend error banner instead of jobs. |
| **Likely cause** | `DATABASE_URL` is blank, or it does not use the PostgreSQL psycopg driver. The architecture forbids SQLite — the URL **must** be `postgresql+psycopg://...`. A common mistake is `postgres://...`, `postgresql://...` (no `+psycopg`), or pointing at `localhost` from inside a container instead of the `postgres` service name. |
| **Fix** | Set exactly:<br>`DATABASE_URL=postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}`<br>The host must be the compose service name `postgres`, not `localhost`. Keep `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` consistent with it. |
| **Where to look** | `docker compose logs web` / `worker` / `migrate`; `app/web/config.py:34`. On the **Jobs** page a repository backend failure renders as `backend_error` (`app/web/main.py`, `_resolve_worker_repositories`). |

### `WORKER_REPOSITORY_BACKEND` set to an unknown value

| | |
|---|---|
| **Symptom** | The **Jobs**/**Dashboard** pages list no jobs and show a backend error message; or the worker logs a `RepositoryBackendError`. |
| **Likely cause** | `WORKER_REPOSITORY_BACKEND` is something other than `postgres` (or `memory`, which is dev/tests only). `build_repositories` raises `RepositoryBackendError`, which the web layer catches and surfaces as a friendly banner rather than a 500. |
| **Fix** | Set `WORKER_REPOSITORY_BACKEND=postgres` for any real deployment. Use `memory` only for local dev/tests where data need not survive a restart. |
| **Where to look** | `_resolve_worker_repositories` in `app/web/main.py`; `app/repositories/__init__.py` (`RepositoryBackendError`). |

---

## Database, queue, and migration failures

### Postgres unavailable

| | |
|---|---|
| **Symptom** | `web` and `worker` never start (they `depends_on: postgres: condition: service_healthy`). Or they were running and now every request/DB call raises a connection error. `docker compose ps` shows `postgres` as `unhealthy` or `Exited`. |
| **Likely cause** | The `postgres` container failed its `pg_isready` healthcheck (bad credentials env, corrupted `postgres_data` volume, port conflict, or OOM kill), so dependents are gated. |
| **Fix** | 1) `docker compose ps` and `docker compose logs postgres`. 2) Confirm `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB` match what `DATABASE_URL` uses. 3) Wait for the healthcheck to pass: `docker compose up -d postgres` then watch `docker compose ps`. 4) If the data volume is corrupt and the data is disposable, `docker compose down` and remove the `postgres_data` volume, then re-up (the `migrate` service rebuilds the schema). **Backup = the `postgres_data` volume** — there is no other source of truth. |
| **Where to look** | `docker compose logs postgres`; healthcheck `pg_isready` in `docker-compose.yml`. |

### Redis unavailable

| | |
|---|---|
| **Symptom** | The Dashboard **Queue** card shows offline (red), and the **Jobs** page warns the queue is unavailable. `Run once` still succeeds but flashes: *"Fila indisponível no momento: a transcrição foi registrada e será processada assim que a fila voltar."* The job stays `pending`. |
| **Likely cause** | The `redis` container is down/unhealthy (failed `redis-cli ping`), or `REDIS_URL` is wrong. With `QUEUE_BACKEND=redis`, enqueue is best-effort. |
| **Fix** | Postgres is the source of truth, so **no job is lost**. Bring Redis back: `docker compose up -d redis`, confirm health with `docker compose ps`. On startup and while idle the worker calls `requeue_pending_jobs`, which re-enqueues all Postgres `pending` jobs — so once Redis returns, queued work drains automatically. Verify `REDIS_URL=redis://redis:6379/0` (host = service name `redis`). |
| **Where to look** | `docker compose logs redis`; the queue health probe `_queue_status` in `app/web/main.py`; enqueue best-effort branch in `run_once`; `app/queue/`. |

### `migrate` service failed (migration failed)

| | |
|---|---|
| **Symptom** | The one-shot `migrate` container exits non-zero. `web` and `worker` never start because they `depends_on: migrate: condition: service_completed_successfully`. Logs from `migrate` show an Alembic error. |
| **Likely cause** | `alembic upgrade head` could not run: Postgres not reachable yet, bad `DATABASE_URL`, a partially-applied/dirty schema, or a hand-edited database that diverged from `alembic/versions/0001_create_initial_postgres_schema.py`. |
| **Fix** | 1) Read the error: `docker compose logs migrate`. 2) Ensure Postgres is healthy first (`docker compose up -d postgres`, wait). 3) Re-run just the migration: `docker compose run --rm migrate`. 4) For a fresh dev DB, drop the `postgres_data` volume and let `migrate` create the schema cleanly. 5) To inspect state, run Alembic against the DB: `docker compose run --rm migrate alembic current` / `alembic history`. |
| **Where to look** | `docker compose logs migrate`; migration file `alembic/versions/0001_create_initial_postgres_schema.py`; tables defined in `app/database/models.py`. |

---

## Google OAuth failures

The web app drives a standard Authorization Code flow: `/connect-google` builds
the consent URL (scope `https://www.googleapis.com/auth/drive`), Google
redirects back to `GOOGLE_REDIRECT_URI`, and `/oauth/google/callback` exchanges
the code via `exchange_google_code` (`app/web/main.py`).

### `redirect_uri_mismatch`

| | |
|---|---|
| **Symptom** | After clicking **Connect Google**, Google shows an error page: *"Error 400: redirect_uri_mismatch"*. You never reach `/oauth/google/callback`. |
| **Likely cause** | The `redirect_uri` sent in the consent request does not **exactly** match an Authorized redirect URI registered for that OAuth client in Google Cloud Console. The app sends `GOOGLE_REDIRECT_URI` verbatim (see `connect_google`). Mismatches are usually scheme (`http` vs `https`), trailing slash, port, or domain. |
| **Fix** | Make `GOOGLE_REDIRECT_URI` and the Cloud Console entry **byte-for-byte identical**, e.g.:<br>`GOOGLE_REDIRECT_URI=http://localhost:8000/oauth/google/callback` (local)<br>`GOOGLE_REDIRECT_URI=https://YOUR_DOMAIN/oauth/google/callback` (prod)<br>Add that exact string under *APIs & Services → Credentials → your OAuth client → Authorized redirect URIs*, save, then retry **Connect Google**. The path is always `/oauth/google/callback`. |
| **Where to look** | The error is on Google's page (not in app logs). The value sent is `web_settings.google_redirect_uri` in `connect_google` (`app/web/main.py`). |

### `invalid_client` / token exchange fails on callback

| | |
|---|---|
| **Symptom** | The callback returns a 4xx/5xx; `web` logs an exception from `exchange_google_code` (a `requests` `raise_for_status`). Google may report `invalid_client` or `invalid_grant`. |
| **Likely cause** | Wrong `GOOGLE_WEB_CLIENT_SECRET` (or client id), the OAuth client is a "Desktop" type instead of "Web application", or the authorization code expired/was reused. |
| **Fix** | Confirm `GOOGLE_WEB_CLIENT_ID` and `GOOGLE_WEB_CLIENT_SECRET` are from the same **Web application** client and match the Cloud Console. Restart the flow from `/connect-google` (codes are single-use and short-lived). |
| **Where to look** | `docker compose logs web` around the POST to `https://oauth2.googleapis.com/token` (`exchange_google_code`, `app/web/main.py`). |

### "Invalid OAuth state"

| | |
|---|---|
| **Symptom** | The callback returns `400 Invalid OAuth state`. |
| **Likely cause** | The session lost the `oauth_state` it set in `/connect-google` (cookie blocked, different browser/tab, or `SESSION_COOKIE_SECURE=true` while serving over plain `http`, so the cookie was never stored). |
| **Fix** | Start the flow fresh in the same browser. Over plain HTTP (local dev) keep `SESSION_COOKIE_SECURE=false`; set it `true` only behind HTTPS. |
| **Where to look** | `oauth_callback` state check in `app/web/main.py`; `SessionMiddleware` config (`https_only=web_settings.session_cookie_secure`). |

---

## Transcription provider failures

The provider is chosen by the product rule in
`app/transcription/factory.py:resolve_provider` and surfaced to the UI by
`get_transcription_provider_status` (`app/transcription/provider.py`):

- `LOCAL_TRANSCRIPTION_ENABLED=false` → **Deepgram**, per-user key required.
- enabled **and** valid → **local engine**; no Deepgram key required. The UI
  shows *"Modelo local ativo: <engine model compute/quant>"*.
- enabled **but** invalid → Deepgram required again; the UI shows
  *"Modelo local inválido. Consulte a documentação de modelos locais."* with a
  link to `LOCAL_TRANSCRIPTION_DOC_URL`, and `Run once` is blocked unless a
  Deepgram key is set. **There is no silent fallback.**

### Deepgram key missing

| | |
|---|---|
| **Symptom** | On the local-disabled (or local-invalid) path, `Run once` flashes *"Configure sua Deepgram API Key antes de iniciar uma transcrição."* and no job is created. The Dashboard **Deepgram** card shows not configured. |
| **Likely cause** | The signed-in user has no Deepgram key saved, while a key is required (`deepgram_required=True` from the transcription status). Keys are **per-user and encrypted** — there is no global `DEEPGRAM_API_KEY` fallback in the web/worker deployment. |
| **Fix** | Go to **Settings → Deepgram**, paste the key, save. Optionally click **Testar** (`/settings/deepgram/test`) — a valid key flashes *"Deepgram API Key válida."* Then retry `Run once`. (Alternatively, enable a valid local engine to drop the requirement entirely.) |
| **Where to look** | `RUN_ONCE_MESSAGES["no_deepgram_key"]` and `run_once` gating in `app/web/main.py`; `DeepgramKeyRequiredError` in `app/errors.py`. |

### Deepgram key invalid

| | |
|---|---|
| **Symptom** | **Settings → Deepgram → Testar** flashes *"Deepgram API Key inválida."* (or *"Não foi possível verificar agora."*). A started job may fail with the friendly transcription error. |
| **Likely cause** | The key is wrong, revoked, or out of credit/quota; or Deepgram is unreachable from the container at test time (`unverifiable`). |
| **Fix** | Re-copy the key from the Deepgram console (no stray whitespace), save, and **Testar** again until it shows *"Deepgram API Key válida."* If it shows *"Não foi possível verificar agora,"* check the container's outbound network/DNS and retry. |
| **Where to look** | `verify_deepgram_key` / `DEEPGRAM_TEST_MESSAGES` in `app/web/main.py` and `app/web/deepgram_key.py`; for a failed job, the friendly `error_message` on the job + the traceback in `docker compose logs worker`. |

### Local model invalid (engine/model/compute/quant rejected)

| | |
|---|---|
| **Symptom** | The Dashboard **Transcription** card shows *"Modelo local inválido. Consulte a documentação de modelos locais."* with a doc link. `Run once` is blocked unless a Deepgram key is set. |
| **Likely cause** | `validate_local_config` (`app/transcription/local_validation.py`) rejected the config. Reasons it returns (Portuguese, surfaced in the UI/logs): unsupported engine (`Engine de transcrição local não suportado`), unsupported model (`Modelo local não suportado`), unsupported `compute_type` for CPU (faster-whisper), or unsupported `quantization` (whisper.cpp). |
| **Fix** | Use only allowed values:<br>• `LOCAL_TRANSCRIPTION_ENGINE` ∈ `faster-whisper`, `whisper-cpp`.<br>• `LOCAL_TRANSCRIPTION_MODEL` ∈ `tiny, base, small, medium, large-v1, large-v2, large-v3, large-v3-turbo` (**multilingual only — never `.en` models**; the app needs pt-BR + English).<br>• faster-whisper `LOCAL_TRANSCRIPTION_COMPUTE_TYPE` ∈ `int8, int8_float32, float32`.<br>• whisper.cpp `LOCAL_TRANSCRIPTION_QUANTIZATION` ∈ `q4_0, q4_1, q5_0, q5_1, q8_0`.<br>Restart `web` and `worker` after changing env (status is computed from env at startup). |
| **Where to look** | `app/transcription/local_validation.py` (`_invalid(...)` reasons); the card text comes from `get_transcription_provider_status`. See [Local Transcription](06-local-transcription.md). |

### whisper.cpp binary not found

| | |
|---|---|
| **Symptom** | Local config validates as invalid with reason *"Binário whisper.cpp ausente ou não executável (WHISPER_CPP_BINARY)."* A failed job's `error_message` is *"Binário whisper.cpp não encontrado. Verifique WHISPER_CPP_BINARY."* |
| **Likely cause** | `WHISPER_CPP_BINARY` is unset, points to a path that does not exist inside the container, or the file is not executable. The `whisper-cli` binary is **external** — it is **not** compiled into the image (even with `INSTALL_WHISPER_CPP`, which only apt-installs `ffmpeg`). |
| **Fix** | Provide the `whisper-cli` binary to the container (mount it, e.g. under `/models`, or bake it in) and point `WHISPER_CPP_BINARY` at it, e.g. `WHISPER_CPP_BINARY=/models/whisper-cli`. Ensure it is executable (`chmod +x`). Restart `worker`/`web`. |
| **Where to look** | `_validate_whisper_cpp` in `app/transcription/local_validation.py`; `WhisperCppBinaryNotFoundError` in `app/errors.py`; provider `app/transcription/whisper_cpp_provider.py`. |

### whisper.cpp model path not found

| | |
|---|---|
| **Symptom** | Local config invalid with reason *"Arquivo de modelo whisper.cpp ausente (LOCAL_TRANSCRIPTION_MODEL_PATH)."* A failed job's `error_message` is *"Arquivo de modelo local não encontrado. Verifique LOCAL_TRANSCRIPTION_MODEL_PATH."* |
| **Likely cause** | `LOCAL_TRANSCRIPTION_MODEL_PATH` is unset or the ggml file does not exist at that path. For whisper.cpp the model path is **ALWAYS required** — whisper.cpp cannot fetch a ggml model itself; `LOCAL_TRANSCRIPTION_AUTO_DOWNLOAD` applies only to faster-whisper. |
| **Fix** | Download the ggml model and place it under the mounted models directory (`./models:/models:ro`), then set `LOCAL_TRANSCRIPTION_MODEL_PATH=/models/ggml-small.bin` (matching your chosen model + quantization). Confirm the file is present **inside** the container: `docker compose run --rm worker ls -l /models`. Restart `worker`/`web`. |
| **Where to look** | `_validate_whisper_cpp` model-path check in `app/transcription/local_validation.py`; `ModelNotFoundError` in `app/errors.py`. |

### faster-whisper not installed

| | |
|---|---|
| **Symptom** | Local config invalid with reason *"O pacote faster-whisper não está instalado nesta imagem (INSTALL_FASTER_WHISPER=true)."* |
| **Likely cause** | `LOCAL_TRANSCRIPTION_ENGINE=faster-whisper` but the `faster_whisper` Python package is absent from the image. It is gated behind the **build arg** `INSTALL_FASTER_WHISPER` (default `false`), which is a Docker build-time arg — **not** a runtime env var. |
| **Fix** | Rebuild the image with the package installed:<br>`docker compose build --build-arg INSTALL_LOCAL_TRANSCRIPTION=true --build-arg INSTALL_FASTER_WHISPER=true`<br>then `docker compose up -d`. Setting it only in `.env` at runtime does nothing — it must be passed at build time. |
| **Where to look** | `_validate_faster_whisper` module check in `app/transcription/local_validation.py` (`module_available("faster_whisper")`); build args in `Dockerfile`; provider `app/transcription/faster_whisper_provider.py`. |

---

## Job and content failures (per-job `error_message`)

When a job fails, it always reaches a terminal `failed` state with a friendly
`error_message`; the traceback is in `docker compose logs worker`. See the
worker `process()` flow in `app/worker/processor.py`.

### Video without audio (or audio extraction yields nothing)

| | |
|---|---|
| **Symptom** | A job goes to `failed`; its `error_message` is a transcription error (e.g. *"Não foi possível transcrever a reunião."*) or an empty/blank transcript. For local engines, ffmpeg audio extraction may fail. |
| **Likely cause** | The MP4 has no audio track (a screen-only Meet recording, or a corrupted/partial Drive file). Deepgram returns no speech; whisper.cpp's ffmpeg step (`extract 16kHz mono WAV`) produces no usable audio. |
| **Fix** | Verify the source recording actually contains audio before re-running. With ffprobe on a copy:<br>`ffprobe -i recording.mp4 -show_streams -select_streams a` (expect at least one audio stream). Re-record/re-upload a recording that includes audio, then `Run once` again. |
| **Where to look** | `docker compose logs worker` for the ffmpeg/provider traceback; `app/transcription/audio.py` (ffmpeg command builder), `app/transcription/whisper_cpp_provider.py`, `app/transcription/deepgram_provider.py`. |

### File too large

| | |
|---|---|
| **Symptom** | A job fails during download or transcription; the worker log shows a Drive download error, a provider size/timeout rejection, or the container is OOM-killed on a large local model run. |
| **Likely cause** | A very long recording: the download fills the per-job scratch dir, Deepgram rejects/times out an oversized payload, or a large local model exhausts container memory on CPU. |
| **Fix** | Ensure adequate disk on the host backing `TMP_DIR` and the `./tmp` mount (scratch lives at `TMP_DIR/jobs/<job_id>/`). For local engines, prefer a smaller model (`small`/`base`) or a smaller faster-whisper `compute_type` (`int8`) and raise `LOCAL_TRANSCRIPTION_THREADS` cautiously; give the container more memory. For Deepgram, split/shorten very long recordings. Re-run after adjusting. |
| **Where to look** | `docker compose logs worker` (download + transcribe steps); scratch path built in `JobProcessor.process` (`Path(tmp_dir)/"jobs"/str(job.id)`), always cleaned up in `_cleanup_job_dir`. |

### Job missing its source file / Drive folder not set

| | |
|---|---|
| **Symptom** | A job fails fast with `error_message` *"Configure a pasta de origem no Drive antes de transcrever."* or it never gets a file id. |
| **Likely cause** | The worker's preconditions failed in `process()`: no user `settings` (raises `DriveFolderMissingError`), no Google `token` (`GoogleTokenMissingError`), or no `job.source_file_id`. |
| **Fix** | In the UI: connect Google (**Connect Google**) and set the source folder (**Settings → Drive**) for that user, then `Run once` again. `Run once` itself also pre-checks these and flashes `no_settings`/`not_connected` rather than creating a doomed job. |
| **Where to look** | Precondition checks at the top of `JobProcessor.process` (`app/worker/processor.py`); `DriveFolderMissingError` / `GoogleTokenMissingError` in `app/errors.py`. |

---

## Worker, queue, and lifecycle problems

### Job stuck in `processing`

| | |
|---|---|
| **Symptom** | A job sits in `processing` indefinitely and never reaches `completed`/`failed`. |
| **Likely cause** | The worker crashed or was killed mid-job (so it could not call `mark_completed`/`mark_failed`), or the global lock/long transcription is still genuinely running. The DB row keeps `status=processing`, `started_at` set. |
| **Fix** | The worker self-heals: on startup `run()` calls `recover_stale_jobs` (which uses `reset_stale_processing_jobs`) to mark `processing` jobs older than `STALE_JOB_TIMEOUT_MINUTES` as **`failed`** (with a stale-timeout `error_message`) — they are **not** reset to `pending` and do **not** re-enter the queue automatically. To recover now, restart the worker (`docker compose restart worker`) to fail the stuck job, then trigger **Run once** again: dedupe only blocks `pending`/`processing`/`completed`, so a failed source file can be re-run. Tune `STALE_JOB_TIMEOUT_MINUTES`, and confirm it is not simply a slow large-model run still in progress. |
| **Where to look** | `docker compose logs worker` (look for stale-recovery and `Transcription started/completed` lines); `recover_stale_jobs` in `app/worker/main.py`; `reset_stale_processing_jobs` in the repository contract (`app/core/ports.py`, `app/repositories/postgres.py`). |

### Worker not consuming the queue

| | |
|---|---|
| **Symptom** | Jobs pile up as `pending`; the Dashboard shows growing totals but nothing transitions to `processing`/`completed`. |
| **Likely cause** | The `worker` container is down/restarting; or `QUEUE_BACKEND` mismatch (web enqueues to Redis but worker runs the poll loop, or vice-versa); or Redis is down so nothing is being dequeued (web/worker still reconcile from Postgres, but only if the worker is up); or `WORKER_CONCURRENCY` is `0`/misconfigured. |
| **Fix** | 1) `docker compose ps` — make sure `worker` is `Up`, not crash-looping; read `docker compose logs worker`. 2) Keep `QUEUE_BACKEND` consistent across `web` and `worker` (compose default is `redis`). With `redis`, `run()` runs `run_queue_loop` threads and also `requeue_pending_jobs` on startup/idle; with `none` it runs the legacy poll loop (`claim_next_pending_job`). 3) Ensure Redis is healthy. 4) `WORKER_CONCURRENCY` ≥ 1 (default 1). 5) `docker compose restart worker` to force a startup reconcile. |
| **Where to look** | `docker compose logs worker`; `main.run()` / `run_queue_loop` / `run_worker_loop` in `app/worker/main.py`; queue internals in `app/queue/` (keys `transcription:queue`, `transcription:queued`, `transcription:global_lock`). |

### Two workers, but only one job processes at a time

| | |
|---|---|
| **Symptom** | With concurrency/replicas, throughput is serialized — jobs run one at a time globally. |
| **Likely cause** | This is **by design**. A single global lock (`transcription:global_lock`, `SET NX EX` with a token, TTL `TRANSCRIPTION_GLOBAL_LOCK_TTL_SECONDS`, default 14400) serializes transcription across the whole system; `claim_job` then does the atomic `pending→processing` flip in Postgres as the final dedupe defense. |
| **Fix** | Expected behavior — no fix needed. The lock prevents duplicate/competing transcriptions. If a worker died holding the lock, it auto-expires after the TTL. |
| **Where to look** | `app/queue/` lock handling; `TRANSCRIPTION_GLOBAL_LOCK_TTL_SECONDS`; `claim_job` in `app/repositories/postgres.py`. |

### An id seems "lost" in the dedupe set

| | |
|---|---|
| **Symptom** | A `pending` job's id is in `transcription:queued` but never appears in `transcription:queue`, so it is never dequeued. |
| **Likely cause** | A crash between adding to the dedupe set and pushing onto the list left the id orphaned. |
| **Fix** | Self-healing: `ensure_queued` (which uses Redis `LPOS`) detects an id present in the dedupe set but absent from the list and re-pushes it; `requeue_pending_jobs` at worker startup/idle also re-enqueues all Postgres `pending` jobs. Restart the worker if you want the reconcile to run immediately. |
| **Where to look** | `ensure_queued` / `requeue_pending_jobs` in `app/queue/`; worker startup in `app/worker/main.py`. |

---

## Download / output problems

### "Download TXT" not appearing / download fails

| | |
|---|---|
| **Symptom** | The **Download TXT** button is missing on a job, or `GET /jobs/{id}/download` returns 404/409/503. |
| **Likely cause** | Maps to `DownloadError` codes in `get_downloadable_transcript`: the job is not `completed` (`409 not_completed`), it is completed but has no stored transcript (`404 no_transcript`), the id is unknown **or belongs to another user** (`404 not_found` — ownership is strict, even for admins), or the worker repository is unavailable (`503`). |
| **Fix** | • Button only shows when `status=completed` — wait for or re-run the job. • If completed without a transcript, the transcribe step produced no text (see *video without audio*); re-run on a recording with audio. • A 404 on another user's job is intentional isolation — sign in as the owner. • A 503 means the worker repository/DB is down — fix `DATABASE_URL`/Postgres (see above). |
| **Where to look** | `download_transcript` route + status mapping in `app/web/main.py`; `app/services/download_service.py` (`get_downloadable_transcript`, `DownloadError`). The served text is `transcripts.transcript_text`. |

### Transcript completed but no copy in the destination Drive folder

| | |
|---|---|
| **Symptom** | The job is `completed` and **Download TXT** works, but no `..._Transcricao.txt` appears in the destination Drive folder. |
| **Likely cause** | The Drive backup upload is conditional: it happens **only if** `save_copy_to_drive` is enabled **and** a destination folder is set. With either missing, the transcript is still saved to Postgres (and downloadable) but not uploaded to Drive. |
| **Fix** | In **Settings → Drive**, set a valid **destination** folder URL and tick **save copy to Drive**, then run a new job. (Editing settings does not retro-upload past jobs.) |
| **Where to look** | The `if settings.save_copy_to_drive and settings.destination_drive_folder_id:` branch in `JobProcessor.process` (`app/worker/processor.py`); `user_drive_settings` table in `app/database/models.py`. |

---

## Quick triage checklist

```bash
# 1. Are all five services up and healthy?
docker compose ps

# 2. Did the schema migration succeed?
docker compose logs migrate

# 3. Are required env vars present? (web fails fast if not)
docker compose config        # needs a local .env: cp .env.example .env

# 4. Where did the last failure happen?
docker compose logs --tail=200 web
docker compose logs --tail=200 worker

# 5. Force a startup reconcile (re-enqueue pendings, recover stale processing)
docker compose restart worker
```

If the app still misbehaves, run the test suite locally to confirm the code path
is healthy, then re-check configuration:

```bash
python -m pytest -v
python -m compileall app scripts
```

## Related documentation

- [Architecture](01-architecture.md)
- [Configuration](03-environment-variables.md)
- [Local Transcription](06-local-transcription.md)
- [Queue and Worker](09-redis-queue.md)
