# Roadmap

This document is a forward-looking plan for Meet Transcription. It explains
**what already exists** in the codebase today and **what is planned** next, why
each item matters, and a rough approach for implementing it without breaking the
hard rules in [`CLAUDE.md`](../CLAUDE.md): PostgreSQL stays the single source of
truth (no SQLite), tokens/keys stay encrypted at rest, nothing transcribes inside
an HTTP request, the UI stays server-rendered Jinja2 (no React/SPA), and the
legacy CLI (`python -m app.main`) keeps working.

Cross-links: [Architecture](01-architecture.md) ·
[Local transcription](06-local-transcription.md) ·
[faster-whisper](07-faster-whisper.md) · [whisper.cpp](08-whisper-cpp.md) ·
[Redis queue](09-redis-queue.md) · [Postgres & migrations](10-postgres-and-migrations.md) ·
[Worker flow](11-worker-flow.md) · [Web UI](12-web-ui.md).

## How to read this document

Every item below is tagged with one of these statuses:

| Status | Meaning |
|---|---|
| **Done** | Already implemented and tested in this repository. |
| **Partial** | Foundations exist in code; one concrete piece is still missing. |
| **Planned** | Not implemented yet; described here as a design intention. |

> Status reflects the current `integration/postgres-platform` branch. Nothing in
> the **Planned** column is in the codebase yet — do not treat planned items as
> existing features or flags.

## Status at a glance

| # | Item | Status |
|---|---|---|
| 1 | Compile whisper.cpp multiarch into the image | **Partial** (engine done; binary still external) |
| 2 | Safe model auto-download | **Partial** (faster-whisper only; off by default) |
| 3 | Local diarization (speaker labels) | **Planned** (schema ready) |
| 4 | Transcript search | **Done** (user-scoped Postgres full-text search at `/search`) |
| 5 | AI summaries / meeting minutes | **Partial** (provider scaffold in `app/summaries/`; no LLM call yet) |
| 6 | Notifications | **Done** (outbound webhooks — [35-webhooks.md](35-webhooks.md)) |
| 7 | Chrome extension to auto-start recording | **Planned** |
| 8 | Advanced multi-worker scaling | **Partial** (lock + concurrency exist) |
| 9 | Transcript exports (TXT/JSON/SRT/VTT/MD) | **Done** ([36-export-formats.md](36-export-formats.md); PDF planned) |
| 10 | Onboarding wizard | **Done** ([33-onboarding.md](33-onboarding.md)) |
| 11 | Observability (`/health`, `/ready`, `/version`, structured logs) | **Done** ([34-observability.md](34-observability.md)) |

---

## 1. Compile whisper.cpp multiarch into the image

**Status: Partial.** The whisper.cpp engine is fully implemented in
`app/transcription/whisper_cpp_provider.py` (extract 16 kHz mono WAV with ffmpeg →
run `whisper-cli -oj` → parse offsets → normalize → clean scratch). What is *not*
in the image is the `whisper-cli` binary itself: today it is **external**, pointed
to by `WHISPER_CPP_BINARY`, and `INSTALL_WHISPER_CPP=true` only `apt`-installs
`ffmpeg`.

```dockerfile
# today: ffmpeg is installed, but whisper-cli is NOT compiled
docker build --build-arg INSTALL_WHISPER_CPP=true -t meet-transcription:wc .
# you must still mount/provide whisper-cli yourself and set:
#   WHISPER_CPP_BINARY=/usr/local/bin/whisper-cli
#   LOCAL_TRANSCRIPTION_MODEL_PATH=/models/ggml-small-q4_0.bin
```

**Why.** Requiring operators to build/mount `whisper-cli` by hand is the biggest
friction point for the lightest-weight local engine. Shipping a prebuilt binary
for both `x86_64` and `ARM64` (the project targets ordinary VPSs on both arches)
would make whisper.cpp a one-flag, zero-extra-setup option.

**Rough approach.**

- Add a multi-stage build to the `Dockerfile`: a builder stage that clones and
  compiles whisper.cpp's `whisper-cli` for the target architecture (driven by
  Docker's `TARGETARCH`), gated behind the existing `INSTALL_WHISPER_CPP` build
  arg so default images stay slim.
- Copy the compiled `whisper-cli` into the runtime image at a stable path and
  default `WHISPER_CPP_BINARY` to it; keep the env var overridable so an external
  binary still works.
- Use `docker buildx` with `--platform linux/amd64,linux/arm64` so the GHCR image
  (`ghcr.io/gabedsam01/meet-transcription`) is multiarch.
- No application-code change is required — the provider already shells out to
  whatever `WHISPER_CPP_BINARY` points at. This is purely a build/packaging task.
- Keep `LOCAL_TRANSCRIPTION_MODEL_PATH` mandatory for whisper.cpp (the engine
  cannot auto-download a model); see item 2.

**Done-when:** `docker build --build-arg INSTALL_WHISPER_CPP=true` on either arch
produces an image whose default `WHISPER_CPP_BINARY` resolves to a working
`whisper-cli`, with no external mount required.

---

## 2. Safe model auto-download

**Status: Partial.** `LOCAL_TRANSCRIPTION_AUTO_DOWNLOAD` exists and defaults to
`false`. When `true`, **only faster-whisper** can fetch a model at job time
(`faster_whisper_provider.py` passes `local_files_only=not auto_download` and
`download_root=model_dir`). whisper.cpp cannot auto-download — its
`LOCAL_TRANSCRIPTION_MODEL_PATH` is always required.

```bash
# faster-whisper, one-time fetch allowed:
LOCAL_TRANSCRIPTION_ENABLED=true
LOCAL_TRANSCRIPTION_ENGINE=faster-whisper
LOCAL_TRANSCRIPTION_AUTO_DOWNLOAD=true   # default is false
LOCAL_TRANSCRIPTION_MODEL_DIR=/models
```

**Why.** Operators currently have to pre-stage models under `./models` (mounted
read-only at `/models`). A safe, opt-in download path would smooth first-run
setup — especially for whisper.cpp `ggml-*.bin` files, which today must be fetched
and placed manually.

**Rough approach.**

- Extend a download helper to whisper.cpp: given
  `LOCAL_TRANSCRIPTION_MODEL` + `LOCAL_TRANSCRIPTION_QUANTIZATION`, resolve the
  expected `ggml-<model>-<quant>.bin` and, when `LOCAL_TRANSCRIPTION_AUTO_DOWNLOAD=true`,
  fetch it into `LOCAL_TRANSCRIPTION_MODEL_DIR` and set
  `LOCAL_TRANSCRIPTION_MODEL_PATH` accordingly.
- **Safety first** (this is why it stays off by default): verify a published
  checksum after download, restrict downloads to the known model/quant matrix
  (only the supported multilingual models — never `.en` — and the five
  whisper.cpp quantizations `q4_0`/`q4_1`/`q5_0`/`q5_1`/`q8_0`), download to a
  temp path and atomically rename, and reject anything outside the model dir.
- Run downloads **only in the worker**, never in an HTTP request, and ideally as a
  one-time warm-up at worker startup so the first job is not stalled by a fetch.
  The `/models` mount is read-only in compose today, so this needs a writable
  cache location (or a relaxed mount) documented alongside the feature.
- Keep the provider-status rule intact: a model that fails to download leaves the
  config **invalid**, so the UI shows *"Modelo local inválido. Consulte a
  documentação de modelos locais."* and run-once is blocked unless a Deepgram key
  is set — no silent fallback.

**Done-when:** with `LOCAL_TRANSCRIPTION_AUTO_DOWNLOAD=true`, both engines can
acquire a supported model on first use with checksum verification, and the default
(`false`) still guarantees zero network fetches at job time.

---

## 3. Local diarization (speaker labels)

**Status: Planned (schema already supports it).** Deepgram produces speaker labels
today. Local engines do **not** — the MVP stores `speaker = null` in every
segment. Crucially, the normalized transcript schema in
`transcripts.transcript_json` **already has the field**:

```json
{
  "provider": "local",
  "engine": "faster-whisper",
  "segments": [{"start": 0.0, "end": 3.2, "speaker": null, "text": "…"}]
}
```

**Why.** "Who said what" is the single most-requested capability that local
transcription lacks relative to Deepgram. Meeting transcripts are far more useful
when each line is attributed to a speaker.

**Rough approach.**

- Add a diarization step in the worker after the local engine produces segments:
  run a CPU-friendly speaker-diarization pass over the same 16 kHz mono WAV the
  engine already extracts, then assign each segment a `speaker` by overlapping its
  `start`/`end` with the diarization turns.
- Populate the existing `speaker` field in `transcripts.transcript_json.segments`
  and reflect speakers in the rendered `.txt` (`normalizer.render_local_text`) so
  the **Download TXT** gains speaker prefixes — matching what Deepgram already
  does.
- Keep it CPU-only and **opt-in** behind a new env flag (default off), because
  diarization adds significant CPU cost on top of an already ~1× realtime engine;
  document the extra time/RAM in the [VPS recommendations](06-local-transcription.md).
- No database migration is needed for the JSON payload (the schema field exists);
  only rendering and the worker pipeline change. Mock the diarizer via the same
  injectable pattern used for `model_factory` / `runner` / `audio_extractor` so
  tests stay download-free.

**Done-when:** local transcripts carry non-null `speaker` values, the TXT shows
speaker labels, and diarization is off by default with a clear cost note.

---

## 4. Transcript search

**Status: Done (minimal).** A user-scoped search now ships at `GET /search`:
`TranscriptRepository.search_transcripts` (PostgreSQL full-text search via
`to_tsvector('simple', transcript_text) @@ plainto_tsquery`, backed by the GIN
index in migration `0002_add_transcript_fulltext_index.py`; case-insensitive
substring in the in-memory fake). Results are always filtered to the requesting
user. Remaining ideas (ranking, language-specific dictionaries, snippet
highlighting) are future refinements. See [36-export-formats.md](36-export-formats.md)
for the related download work. The original design notes follow.

Every completed job already persists
`transcripts.transcript_text` (the human-readable TXT) and
`transcripts.transcript_json` (the normalized schema) in PostgreSQL. Today the web
UI lists jobs and serves a per-job **Download TXT** at `/jobs/{id}/download`, but
there is **no full-text search** across transcripts.

**Why.** Once a few dozen meetings are transcribed, "find the meeting where we
discussed X" becomes the primary user need. The content is already in Postgres —
it just is not queryable yet.

**Rough approach.**

- Use PostgreSQL's built-in full-text search (`tsvector` / `to_tsquery`) over
  `transcripts.transcript_text` — no new datastore, keeping Postgres as the single
  source of truth. Configure the text-search dictionary for Portuguese and English
  to match the multilingual content.
- Add an Alembic migration (next number after
  `0001_create_initial_postgres_schema.py`) introducing a generated `tsvector`
  column plus a GIN index on it.
- Extend the `JobRepository` contract (`app/core/ports.py`) with a new,
  **user-scoped** search method (results always filtered to the requesting user,
  like `list_jobs_for_user` / `get_job`) and implement it in
  `app/repositories/postgres.py`; mirror it in the in-memory fake
  `app/repositories/memory.py` for tests.
- Add a search box and a results view to the server-rendered jobs page
  (`app/web/templates/`, CSS in `app/web/static/styles.css`) — **no SPA**, just a
  form that GETs query results. Reuse the existing `mid`/`mono`/`dt` helpers for
  layout.

**Done-when:** a signed-in user can search across only their own completed
transcripts from the web UI, backed entirely by PostgreSQL.

---

## 5. AI summaries / meeting minutes

**Status: Partial (scaffold only).** `app/summaries/` now defines the provider
contract (`SummaryProvider`, `Summary`), configuration (`SummarySettings` from
`SUMMARY_ENABLED`/`SUMMARY_PROVIDER`/`SUMMARY_MODEL`, **off by default**), a
status helper, and a `NullSummaryProvider` that raises a friendly
`SummaryUnavailableError`. **No LLM is called** — a concrete provider is still the
remaining work. The design below is the intended next step.

Transcripts are stored but not yet summarized by a real provider.

**Why.** A full transcript answers "what was said"; a summary answers "what
matters" — decisions, action items, and a short recap are what most users actually
want out of a meeting recording.

**Rough approach.**

- Add an **optional, post-transcription** worker step that sends the completed
  `transcript_text` to an LLM and stores the result. Keep it **out of the HTTP
  request path** — summarization happens in the worker after `mark_completed`,
  exactly like transcription itself.
- Persist the summary in PostgreSQL: either a new column on the `transcripts` table
  or reusing `transcript_json` for structured minutes (recap, decisions, action
  items). Ship the change as a new Alembic migration.
- Treat the LLM provider key like the Deepgram key: **per-user and encrypted at
  rest** via the Fernet helper in `app/web/security.py` (`APP_SECRET_KEY`-derived),
  following the `deepgram_credentials` pattern (`encrypted_api_key`). Never store
  or log it in plaintext.
- Surface the summary on the job detail page (`/jobs/{id}`) and optionally as a
  second download, reusing the server-rendered templates.
- Model the feature as opt-in with friendly `AppError` subclasses (e.g. a missing
  summarization key produces a secret-free `user_message`, consistent with
  `DeepgramKeyRequiredError`), so a missing/invalid key never breaks transcription.

**Done-when:** a completed job can carry an LLM-generated summary, generated in the
worker, with the summarization key stored encrypted per user.

---

## 6. Notifications

**Status: Done (webhooks).** The worker now emits best-effort outbound webhooks on
`job.completed` / `job.failed` (`app/webhooks/`, fired from
`JobProcessor._emit_webhook` after the terminal transition). Delivery never blocks
a job, retries transient failures (429/5xx/network), and the payload is
secret-free. Configure with `WEBHOOK_URL` + `WEBHOOK_EVENTS` —
see [35-webhooks.md](35-webhooks.md). Per-user notification destinations and
email/chat channels remain future work; the original design notes follow.

Job state lives in `transcription_jobs` (`pending` →
`processing` → `completed`/`failed`, with a friendly `error_message`).

**Why.** Local CPU transcription can take **an hour or more** for a 60-minute
meeting. Polling the UI for that long is poor UX; an active notification ("your
transcript is ready" / "a job failed") closes the loop.

**Rough approach.**

- Emit a notification from the worker at the existing terminal transitions —
  right where `JobProcessor.process` calls `mark_completed` / `mark_failed` —
  using the friendly `user_message` already attached to errors so notifications
  stay **secret-free** (tracebacks remain in logs only).
- Start with channels that need no browser session: email and/or webhook
  (e.g. a chat webhook). Make the destination a per-user setting in Postgres,
  encrypting any token/secret at rest like other credentials.
- For in-app feedback, the dashboard already computes status cards (Google, Drive
  source, Deepgram, Transcription, Queue, Total jobs, Last job); a lightweight
  "last job" banner can surface completion without introducing an SPA.
- Keep delivery best-effort and isolated: a failed notification must never fail or
  retry the transcription job itself.

**Done-when:** users can opt into at least one notification channel and receive a
secret-free message when their job completes or fails.

---

## 7. Chrome extension to auto-start recording

**Status: Planned.** Today the pipeline begins only **after** a recording already
exists in the Google Drive source folder; the worker reads MP4s from Drive (the
input), and the UI's **Run once** scans that folder. Nothing in the project starts
the recording — that is a manual step in Google Meet.

**Why.** The most error-prone part of the whole flow is a human remembering to
press "record" at the start of every meeting. A browser extension that auto-starts
recording (and lets the recording land in the watched Drive folder as usual) would
make capture reliable and hands-off.

**Rough approach.**

- Build a separate Chrome/Chromium extension (its own codebase — it is **not** part
  of the FastAPI app and does **not** touch the no-SPA rule for the server-rendered
  UI). It detects a Google Meet call and triggers recording so the resulting MP4
  lands in the user's Meet Recordings Drive folder, which the worker already
  watches.
- Keep the backend contract unchanged: the extension's only job is to ensure a
  recording appears in the **source Drive folder**; everything downstream
  (detection, queueing, transcription) works exactly as it does for a manually
  started recording.
- Respect Google Workspace recording permissions and policies; document clearly
  that participants must be informed that the meeting is recorded and transcribed
  (consistent with the privacy note in [Security](16-security.md)).
- Optionally, expose a small authenticated endpoint or reuse existing OAuth so the
  extension can confirm the user's configured source folder — without ever moving
  transcription work into a request.

**Done-when:** installing the extension reliably produces a Drive recording that
the existing worker picks up, with no change to the transcription pipeline.

---

## 8. Advanced multi-worker scaling

**Status: Partial.** The concurrency primitives already exist:

- A **Redis global lock** (`transcription:global_lock`, `SET NX EX` with a token)
  ensures **one transcription at a time** even across worker replicas — so
  concurrent "Run once" clicks never start two CPU transcriptions at once.
- `WORKER_CONCURRENCY` (default `1`) controls in-process queue-loop threads.
- The worker self-heals: `requeue_pending_jobs` re-enqueues all Postgres `pending`
  jobs at startup and while idle, `ensure_queued` (Redis `LPOS`) recovers an id
  orphaned in the dedupe set, and `reset_stale_processing_jobs` /
  `recover_stale_jobs` fail jobs stuck in `processing` after
  `STALE_JOB_TIMEOUT_MINUTES`.
- The final dedupe defense is atomic in Postgres: `claim_job(job_id)` /
  `claim_next_pending_job` flips `pending → processing` so two workers can never
  grab the same job.

What is **not** done is *true parallel scaling*: the global lock deliberately
serializes transcription, which is correct for a single CPU box (local engines are
heavy) but caps throughput at one job at a time across the whole fleet.

**Why.** A team with many recordings, or a deployment using **Deepgram** (which is
fast and offloads CPU to an external service), should be able to process several
jobs in parallel instead of being throttled by a lock designed for a CPU-bound
single host.

**Rough approach.**

- Make the global lock **provider-aware / capacity-aware**: replace the single
  binary `transcription:global_lock` with a bounded concurrency mechanism (e.g. a
  small pool of slots in Redis) so N jobs can run when capacity allows — keeping
  the strict single-slot behavior as the safe default for local CPU engines, where
  `WORKER_CONCURRENCY=1` should remain the recommendation.
- Allow horizontal scaling of the `worker` service (multiple replicas), relying on
  the already-atomic `claim_job` / `claim_next_pending_job` in Postgres as the
  correctness guarantee — Redis only schedules; Postgres remains the source of
  truth.
- Tie the parallelism budget to the active provider: more slots when Deepgram is
  the provider (network-bound), few or one when a local engine is active
  (CPU-bound), reusing the provider-resolution path
  (`get_transcription_provider_status` / `resolve_provider`).
- Add per-worker metrics/health so an operator can see queue depth, in-flight jobs,
  and stale recoveries before scaling out.

**Done-when:** the deployment can run multiple worker replicas with a configurable,
provider-aware parallelism budget, while preserving exactly-once job claiming and
the safe single-slot default for local CPU transcription.

---

## Non-goals (explicitly out of scope)

To keep the roadmap honest, these stay **off** the table — they would violate the
project's hard rules:

- **SQLite or any non-Postgres primary store.** PostgreSQL remains the single
  source of truth; Redis stays queue/lock only. See
  [Postgres & migrations](10-postgres-and-migrations.md).
- **A React/SPA front end or a JS build step.** The UI stays server-rendered
  Jinja2 with local CSS. See [Web UI](12-web-ui.md).
- **Transcription inside an HTTP request.** All heavy work stays in the worker;
  the request path only validates and enqueues. See [Worker flow](11-worker-flow.md).
- **Plaintext tokens or API keys.** Every credential stays encrypted at rest via
  Fernet (`APP_SECRET_KEY`). See [Security](16-security.md).
- **Breaking the legacy CLI** (`python -m app.main`, `--once`/`--watch`/`--reprocess`).
  It remains a supported, env-driven deployment.

## Contributing to the roadmap

When you implement a roadmap item:

1. Keep the [hard rules](../CLAUDE.md) intact — especially no SQLite, encryption
   at rest, no in-request transcription, and the `JobRepository` contract method
   names in `app/core/ports.py`.
2. Add or extend repository methods in **both** the Postgres adapter
   (`app/repositories/postgres.py`) and the in-memory fake
   (`app/repositories/memory.py`).
3. Ship schema changes as a **new Alembic migration** under `alembic/versions/`
   (the `migrate` service runs `alembic upgrade head` on startup).
4. Run the validation suite before finishing:

   ```bash
   python -m pytest -v
   python -m compileall app scripts
   docker compose config        # needs a local .env (cp .env.example .env)
   docker compose build
   ```

5. Move the item from **Planned**/**Partial** to **Done** in the table above and
   update the relevant sibling docs.
