# Overview — feat/audio-local-diarization-chrome-extension

## 1. Branch
`feat/audio-local-diarization-chrome-extension` (forked from `main`).

## 2. Objective
Add the audio + local-models + diarization + browser-capture block to Meet
Transcription, **without breaking the existing Deepgram/whisper providers or the
web + worker + PostgreSQL platform**:

1. Audio preprocessing library (probe / extract / compress / chunk / stitch).
2. Local model manager + optional auto-download + a one-shot `model-init` service.
3. Optional local speaker diarization (pyannote, CPU, off by default).
4. A Manifest V3 Chrome extension that records Google Meet **tab audio** (one
   click) and uploads it.
5. `POST /api/recordings/upload` that turns an uploaded recording into a worker job.

Everything new is **OFF by default** and additive, so the prior behavior is
byte-for-byte unchanged when the new flags are not set.

## 3. Files created
**Audio (`app/audio/`)** — `__init__.py`, `errors.py`, `config.py`, `probe.py`,
`preprocessor.py`, `compress.py`, `chunking.py`, `stitch.py`.

**Local model manager (`app/models/`)** — `__init__.py`, `errors.py`,
`manifest.py`, `validators.py`, `downloader.py`, `manager.py`. Plus the entrypoint
`app/model_init.py` (`python -m app.model_init`).

**Diarization (`app/diarization/`)** — `__init__.py`, `errors.py`, `config.py`,
`provider.py`, `none_provider.py`, `pyannote_provider.py`, `align.py`.

**Recording uploads** — `app/recordings.py` (the `chrome-extension:<uuid>`
sentinel, shared recordings dir, metadata sidecar helpers).

**Chrome extension (`chrome-extension/meet-audio-recorder/`)** — `manifest.json`,
`README.md`, `src/background.js`, `src/content.js`, `src/popup.html`,
`src/popup.js`, `src/recorder.js`, `src/api.js`, `src/offscreen.html`,
`src/offscreen.js`, `src/styles.css`.

**Docs** — `documentation/24-audio-preprocessing.md`,
`documentation/25-local-model-manager.md`, `documentation/26-diarization.md`,
`documentation/27-chrome-extension.md`, and this overview.

**Tests** — `tests/test_audio_probe.py`, `tests/test_audio_preprocess.py`,
`tests/test_audio_chunking.py`, `tests/test_audio_stitch.py`,
`tests/test_models_manager.py`, `tests/test_models_manifest.py`,
`tests/test_diarization.py`, `tests/test_diarization_align.py`,
`tests/test_recordings.py`, `tests/test_recordings_upload.py`,
`tests/test_worker_upload_job.py`, `tests/test_worker_diarization.py`,
`tests/test_worker_audio.py`, `tests/test_model_init.py`.

## 4. Files altered
- `app/errors.py` — added `RecordingNotFoundError`.
- `app/worker/container.py` — added optional, defaulted wiring: `audio_config`,
  `audio_runner`, `diarization_config`, `diarization_probes`,
  `build_diarization_provider`, `recordings_dir`; `build_container` populates them.
- `app/worker/processor.py` — upload-job branch (reads the local recording instead
  of Drive; no Google token required), optional audio no-audio fast-fail, optional
  diarization + transcript re-render. The Drive + Deepgram path is unchanged.
- `tests/support.py` — `make_worker_container` accepts the new optional kwargs.
- `app/web/config.py` — added `extension_upload_token`, `extension_upload_max_mb`,
  `extension_upload_user_email`, `recordings_dir`.
- `app/web/main.py` — added `POST /api/recordings/upload` + helpers.
- `docker-compose.yml` — audio/diarization env on the shared anchor, extension env
  on `web`, and an **opt-in** `model-init` service (profile `model-init`).
- `Dockerfile` — `INSTALL_PYANNOTE` build arg (pyannote.audio, lazy) + ffmpeg for
  the preprocessing path.
- `.env.example` — documented all new variables.
- `.gitignore` — ignore `data/recordings/` and downloaded `models/*`.
- `README.md` — new feature sections + doc links.

## 5. Migrations
**None.** Chrome-extension uploads reuse the existing `transcription_jobs.source_file_id`
(`Text`) column via the sentinel `chrome-extension:<uuid>`. The dedupe partial
unique index only constrains `completed` rows by source, and each upload has a
unique id, so no Alembic change was needed.

## 6. Environment variables added
- **Audio**: `AUDIO_PREPROCESSING_ENABLED` (default `false`),
  `AUDIO_TARGET_SAMPLE_RATE`, `AUDIO_TARGET_CHANNELS`, `AUDIO_TARGET_BITRATE`,
  `AUDIO_CHUNK_MAX_DURATION_SECONDS`, `AUDIO_CHUNK_OVERLAP_SECONDS`,
  `AUDIO_MAX_INLINE_MB`, `AUDIO_MAX_FILE_API_MB`.
- **Diarization**: `DIARIZATION_ENABLED` (default `false`), `DIARIZATION_ENGINE`
  (`none`), `DIARIZATION_MODEL`, `DIARIZATION_AUTH_TOKEN` (secret),
  `DIARIZATION_REQUIRED`, `DIARIZATION_MIN_SPEAKERS`, `DIARIZATION_MAX_SPEAKERS`.
- **Extension upload**: `EXTENSION_UPLOAD_TOKEN` (enables the feature; secret),
  `EXTENSION_UPLOAD_MAX_MB` (default `500`), `EXTENSION_UPLOAD_USER_EMAIL`
  (default = `ADMIN_USERNAME`), `EXTENSION_RECORDINGS_DIR`
  (default `/app/data/recordings`).
- **Build arg**: `INSTALL_PYANNOTE` (default `false`).

## 7. Tests added/changed
14 new test files (above). `tests/support.py` extended (backward compatible).
102 unit tests for the new modules + 25 integration tests (recordings helper,
upload endpoint incl. token/limit/no-secret-logging, worker upload jobs, worker
diarization, worker audio fast-fail, `model-init`).

## 8. Commands executed
```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m compileall app scripts        # rc=0
.venv/bin/python -m pytest -q                       # full suite
docker compose config                               # OK
docker compose --profile model-init config          # OK (service hidden by default)
docker compose build                                # default image
```

## 9. Test results
- Baseline before changes: **256 passed, 37 skipped**.
- After this branch: **383 passed, 37 skipped** (+127 new tests, 0 regressions).
- `compileall app scripts`: rc=0. `docker compose config`: OK.

## 10. Risks and limitations
- **Compression/chunking/stitching are a tested library, not yet wired into the
  Deepgram/whisper engines** (they decode long audio natively). The worker only
  uses `probe_audio` for a no-audio fast-fail. The chunk/stitch path is ready for a
  future size-limited (Gemini-style) provider.
- **pyannote.audio is heavy** (pulls torch); enabled only via `INSTALL_PYANNOTE`
  build arg and `DIARIZATION_ENABLED=true` + a Hugging Face token. Diarization
  re-extracts a 16 kHz WAV (needs ffmpeg) and, when enabled, overrides any
  Deepgram-native speakers.
- **Extension upload is single-account for the MVP**: uploads are attributed to
  `EXTENSION_UPLOAD_USER_EMAIL` (default the admin). A per-user token scheme is a
  future step.
- **Upload-job transcripts are not copied to Drive** (the recording never came from
  Drive); they are downloaded from the web UI.
- The Chrome extension ships a `http://localhost:8000/*` host-permission
  placeholder; a non-localhost backend origin must be added to `host_permissions`
  (documented in the extension README).
- `docker compose build` only builds the default (light) image; the heavy engines
  (faster-whisper / whisper.cpp / pyannote) are validated via their build args, not
  built here.

## 11. How to test manually
**Extension upload (no models needed — uses Deepgram or a local engine):**
1. `cp .env.example .env`, set `APP_SECRET_KEY`, `ADMIN_*`, and
   `EXTENSION_UPLOAD_TOKEN=<a long random string>`.
2. `docker compose up -d` (web on :8000), sign in, configure a per-user Deepgram
   key (or enable a valid local engine).
3. `curl -X POST http://localhost:8000/api/recordings/upload \
       -H "Authorization: Bearer $EXTENSION_UPLOAD_TOKEN" \
       -F file=@meeting.webm -F meeting_title="Test"` → `201 {job_id,...}`.
4. Watch the worker logs; the job completes and the transcript is downloadable from
   `/jobs`.
5. Load the extension: `chrome://extensions` → Developer mode → *Load unpacked* →
   `chrome-extension/meet-audio-recorder`. Set Backend URL + token in the popup,
   join a Meet, click **Iniciar gravação**, stop → it uploads.

**Model-init:** `docker compose --profile model-init run --rm model-init` with
`LOCAL_TRANSCRIPTION_ENABLED=true` and an engine configured.

## 12. Next steps
- Per-user/per-token attribution for extension uploads.
- Wire chunk/stitch into a size-limited provider if one is added.
- Surface uploaded recordings in the dashboard (source = chrome-extension badge).
- Optionally copy upload transcripts to Drive when the user has Drive configured.

## 13. PR
**https://github.com/gabedsam01/meet-transcription/pull/3**
(`feat/audio-local-diarization-chrome-extension` → `main`).

## 14. Confirmation
- ✅ **No SQLite reintroduced** — no `sqlite3` / `app.db` / `database_path`; jobs
  stay in PostgreSQL via the repository contracts; tests use the existing in-memory
  fakes.
- ✅ **No secrets logged** — the upload token uses a constant-time compare and is
  never logged or echoed in errors; the diarization Hugging Face token never
  appears in logs, errors, the UI, or stored transcripts (asserted by tests).
- ✅ **No heavy transcription in the Web UI** — `POST /api/recordings/upload` only
  validates, streams the file to disk, and creates a pending job; the worker does
  all download/transcribe/diarize work out of band.
