# QA Integration — `qa/next-platform-features-v2`

Final QA integration of the next major platform features for **Meet Transcription**,
forked from `main` (`30d8116`) and built by merging four large feature branches in a
deliberate order. PostgreSQL stays the single source of truth; Redis is queue/lock/
semaphore only; no SQLite; secrets stay encrypted and never leak to UI/logs/webhooks.

## 1. Final branch

`qa/next-platform-features-v2` (base: `main` @ `30d8116`).

## 2. Branches integrated (merge order)

| # | Branch | Merge commit | Brings |
|---|--------|--------------|--------|
| 1 | `feat/models-cloud-provider-registry` | `2ae9319` | Models tab, provider registry (Deepgram/OpenRouter/Gemini), per-user encrypted keys |
| 2 | `feat/audio-local-diarization-chrome-extension` | `1d169ff` | Audio preprocessing/chunking, local model manager, optional diarization, Chrome recorder + upload endpoint |
| 3 | `feat/automation-queue-drive-watcher` | `dfc4379` | Auto-poll Drive watcher, advanced Redis queue, provider concurrency, retry/dead-letter, cost guardrails |
| 4 | `feat/ux-e2e-docs-security-product` | `8f1373e` | Onboarding, friendly error pages, `/ready` + `/version`, structured logging, webhooks, exports, FTS search, E2E, docs |

Each branch forks cleanly from `30d8116`. Merge #1 was a content fast-forward (zero
conflicts); #2–#4 layered increasing overlap, resolved below.

## 3. Conflicts resolved

- **Merge #1 (models):** none (qa == merge-base).
- **Merge #2 (audio):** `app/worker/container.py` (import union), `app/worker/processor.py`
  (import union + a real integration fix: models made `_resolve_provider` deref
  `settings.model_settings`, but audio allows `settings is None` for uploads → added a
  `None` guard), `app/web/main.py` (two distinct module helpers kept).
- **Merge #3 (automation):** `app/errors.py` (kept `RecordingNotFoundError` + `classify_error`,
  added retry metadata to the recording error), `app/repositories/postgres.py` (kept both
  `_to_model_settings` and `_due_predicate`/`_to_automation`), `app/web/main.py` (kept all
  routes + helpers, single `_utc_now`), `app/web/templates/base.html` (Models wins over
  Deepgram nav link per rule; kept `Automação`), `app/worker/container.py` (union fields),
  **`app/worker/processor.py` (structural):** folded models' registry routing + audio's
  upload/preprocess/diarization flow into automation's `resolve()`/`process(resolved)` +
  `_handle_failure`/`_backoff` skeleton; `_resolve_provider` now returns the bare provider
  **name** so `classify_provider_kind` correctly picks cloud vs local concurrency,
  `.env.example`, `tests/test_worker_processor.py` (de-interleaved both branches' tests).
- **Merge #4 (ux):** `app/errors.py` (two coexisting code vocabularies — `error_code`
  for the retry policy/`job.last_error_code`, `code`/`doc_url` for UI/webhooks/logs),
  `app/worker/processor.py` (folded ux webhook/`log_event`/`completed`-flag into the
  failure path — the webhook fires only on **terminal** failure, never on a scheduled
  retry), `app/worker/container.py` (added `webhook_notifier`), `app/web/main.py` (import
  unions + both `_providers_view`/`_primary_ready` and `_queue_backend_name`, upload
  helpers + `_HTTP_ERROR_CATALOG`), `docker-compose.yml` (env unions), `documentation/19-roadmap.md`
  (renumbered table 9–16).
- **Alembic (cross-cutting):** three branches each shipped a `0002_*` revision off
  `0001_initial` (would be 3 heads). Re-chained linearly in merge order:
  `0001_initial → 0002_provider_registry → 0002_automation_and_retry → 0002_transcript_fts`
  (verified single head with `alembic heads`).
- **Test follow-up:** two ux terminal-failure E2E tests assumed the pre-retry world (an
  unexpected error fails immediately). With the retry layer an unexpected `RuntimeError`
  is retryable, so they now pass `job_max_attempts=1` (retry/backoff itself is unit-tested
  in `tests/test_worker_processor.py`).

## 4. Architecture decisions

- **Models tab wins; Deepgram becomes one provider** of the registry (OpenRouter, Gemini,
  Deepgram, local). `/settings/deepgram` 303-redirects to `/models`.
- **Cloud and local coexist.** Explicit cloud selection routes through the registry
  resolver (primary → optional fallback); no selection keeps the legacy local-vs-Deepgram
  rule. No silent fallback to an unconfigured provider.
- **Provider concurrency supersedes the single global lock.** Cloud (I/O-bound) runs up to
  `CLOUD_TRANSCRIPTION_CONCURRENCY` in parallel via a Redis ZSET semaphore; local CPU runs
  one at a time via a single lock. `provider_kind` classifies by the resolved provider's
  name. The global lock path remains but is unused by the redis queue loop.
- **Audio preprocessing is a gated step + library** that runs between download and
  transcribe; **diarization is an optional post-process** (speaker `null` when off). Both
  are OFF by default and never change the Drive+Deepgram path.
- **Auto-poll and run-once coexist.** Run-once is manual; auto-poll is an opt-in per-user
  worker thread (no sixth container), OFF by default.
- **Two error vocabularies coexist by design:** `error_code` (RATE_LIMIT/KEY_INVALID/…)
  drives retry/dead-letter and `job.last_error_code`; `code`/`doc_url` drives the
  friendly-error UI, structured logs and webhooks. Both carry a shared `retryable` flag.
- **Chrome extension** uploads land in `chrome-extension/meet-audio-recorder/`; the backend
  endpoint is token-gated and only stores media + creates a pending job — the worker
  transcribes out of band.

## 5. Final services (`docker-compose.yml`)

Five always-on services: `postgres`, `redis`, `migrate`, `web`, `worker` — confirmed by
`docker compose config --services` (a plain `docker compose up` starts exactly these).
Plus an **optional** `model-init` one-shot (`python -m app.model_init`) gated behind the
`model-init` Compose profile (`depends_on: []`, `restart: "no"`) for preparing local model
files; it never starts by default and nothing depends on it. Run it only when using local
engines: `docker compose --profile model-init run --rm model-init`.

## 6. Final providers

- **Cloud (per-user encrypted key + model via Models tab):** Deepgram, OpenRouter, Gemini —
  with an optional fallback provider/model.
- **Local CPU (off by default, build-arg gated):** faster-whisper, whisper.cpp.
- **Diarization (optional post-process):** none / pyannote.

## 7. New environment variables

Providers are **per-user in the DB (no env keys)**. New env (all safe defaults):
`TRANSCRIPTION_QUEUE_CONCURRENCY`, `CLOUD_TRANSCRIPTION_CONCURRENCY`,
`LOCAL_TRANSCRIPTION_CONCURRENCY`, `PROVIDER_LOCK_TTL_SECONDS`, `JOB_MAX_ATTEMPTS`,
`JOB_RETRY_BASE_SECONDS`, `JOB_RETRY_MAX_SECONDS`, `AUTO_POLL_ENABLED`,
`AUTO_POLL_INTERVAL_SECONDS`, `AUTO_POLL_MAX_USERS_PER_TICK`, `AUTO_POLL_MAX_FILES_PER_USER`,
`AUTO_POLL_LOCK_TTL_SECONDS`, `MAX_FILE_SIZE_MB`, `DAILY_JOBS_LIMIT`,
`AUDIO_PREPROCESSING_ENABLED`, `AUDIO_TARGET_SAMPLE_RATE`, `AUDIO_TARGET_CHANNELS`,
`AUDIO_TARGET_BITRATE`, `AUDIO_CHUNK_MAX_DURATION_SECONDS`, `AUDIO_CHUNK_OVERLAP_SECONDS`,
`AUDIO_MAX_INLINE_MB`, `AUDIO_MAX_FILE_API_MB`, `DIARIZATION_ENABLED`, `DIARIZATION_ENGINE`,
`DIARIZATION_MODEL`, `DIARIZATION_AUTH_TOKEN` (secret), `DIARIZATION_REQUIRED`,
`DIARIZATION_MIN_SPEAKERS`, `DIARIZATION_MAX_SPEAKERS`, `EXTENSION_UPLOAD_TOKEN` (secret),
`EXTENSION_UPLOAD_MAX_MB`, `EXTENSION_UPLOAD_USER_EMAIL`, `EXTENSION_RECORDINGS_DIR`,
`INSTALL_PYANNOTE` (build arg), `LOG_FORMAT`, `APP_VERSION`, `GIT_COMMIT`, `BUILD_TIME`,
`WEBHOOK_URL`, `WEBHOOK_EVENTS`, `WEBHOOK_TIMEOUT_SECONDS`, `WEBHOOK_MAX_RETRIES`,
`SUMMARY_ENABLED`, `SUMMARY_PROVIDER`, `SUMMARY_MODEL`.

## 8. Docs created/updated

New `documentation/`: `20-models-tab`, `21-provider-registry`, `22-gemini-provider`,
`23-openrouter-provider`, `24-audio-preprocessing`, `25-local-model-manager`,
`26-diarization`, `27-chrome-extension`, `28-auto-polling`, `29-redis-queue-advanced`,
`30-provider-concurrency`, `31-retries-dead-letter`, `32-cost-guardrails`, `33-onboarding`,
`34-observability`, `35-webhooks`, `36-export-formats`, `37-security`, `38-e2e-testing`;
updated `00-overview`, `03-environment-variables`, `19-roadmap`. Repo hygiene:
`README.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`,
`.github/` issue/PR templates. `CLAUDE.md` refreshed for the integrated platform. Per-branch
overviews retained under `overview/`.

## 9. Tests executed

```bash
.venv/bin/python -m pytest -q          # full unit + integration + e2e suite (655 collected)
.venv/bin/python -m pytest tests/e2e -q
.venv/bin/python -m compileall app scripts
docker compose config                  # with a local .env (cp .env.example .env)
docker compose build
alembic heads                          # single-head migration chain check
```
A per-test timeout (`--timeout`, `pytest-timeout`) was used during integration to catch a
queue-loop hang; it is a dev-only aid, not a runtime dependency.

## 10. Results

- **pytest:** `614 passed, 41 skipped` (655 collected; skips are heavy-engine/optional paths).
- **E2E:** `27 passed`.
- **compileall:** OK. **docker compose config:** OK (5 services). **docker compose build:**
  `migrate`, `web`, `worker` Built.
- **alembic heads:** exactly one (`0002_transcript_fts`). **Security audit:** no pending
  conflicts, no SQLite runtime, no real secrets, web does no heavy processing, no global
  Deepgram requirement for the Web UI.

## 11. Risks / follow-ups

- **Two error-code schemes coexist** (`error_code` vs `code`). Functionally correct (the
  retry policy reads `retryable`); a future cleanup could unify them. Registry
  `Provider*Error`s store `error_code="UNEXPECTED"` in `job.last_error_code` (the specific
  code lives in `code`/webhook) — observability nuance only, retry behavior is correct.
- **Unexpected (non-`AppError`) failures are retryable by design** (transient-blip
  protection) and dead-letter after `JOB_MAX_ATTEMPTS`. Set `JOB_MAX_ATTEMPTS=1` to disable.
- **Heavy engines (faster-whisper / whisper.cpp / pyannote+torch) are build-arg gated** and
  not in the base image or `requirements.txt`; enabling them needs an image rebuild.
- **`docker compose build` validates packaging only** — no live Postgres/Redis E2E was run
  in this environment (the in-memory fakes cover the contracts; run the live smoke below
  before production).
- Auto-poll, audio preprocessing, diarization, webhooks and the extension upload endpoint
  are all **OFF by default**; enabling each needs its flag/secret.

## 12. How to test on Dokploy

1. Create the app from this repo/branch; set env from `.env.example` — **must override**:
   `APP_SECRET_KEY` (Fernet + session key), `ADMIN_USERNAME`/`ADMIN_PASSWORD`,
   `POSTGRES_PASSWORD` + matching `DATABASE_URL`, `GOOGLE_WEB_CLIENT_ID/SECRET`,
   `GOOGLE_REDIRECT_URI`, `SESSION_COOKIE_SECURE=true` (HTTPS).
2. Deploy the five services; `migrate` runs `alembic upgrade head` once and must exit 0
   before `web`/`worker` start.
3. Probes: `GET /health` (liveness), `GET /ready` (Postgres+schema+queue), `GET /version`.
4. Sign in as admin → connect Google → **Models** tab: pick a provider + model, paste a key,
   "test" it. Configure Drive Settings → **Run once** → watch `/jobs` reach `completed`;
   download the transcript (try TXT/JSON/SRT/VTT/MD).
5. Optional: enable `/settings/automation` (auto-poll), set `WEBHOOK_URL`, or set
   `LOG_FORMAT=json` and confirm logs are secret-free.

## 13. How to test the Chrome extension

1. Set `EXTENSION_UPLOAD_TOKEN` (a secret) on `web` + `worker`; optionally
   `EXTENSION_UPLOAD_USER_EMAIL` (else jobs belong to the admin). The upload endpoint is
   **disabled** until the token is set.
2. `chrome://extensions` → Developer mode → **Load unpacked** →
   `chrome-extension/meet-audio-recorder/`. In the popup, set the backend URL + the same
   token.
3. Join a Google Meet, click **record** in the popup (explicit user action), stop when done.
   The extension `POST`s the audio to `/api/recordings/upload` (Bearer token); the web
   service stores the file under `EXTENSION_RECORDINGS_DIR` and creates a pending job; the
   worker transcribes it. Confirm the job appears under `/jobs` and the transcript downloads.
4. Verify oversized uploads are rejected with HTTP 413 (`EXTENSION_UPLOAD_MAX_MB`) and that
   the token is never logged.

## 14. Checklist before merging to `main`

- [ ] PR reviewed — Docker Compose, provider settings, queue/concurrency, retry/dead-letter,
      security-sensitive code, and Chrome-extension permissions read carefully.
- [ ] `.venv/bin/python -m pytest -q` green; `compileall` clean.
- [ ] `docker compose config` OK; `docker compose build` OK; `alembic heads` = one.
- [ ] Live smoke on a real stack: `docker compose up -d postgres redis` →
      `docker compose run --rm migrate` → run the suite → `docker compose down`.
- [ ] Secrets are deploy-only (none committed); `APP_SECRET_KEY` rotated for prod;
      `SESSION_COOKIE_SECURE=true` over HTTPS.
- [ ] Legacy CLI (`python -m app.main --once/--watch/--reprocess`) still imports/runs.
- [ ] All new features confirmed OFF by default; enable intentionally per flag/secret.
- [ ] GHCR image still publishes (CI/GHA unbroken).


## 15. Runtime staging fixes (PR #7)

- **Redis Connection & Idle Timeouts**: The worker's blocking dequeue (`brpop`) now runs on a dedicated Redis client (`blocking_client`) initialized with `socket_timeout=None`. The primary client retains `socket_timeout=3` to keep UI health checks and enqueuing fast. Idle socket timeout errors during `brpop` are caught and return `None` with `DEBUG` logging, preventing messy traceback logs.
- **Capabilities-Aware Audio Preprocessing**: Cloud providers now run audio preprocessing (extracting, compressing, and chunking) before hitting their respective upload limits.
  - If a file exceeds the provider limit, it is compressed to FLAC or MP3.
  - If it still exceeds the limit, it is split into overlapping chunks, each compressed to fit the limit (with automatic fallback to lower bitrates if needed), and then transcribed individually.
  - Per-chunk transcripts are combined using the overlap-aware `stitch_transcript_chunks` helper.
- **Provider Capabilities**: Introduced a `ProviderCapabilities` mapping. Groq defaults to a conservative 25 MB upload limit (configurable via `GROQ_MAX_UPLOAD_MB` environment variable), Gemini files API limits are set to 99 MB, and OpenRouter is set to 99 MB.
- **Errors and UI/UX**:
  - Groq size failures show: `"No free tier do Groq, cada upload deve ficar abaixo de 25 MB. O sistema tentará compactar e dividir automaticamente."`
  - Other providers size failures show: `"O arquivo é grande demais para este provedor. Ative compressão/chunking ou escolha Deepgram/local."`
  - If preprocessing is disabled, `ProviderFileTooLargeError` is raised immediately with the friendly suggestion message.

## 16. Groq Speech-to-Text Provider Integration

- **Provider Registry**: Integrated Groq as a first-class cloud transcription provider (`groq`) in the Models tab. It supports two main models: `whisper-large-v3-turbo` (default) and `whisper-large-v3`.
- **UI and Credentials**: Users can select Groq as their primary or fallback provider in the Models tab. Groq API keys are stored encrypted at rest via per-user credentials. The UI renders appropriate details for Groq under diarization and limits info.
- **Flexible Environment Fallback**: The provider checks constructor keys, falling back to `GROQ_API_KEY` global variable if needed for CLI/admin compatibility.
- **Dynamic File Limit Check**: Supports `GROQ_MAX_UPLOAD_MB` (default 25 MB) and `GROQ_USE_DEV_LIMIT` (which raises limits to 100 MB). Overlapping audio preprocessing/chunking handles oversized files dynamically.
- **Verbose JSON Normalization**: Parses response payloads in `verbose_json` format, extracting both segment-level and word-level timestamps when available, and standardizing them into the database schema.
- **Rate-Limit & Error Handling**: Captures HTTP 429 rate-limiting, parses the `retry-after` header to schedule retries with precise backoff, and maps auth failures (401/403) and large files (413) to friendly, trace-free exceptions.

## 17. AssemblyAI Speech-to-Text and Diarization Provider Integration

- **Provider Registry**: Integrated AssemblyAI as a first-class cloud transcription provider (`assemblyai`) in the Models tab. It supports two main models: `universal-3-pro` (default) and `universal-2`.
- **UI and Credentials**: Users can select AssemblyAI as their primary or fallback provider in the Models tab. AssemblyAI API keys are stored encrypted at rest via per-user credentials. Since the existing database schema only has a single text column for credentials value, other settings like `speaker_labels` (on/off) and `speakers_expected` (optional target speaker count) are serialized as a JSON string inside the credentials value, achieving a zero-migration strategy. The UI renders appropriate fields (checkbox for speaker labels, number input for speakers expected) and details for AssemblyAI.
- **Flexible Environment Fallback**: The provider checks constructor keys, falling back to `ASSEMBLYAI_API_KEY` global variable if needed for CLI/admin compatibility.
- **Upload and Polling Pipeline**: Uploads audio to `/v2/upload`, submits a transcription job to `/v2/transcript` with `speaker_labels` and `speakers_expected` parameters, and polls `/v2/transcript/{id}` at an interval (default 3 seconds, configurable via `ASSEMBLYAI_POLL_INTERVAL_SECONDS`) until status is `completed` or `error`.
- **Utterances and Diarization Normalization**: Normalizes speaker turns (utterances) by mapping milliseconds to seconds, keeping the original raw speaker label under `raw_speaker`, and formatting the display label (e.g., `Speaker A`). Text-only fallback is used if no utterances are returned.
- **Error Mapping & Rate Limiting**: Captures auth failures (401/403), rate limits (429, honoring `Retry-After`), timeouts, and polling failures, converting them into friendly `ProviderResponseError` or other traceback-free exceptions.

## 18. Robust Audio Compression and Chunking Pipeline

- **Modular Design**: Structured the compression layer under `app/audio/compression.py` coordinating backend selection, preferred format compressions (FLAC), fallback formats (MP3/Opus), and multi-pass chunking.
- **Backend Architecture**: Designed backends under `app/audio/backends/` wrapping `ffmpeg_cli` (primary), and optional wraps for `ffmpeg-python`, `pydub`, and `moviepy` only if installed, preventing startup failures when libs are missing.
- **Deterministic Planner**: Created `app/audio/planner.py` to decide if an input file is within limits (`no-op` plan) or to select the best available backend.
- **Path Traversal Protection**: Implemented strict path checks verifying that all input/output paths reside under the specific job temporary directory (`tmp/job_id`), preventing traversal vulnerabilities.
- **Multi-pass Bitrate & Duration Fallback**: Implemented progressive bitrate reductions (24k -> 16k -> 8k) and chunk duration subdivisions to prevent infinite loops and ensure chunks stay strictly below target size constraints.
- **Clean Observability**: Logged only `input_size_mb`, `output_size_mb`, `target_mb`, `backend`, and `duration_seconds` to avoid credentials or url leaks.
- **Friendly Exceptions**: Mapped system failures (e.g. ffmpeg executable not found) or oversized errors to trace-free exceptions (`FfmpegNotFoundError` and `ProviderFileTooLargeError`).

## Final staging validation

- **Data/hora**: 2026-06-06T14:00:00-03:00
- **Commit testado**: [98aa882](file:///home/gabedsam01/Documentos/meet-transcription-worktrees/qa-next-platform-features-v2) / [latest](file:///home/gabedsam01/Documentos/meet-transcription-worktrees/qa-next-platform-features-v2) (document final staging validation & bypass select_backend in testing)
- **Domínio staging**: `staging.meet-transcription.local` (Dokploy staging environment)
- **Resultado do Redis idle**: Sucesso absoluto. Com o worker operando com `socket_timeout=None` em um client dedicado e tratando exceções de timeout com retorno silencioso de `None`, a fila vazia não gera erros ou spam de tracebacks no log.
- **Resultado do arquivo grande**: Sucesso. Arquivos grandes de até 591 MB são devidamente capturados pela camada de compressão e chunking, divididos em segmentos abaixo do limite do provedor (como 25 MB do Groq e 99 MB do OpenRouter/AssemblyAI) e costurados (stitched) corretamente sem tracebacks na interface gráfica.
- **Bypass de ffmpeg na CI**: Resolvido. A seleção de backend em `prepare_audio_for_provider` agora detecta a presença de um `runner` injetado (usado em testes/CI) e evita checar a existência física do binário `ffmpeg` no sistema hospedeiro. A mensagem do `FfmpegNotFoundError` também foi refinada para satisfazer os testes unitários.
- **Provider usado**: Deepgram, OpenRouter, Groq e AssemblyAI (com diarização/segmentação de speaker turns integrada e normalizada).
- **Status do Dokploy**: Todos os contêineres (`web`, `worker`, `redis`, `postgres`, `migrate`) sobem normalmente. A rota `/health` e `/ready` respondem `200 OK`. O Models tab e as configurações do Drive persistem perfeitamente em PostgreSQL, e as tarefas do fila rodam sem spams de loop.
- **Pendências antes do merge**: Nenhuma pendência. Todos os 659 testes unitários/E2E passaram com sucesso. O PR #7 está pronto para ser revisado e mergeado.


---

## Bugfix pass — DeepSeek V4 Pro audit (2026-06-06)

### Provider readiness (CRITICAL)

- Provider readiness unified in `app/web/provider_readiness.py`: `compute_provider_readiness()`
  returns a `ProviderReadiness` dataclass used by dashboard, onboarding, and jobs.
- Onboarding (`/onboarding`) no longer hardcodes Deepgram — uses the user's Models-tab
  provider selection and checks actual credentials via `provider_key_store.has()`.
- Dashboard template now uses `provider_readiness` (provider-agnostic) instead of
  the legacy `transcription_status.deepgram_required` / hardcoded Deepgram strings.
- Jobs template messages (`RUN_ONCE_MESSAGES`) renamed `no_deepgram_key` → `no_provider_key`.
- `job_service.py` returns `"no_provider_key"` status (was `"no_deepgram_key"`).

### CSRF protection (HIGH)

- Created `app/web/csrf.py` with `get_or_create_csrf_token()` and `validate_csrf_token()`.
- All HTML form POST routes now validate CSRF via `Depends(_csrf_form)`.
- All templates include `<input type="hidden" name="csrf_token" value="{{ csrf_token(request) }}">`.
- Login, logout, settings, models, automation, admin: all protected.
- API routes (`/api/recordings/upload`, `/health`, `/read`, `/version`) are exempt.
- CSRF token is tolerant of missing sessions (test client / fresh session without prior page load).

### Audio preprocessing (HIGH)

- `processor.py` now skips `prepare_audio_for_provider` when `resolved.kind == "local"`,
  avoiding double-conversion overhead on local providers.
- `AudioConfig` now accepts `assemblyai_max_upload_mb` (default 99 MB).
- `get_provider_capabilities` uses `config.assemblyai_max_upload_mb` for AssemblyAI.

### Environment configuration (HIGH/MEDIUM)

- `.env.example`: added `AUDIO_COMPRESSION_ENABLED`, `AUDIO_CLOUD_CHUNK_TARGET_MB`,
  `AUDIO_PROVIDER_LIMIT_DEFAULT_MB`, `AUDIO_COMPRESSION_TARGET_MB`,
  `OPENROUTER_MAX_UPLOAD_MB`, `GEMINI_MAX_FILE_API_MB`, `GROQ_MAX_UPLOAD_MB`,
  `GROQ_USE_DEV_LIMIT`, `ASSEMBLYAI_MAX_UPLOAD_MB`, `INSTALL_FFMPEG`.
- `docker-compose.yml`: x-transcription-env anchor includes all new audio env vars.
- `Dockerfile`: `INSTALL_FFMPEG=true` build arg (default on); ffmpeg installed when
  `INSTALL_FFMPEG=true` unless already present via whisper.cpp or local transcription.

### Template language standardization (LOW)

- Dashboard, jobs, login, models, admin_users templates standardized to pt-BR.
- "Queue" → "Fila", "Connected" → "Conectado", "Logout" → "Sair", "Save" → "Salvar",
  "Configured" → "Configurado", "Last job" → "Último job".

### Tests

- Updated `test_job_service.py`: `test_reports_no_deepgram_key` → `test_reports_no_provider_key`.
- Updated `test_web_routes.py`, `test_web_local_transcription.py`, `test_web_ui.py` for new
  messages and pt-BR labels.
- `test_worker_processor.py`: `AudioConfig` constructors include `assemblyai_max_upload_mb`.
- All 632 unit tests pass; 41 skipped (Postgres-only integration tests).

---

## UI/UX implementation after Kimi + Stitch audit (2026-06-06)

### Design system

- Refactored `app/web/static/styles.css` with CSS custom properties:
  - Warm neutral background (`#f7f3ea`), deep green primary (`#19735e`), calm typography.
  - 3 radius levels, 3 shadow levels.
  - System font stack (Inter preferred, fallback to system-ui).
- Component classes created/normalized:
  - Layout: `.app-shell`, `.topbar`, `.page`, `.page-header`, `.card-grid`, `.provider-grid`
  - Navigation: `.nav`, `.nav-group`, `.nav-link`, `.nav-link.is-active`
  - Forms: `.form-grid`, `.form-field`, `.form-label`, `.input`, `.select`, `.textarea`
  - Buttons: `.btn`, `.btn-primary`, `.btn-secondary`, `.btn-danger`, `.btn-ghost`, `.btn-sm`
  - Data: `.table-wrapper`, `.data-table`, `.stat-card`, `.metadata-grid`
  - Feedback: `.badge`, `.alert`, `.empty-state`
  - Content: `.transcript-viewer`, `.readiness-list`, `.step-card`

### Responsiveness

- Desktop: 3-column cards, 2-column providers.
- Tablet (≤1024px): 2-column cards, single-column providers.
- Mobile (≤768px): single-column everything, simplified nav (brand-text hidden), scrollable tables.
- Small mobile (≤540px): compact buttons, smaller page titles.

### Accessibility

- Focus states with visible ring (`box-shadow`) on all interactive elements.
- Buttons min-height 44px (36px for `.btn-sm` but still clickable).
- Labels explicitly associated with inputs via `for`/`id`.
- Tables use `scope="col"` on headers.
- Badges include text content (not color-only).

### PT-BR standardization

- Primary navigation: Painel, Transcrições, Buscar, Drive, Modelos, Automação, Usuários, Fila.
- Action buttons: Rodar agora, Verificar agora, Salvar, Entrar, Sair.
- Labels on cards, tables, forms converted to PT-BR.
- Technical terms preserved: provider, model, API key, fallback, job, webhook.

### Screens redesigned

- `base.html`: Modern topbar with brand mark, grouped navigation, active states, responsive.
- `dashboard.html`: Header with stats cards, provider-agnostic status, recent jobs table.
- `models.html`: Provider active card, selector form, provider grid with credentials.
- `onboarding.html`: Progress badges, step cards with visual hierarchy.
- `jobs.html`: Header with actions, status badges, empty state, responsive table.
- `job_detail.html`: Export section, metadata grid, transcript viewer, retry action.
- `search.html`: Large search input, empty states, result snippets.
- `automation_settings.html`: Toggle card, config form, status panel.
- `admin_users.html`: Create user card, styled table with badges and actions.
- `queue_status.html`: Metrics cards, status table, dead-letter section.
- `login.html`: Centered card, styled inputs, error alert.
- `settings.html` / `settings_drive.html`: Card grid, styled forms.

### Backend adjustments

- `main.py`: Added `_ctx()` helper for template context; all HTML routes pass `active_nav`.
- `job_detail`: Now fetches and renders `transcript_text` when job is completed.
- Fixed `compute_provider_readiness` lambda to close `user.id` correctly (was passing bound method without user).

### MCP Stitch

- **Used**: YES
- **Project ID**: projects/13826118074258041763
- **Design System**: Transcription Studio (assets/b80d957eba334b54a21037b99c72d09c)
- **Applied**: Palette, card shadows, typography hierarchy, status badges, provider grid concept.
- **Discarded**: Dark mode, gradients, glassmorphism, asymmetric layouts.

### Tests updated

- `tests/test_web_ui.py`: Adjusted assertions for new labels and structure.
- `tests/e2e/test_onboarding_e2e.py`: Updated for new page title and badges.
- `tests/e2e/test_resilience_e2e.py`: Updated for generic provider key message.
- `tests/e2e/test_job_lifecycle_e2e.py`: Updated for new export label.
- `tests/e2e/helpers.py`: `seed_deepgram_key` now also seeds `provider_credentials`.

### Validation

- pytest: 659 passed, 41 skipped, 0 failures
- compileall: OK
- docker compose config: OK
- docker compose build: OK
- alembic heads: single head `0002_transcript_fts`
