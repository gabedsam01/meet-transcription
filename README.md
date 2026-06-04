# Meet Transcription

Meet Transcription watches a Google Drive folder for **Google Meet recordings**,
transcribes each MP4 — with **Deepgram** or a **local CPU engine**
(faster‑whisper / whisper.cpp) — saves the transcript in **PostgreSQL**, and serves
a **Download TXT** from a server‑rendered web UI. It ships as a small set of
Docker services and is built to run on an ordinary VPS, x86_64 or ARM64, with no
GPU.

> 📚 Full, operational documentation lives in **[`documentation/`](documentation/)**
> (overview, architecture, installation, every env var, OAuth, Deepgram, local
> transcription, Redis, Postgres, worker flow, web UI, Dokploy, GHCR,
> troubleshooting, security, development, testing, roadmap).

---

## 1. Overview

**What it is.** A multi‑user web app + background worker that turns meeting
recordings into readable transcripts.

**Main flow:**

```
Google Meet records → file lands in a Google Drive folder
        → the app detects the new video (Run once)
        → the worker downloads it and transcribes (Deepgram or local engine)
        → the transcript is saved in PostgreSQL
        → the user downloads the TXT from the web UI
```

Google Drive is the **input** (the source folder the worker reads) and an
**optional backup** (a TXT copy can be uploaded to a destination folder). The
**primary** way to get a transcript is the **Download TXT** button in the UI.

---

## 2. Final architecture

The deployment is **five services** (see `docker-compose.yml`), with **PostgreSQL
as the single source of truth — there is no SQLite mode**, and **Redis as the
queue/lock** (not the main database).

```
                ┌──────────┐         ┌──────────┐
   browser  ──▶ │   web    │         │  worker  │ ──▶ Google Drive
                │ (FastAPI)│         │ (jobs)   │ ──▶ Deepgram / local engine
                └────┬─────┘         └────┬─────┘
                     │   enqueue job_id   │  dequeue + global lock
                     ▼                    ▼
                  ┌──────┐   pending    ┌──────────┐
                  │redis │◀────────────▶│ postgres │  ◀── source of truth
                  └──────┘   jobs       └──────────┘
```

**Startup order:** `postgres` becomes healthy → `redis` becomes healthy →
`migrate` runs `alembic upgrade head` and exits → `web` and `worker` start.

| Service | Command | Responsibility |
|---|---|---|
| **postgres** | `postgres:16` | Single source of truth: users, settings, encrypted tokens/keys, jobs, transcripts. Internal only. |
| **redis** | `redis:7-alpine` | Transcription **queue** + **global execution lock**. Internal only. |
| **migrate** | `alembic upgrade head` | One‑shot schema migration; exits 0. Web/worker wait for it. |
| **web** | `uvicorn app.web.main:app --host 0.0.0.0 --port 8000` | UI + OAuth. **Validates and enqueues only — never transcribes in a request.** |
| **worker** | `python -m app.worker.main` | Claims jobs, downloads, transcribes, saves transcripts, optional Drive upload. |

Key principles:

- **Postgres is the truth.** Redis can be wiped at any time; the worker
  re‑enqueues pending jobs from Postgres on startup and while idle.
- **The web UI does no heavy work.** Run once only creates a `pending` job and
  enqueues its id.
- **The worker processes one transcription at a time** behind a Redis global lock,
  so concurrent “Run once” clicks never start two CPU transcriptions at once.
- **Deepgram and local transcription are interchangeable providers** behind one
  interface; the chosen provider is decided per the rule in §11.

web and worker run from the **same image** with different commands.

---

## 3. Transcription providers

### Deepgram

- Requires a **per‑user API key** (Settings → Deepgram).
- The key is stored **encrypted at rest** (Fernet, key derived from
  `APP_SECRET_KEY`) and never shown again.
- **Best for diarization / speaker labels** and is **fast**.
- Has a **per‑use cost** and sends audio to an external service.

### Local transcription

- **No per‑use cost**, **CPU‑only**, runs on your own infra (private).
- **Slower** than Deepgram and has **no diarization** in the MVP (`speaker = null`).
- Handles **pt‑BR and English** using **multilingual** models — **do not use `.en`
  models** (they are English‑only).

### faster‑whisper

- Python engine (CTranslate2). CPU, `compute_type=int8` recommended.
- Models: `tiny`, `base`, `small`, `medium`, `large-v1`, `large-v2`, `large-v3`,
  `large-v3-turbo`.

### whisper.cpp

- Native engine, good for weak VPSs. Driven via the **`whisper-cli`** binary.
- Requires **`WHISPER_CPP_BINARY`** and **`LOCAL_TRANSCRIPTION_MODEL_PATH`** (it
  cannot auto‑download a model) and extracts a **16 kHz mono WAV** with ffmpeg.
- Quantizations: `q4_0`, `q4_1`, `q5_0`, `q5_1`, `q8_0`.

See **[documentation/06-local-transcription.md](documentation/06-local-transcription.md)**,
**[07-faster-whisper.md](documentation/07-faster-whisper.md)**, and
**[08-whisper-cpp.md](documentation/08-whisper-cpp.md)**.

---

## 4. VPS requirements

| Tier | Hardware | faster‑whisper | whisper.cpp |
|---|---|---|---|
| **Minimum** | 4 GB RAM, 1–2 vCPU | `base`/`small` `int8` | `base`/`small` `q4_0` |
| **Recommended** | 8 GB RAM, 4 vCPU | `small`/`medium` `int8` | `small`/`medium` `q5_0` |
| **Comfortable** | 16–24 GB RAM, 4+ vCPU | `medium`/`large-v3-turbo` `int8` | `medium`/`large` `q5_0`/`q8_0` |

CPU transcription is roughly **~1× realtime or slower** — a 60‑minute meeting can
take an hour or more. Keep `WORKER_CONCURRENCY=1` and raise
`STALE_JOB_TIMEOUT_MINUTES` for long recordings.

---

## 5. Environment variables

Full reference (with risk‑if‑wrong) in
**[documentation/03-environment-variables.md](documentation/03-environment-variables.md)**.
Quick list:

**Database (PostgreSQL — required, single source of truth)**

| Variable | Example |
|---|---|
| `POSTGRES_DB` | `meet_transcription` |
| `POSTGRES_USER` | `meet_user` |
| `POSTGRES_PASSWORD` | `a-strong-db-password` |
| `DATABASE_URL` | `postgresql+psycopg://meet_user:...@postgres:5432/meet_transcription` (never a SQLite path) |

**Redis (queue + lock)**

| Variable | Default |
|---|---|
| `QUEUE_BACKEND` | `redis` (also `memory` for dev, `none` for the legacy poll loop) |
| `REDIS_URL` | `redis://redis:6379/0` |
| `QUEUE_NAME` | `transcription` |
| `TRANSCRIPTION_GLOBAL_LOCK_TTL_SECONDS` | `14400` |

**Web / Admin**

| Variable | Notes |
|---|---|
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | Bootstrap admin login. |
| `APP_SECRET_KEY` | Session signing **and** the encryption key for tokens/keys. Use a long random value. |
| `SESSION_COOKIE_SECURE` | `true` behind HTTPS. |

**Google OAuth**

| Variable | Notes |
|---|---|
| `GOOGLE_WEB_CLIENT_ID` / `GOOGLE_WEB_CLIENT_SECRET` | OAuth **Web application** credentials. |
| `GOOGLE_REDIRECT_URI` | Must exactly equal `https://YOUR_DOMAIN/oauth/google/callback`. |

**Worker**

| Variable | Notes |
|---|---|
| `WORKER_REPOSITORY_BACKEND` | `postgres` (production) or `memory` (dev/tests only). |
| `WORKER_POLL_INTERVAL_SECONDS` | Idle poll interval. |
| `WORKER_CONCURRENCY` | Parallel workers (keep `1` for CPU local transcription). |
| `STALE_JOB_TIMEOUT_MINUTES` | Fail a stuck `processing` job at startup after this. |
| `TMP_DIR` | Scratch dir for downloads/transcripts. |

**Local transcription**

| Variable | Default / values |
|---|---|
| `LOCAL_TRANSCRIPTION_ENABLED` | `false` |
| `LOCAL_TRANSCRIPTION_ENGINE` | `faster-whisper` \| `whisper-cpp` |
| `LOCAL_TRANSCRIPTION_MODEL` | `small` (see model list) |
| `LOCAL_TRANSCRIPTION_LANGUAGE` | `auto` \| `pt` \| `en` \| … |
| `LOCAL_TRANSCRIPTION_THREADS` | `4` |
| `LOCAL_TRANSCRIPTION_MODEL_DIR` | `/models` |
| `LOCAL_TRANSCRIPTION_COMPUTE_TYPE` | `int8` (faster‑whisper) |
| `LOCAL_TRANSCRIPTION_QUANTIZATION` | `q4_0` (whisper.cpp) |
| `LOCAL_TRANSCRIPTION_MODEL_PATH` | `/models/ggml-small-q4_0.bin` (whisper.cpp — required) |
| `WHISPER_CPP_BINARY` | `/usr/local/bin/whisper-cli` (whisper.cpp) |
| `LOCAL_TRANSCRIPTION_AUTO_DOWNLOAD` | `false` (faster‑whisper only) |
| `LOCAL_TRANSCRIPTION_DOC_URL` | link shown in the UI when local is invalid |

**Build args (Docker build time, not runtime)**

| Arg | Effect |
|---|---|
| `INSTALL_LOCAL_TRANSCRIPTION` | install both engines’ deps |
| `INSTALL_FASTER_WHISPER` | `pip install faster-whisper` |
| `INSTALL_WHISPER_CPP` | install `ffmpeg` (the `whisper-cli` binary is external) |

The web/worker deployment uses **per‑user, encrypted Deepgram keys** — it needs
**no global `DEEPGRAM_API_KEY`**. That var is consumed only by the legacy CLI
worker (see §“Legacy Simple Worker Mode”).

---

## 6. Running locally

```bash
git clone https://github.com/gabedsam01/meet-transcription.git
cd meet-transcription
cp .env.example .env
```

Generate a strong `APP_SECRET_KEY` and paste it into `.env`:

```bash
python - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
```

Bring the stack up (builds the image, runs the migration, starts everything):

```bash
docker compose up -d
```

Follow logs per service:

```bash
docker compose logs -f web
docker compose logs -f worker
docker compose logs -f migrate
```

Open `http://localhost:8000` and sign in with `ADMIN_USERNAME` / `ADMIN_PASSWORD`.

> Developing without Docker? Use a virtualenv: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`,
> point `DATABASE_URL` at a local PostgreSQL, run `alembic upgrade head`, then
> `.venv/bin/uvicorn app.web.main:app --reload --port 8000`. For a single‑process
> run with no Redis, set `QUEUE_BACKEND=memory` (or `none` for the poll loop).

---

## 7. Configure Google OAuth

The web app needs OAuth **Web application** credentials (not Desktop):

1. Create/open a Google Cloud project and **enable the Google Drive API**.
2. `APIs & Services` → `Credentials` → **OAuth client ID** → type **Web application**.
3. Add an authorized redirect URI that **exactly** matches `GOOGLE_REDIRECT_URI`:
   - local: `http://localhost:8000/oauth/google/callback`
   - production: `https://YOUR_DOMAIN/oauth/google/callback`
4. Put the client id/secret in `GOOGLE_WEB_CLIENT_ID` / `GOOGLE_WEB_CLIENT_SECRET`.

A mismatch causes `redirect_uri_mismatch`. Details:
**[documentation/04-google-oauth.md](documentation/04-google-oauth.md)**.

---

## 8. Configure Deepgram

1. Sign in to the web UI.
2. Go to **Settings → Deepgram**.
3. Paste your Deepgram API key and save (it is encrypted at rest).
4. Use **Test** to verify the key works.

The key is required to transcribe **unless** a valid local engine is active (§11).
Details: **[documentation/05-deepgram.md](documentation/05-deepgram.md)**.

---

## 9. Configure Drive

1. Go to **Settings → Drive folders**.
2. Paste the **source** folder link (your Meet Recordings folder) or a bare id.
3. The **destination** folder is optional (only used when “save a copy to Drive” is on).

```
https://drive.google.com/drive/folders/1zv32Q...tBD5?usp=sharing
```

The id is extracted automatically.

---

## 10. Configure local transcription

**faster‑whisper (CPU):**

```env
LOCAL_TRANSCRIPTION_ENABLED=true
LOCAL_TRANSCRIPTION_ENGINE=faster-whisper
LOCAL_TRANSCRIPTION_MODEL=small
LOCAL_TRANSCRIPTION_COMPUTE_TYPE=int8
LOCAL_TRANSCRIPTION_LANGUAGE=auto
LOCAL_TRANSCRIPTION_THREADS=4
```

Build the image with the engine baked in (never installed at runtime):

```bash
docker build --build-arg INSTALL_FASTER_WHISPER=true -t meet-transcription:fw .
```

**whisper.cpp (CPU, q4):**

```env
LOCAL_TRANSCRIPTION_ENABLED=true
LOCAL_TRANSCRIPTION_ENGINE=whisper-cpp
LOCAL_TRANSCRIPTION_MODEL=small
LOCAL_TRANSCRIPTION_QUANTIZATION=q4_0
LOCAL_TRANSCRIPTION_MODEL_PATH=/models/ggml-small-q4_0.bin
WHISPER_CPP_BINARY=/usr/local/bin/whisper-cli
LOCAL_TRANSCRIPTION_THREADS=4
```

```bash
docker build --build-arg INSTALL_WHISPER_CPP=true -t meet-transcription:wc .
```

Put model files under the host `./models` directory (mounted at `/models`).

---

## 11. How the provider fallback works

| `LOCAL_TRANSCRIPTION_ENABLED` | Local config | Result |
|---|---|---|
| `false` | — | **Deepgram** required (per‑user key). |
| `true` | **valid** | **Local engine** used; **no** Deepgram key required. |
| `true` | **invalid** | **Deepgram required.** UI shows *“Modelo local inválido. Consulte a documentação de modelos locais.”* with a docs link, and Run once is blocked until a Deepgram key is set. |

There is **no silent fallback**: an invalid local configuration always surfaces a
clear message; if there is also no Deepgram key, run‑once is blocked with a
friendly message instead of failing later.

---

## 12. How Redis works

- The web service **enqueues** `job_id` on **Run once** (`transcription:queue`),
  deduped by a Redis set (`transcription:queued`).
- The worker **dequeues**, takes the **global lock** (`transcription:global_lock`,
  `SET NX EX`), atomically **claims that job** in Postgres
  (`pending → processing`), processes it, then **releases the lock** — one at a
  time, even across replicas.
- **Postgres stays the source of truth.** If Redis is wiped/unavailable, the worker
  **re‑enqueues** all pending jobs on startup and while idle
  (`requeue_pending_jobs`), and a self‑heal (`ensure_queued` via `LPOS`) recovers
  any id orphaned in the dedupe set.

Details: **[documentation/09-redis-queue.md](documentation/09-redis-queue.md)**.

---

## 13. Deploying on Dokploy

Deploy the Compose project, attach your domain to the **web** service only on port
**8000**, keep **Postgres and Redis internal** (no domain), set the environment
variables, persist the `postgres_data` and `redis_data` volumes, and set the
Google redirect URI to `https://YOUR_DOMAIN/oauth/google/callback`. The `migrate`
service applies the schema automatically. Full guide:
**[documentation/13-dokploy-deploy.md](documentation/13-dokploy-deploy.md)** (and
`docs/deploy/dokploy.md`).

---

## 14. GHCR image

On every push to `main` (and the integration branch), GitHub Actions
(`.github/workflows/docker-publish.yml`) runs the tests + `compileall`, builds the
image, and publishes it to the GitHub Container Registry:

```
ghcr.io/gabedsam01/meet-transcription:latest
ghcr.io/gabedsam01/meet-transcription:<short-sha>
```

Use it in production by pulling instead of building (the image is already set in
the `x-app` anchor of `docker-compose.yml`):

```bash
docker compose pull
docker compose up -d
```

Details: **[documentation/14-ghcr.md](documentation/14-ghcr.md)**.

---

## 15. Troubleshooting

A detailed catalog (symptom / cause / fix / where to look) is in
**[documentation/15-troubleshooting.md](documentation/15-troubleshooting.md)**.
The greatest hits:

| Symptom | Likely cause | Fix |
|---|---|---|
| App won’t start, `Missing required environment variable` | `APP_SECRET_KEY` / `GOOGLE_WEB_CLIENT_ID` unset | Set them in `.env`. |
| `redirect_uri_mismatch` on login | `GOOGLE_REDIRECT_URI` ≠ Google Console URI | Make them character‑for‑character identical. |
| Dashboard “Queue: Indisponível” | Redis down/unreachable | Check the `redis` service / `REDIS_URL`; jobs stay pending and reconcile on recovery. |
| Web won’t connect to DB | bad `DATABASE_URL` / Postgres down | Verify the DSN host `postgres:5432` and credentials. |
| “Modelo local inválido” banner | engine/model/quant/binary/path invalid | Follow the docs link; fix config or set a Deepgram key. |
| `whisper.cpp binary não encontrado` | `WHISPER_CPP_BINARY` missing | Mount/install `whisper-cli` and point the var at it. |
| Job stuck in `processing` | worker crashed mid‑job | It is failed at startup after `STALE_JOB_TIMEOUT_MINUTES`. |
| Worker not consuming | `QUEUE_BACKEND` mismatch / Redis down | Ensure web+worker share `QUEUE_BACKEND=redis` and a reachable `REDIS_URL`. |

---

## Legacy Simple Worker Mode

The original env‑driven CLI worker (`python -m app.main`) still works and is kept
for **compatibility only** — it is **not** the Compose `worker` service. It uses a
mounted OAuth `token.json` (or a Service Account), reads settings from `.env`,
stores state in `data/processed_files.json`, needs **no database or web UI**, and
reads the global `DEEPGRAM_API_KEY`.

```bash
python scripts/generate_google_oauth_token.py \
  --client-secrets secrets/oauth-client.json \
  --token-file secrets/token.json

docker compose run --rm worker python -m app.main --once     # process once
docker compose run --rm worker python -m app.main --watch    # poll continuously
docker compose run --rm worker python -m app.main --once --reprocess DRIVE_FILE_ID
```

---

## Security

- **Never commit secrets.** `.env`, `secrets/*.json`, `token.json`, and
  `data/processed_files.json` are git‑ignored.
- Google tokens and per‑user Deepgram keys are **encrypted at rest** (Fernet, key
  from `APP_SECRET_KEY`); secrets are never logged.
- Use `SESSION_COOKIE_SECURE=true` behind HTTPS; keep Postgres and Redis internal.
- **Privacy:** make sure meeting participants know recordings are transcribed; you
  are responsible for complying with applicable laws.

More: **[documentation/16-security.md](documentation/16-security.md)**.

---

## Development & testing

```bash
.venv/bin/python -m pytest -v
.venv/bin/python -m compileall app scripts
docker compose config        # needs a local .env (cp .env.example .env)
docker compose build
```

PostgreSQL integration tests run against a real database via `TEST_DATABASE_URL`
(or `DATABASE_URL`); when unreachable they **skip** — they never fall back to
SQLite. Local engines are mocked in tests (no model downloads). See
**[documentation/17-development.md](documentation/17-development.md)** and
**[18-testing.md](documentation/18-testing.md)**.

---

## Roadmap

Compile whisper.cpp multiarch into the image, safe model auto‑download, local
diarization, transcript search, AI summaries, notifications, and a browser
extension to auto‑start recording — see
**[documentation/19-roadmap.md](documentation/19-roadmap.md)**.

## License

MIT
