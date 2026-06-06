# Chrome Recorder Extension (Meet Audio Recorder)

The **Meet Audio Recorder** is a Manifest V3 Chrome extension that records the
**tab audio** of a Google Meet call (and, optionally, your **microphone**) with a
single click and uploads it to the Meet Transcription backend. Instead of waiting
for Google to drop an MP4 into a Drive folder, you capture the call directly and
the backend transcribes it like any other job.

This document covers the end-to-end flow (popup → offscreen `MediaRecorder` →
upload), the backend endpoint contract (`POST /api/recordings/upload`), the
environment that **gates the feature OFF by default**, how to load the unpacked
extension, and troubleshooting.

See also: [Overview](00-overview.md) for the Drive-based flow this complements,
[Architecture](01-architecture.md) for the service topology, [Worker
flow](11-worker-flow.md) for the job lifecycle, and [Local
transcription](06-local-transcription.md) — an extension upload can be transcribed
by **either** Deepgram **or** a local CPU engine, exactly like a Drive job.

## The product decision

Google Meet only writes a recording MP4 to Drive when the org has cloud recording
enabled and someone presses *Record*. The Chrome extension gives the same
transcription pipeline a **second, self-service input**: capture the tab audio in
the browser and POST it to the backend. The decision is governed by the same firm
principles as the rest of the stack:

- **The HTTP request NEVER transcribes.** Like "Run once", the upload route only
  validates the token, streams the media to disk, and creates a `pending` job.
  The **worker** owns every download/transcribe/save step. No transcription ever
  runs inside an HTTP request.
- **PostgreSQL stays the single source of truth.** The endpoint writes the media
  to a shared `./data` volume and creates a job row; job *state* lives only in
  Postgres. **No Alembic migration is needed** — an upload reuses the existing
  `transcription_jobs` row and marks it with a sentinel `source_file_id`.
- **The feature is OFF by default.** There is no `EXTENSION_*` enable flag; the
  feature is enabled **only** by setting `EXTENSION_UPLOAD_TOKEN` (a secret).
  Unset, the route answers `503` and nothing else changes.
- **No Drive, no Google token.** An uploaded recording did not come from Drive, so
  the worker needs neither a Drive client nor a per-user Google OAuth token to
  transcribe it (see [Worker handling](#worker-handling-no-drive-no-token)).
- **The token is never logged.** It is compared in constant time and never echoed
  into any log line, response body, or error message — on either side.

## End-to-end flow

```
 popup: you click "Iniciar gravação"  (REQUIRED user gesture for tabCapture)
                                │
                                ▼
 background.js (service worker): chrome.tabCapture.getMediaStreamId(targetTabId)
                                │  → create offscreen document (reason USER_MEDIA)
                                ▼
 offscreen.js + recorder.js: getUserMedia(tab stream)  [+ optional mic, mixed]
                                │  → MediaRecorder → WebM/Opus Blob
                                ▼
 api.js: POST {backendUrl}/api/recordings/upload   (multipart, Bearer token)
                                │
                                ▼
 backend (web): validate token → stream media to ./data/recordings → create pending job
                                │
                                ▼
 worker: dequeue → resolve provider → transcribe local WebM (no Drive) → save in Postgres
                                │
                                ▼
 web UI: "Download TXT" for the completed job  (served from Postgres, not Drive)
```

Step by step:

1. **One click starts it.** `chrome.tabCapture` **requires a user gesture**, so
   the extension can never auto-start. Recording begins only when you click
   **"Iniciar gravação"** in the popup while a Google Meet tab is active.
2. **Three contexts cooperate.** MV3 service workers have no DOM and cannot use
   `MediaRecorder` / `getUserMedia`. So `popup.js` messages `background.js` (the
   service worker), which mints a tab stream id with
   `chrome.tabCapture.getMediaStreamId({ targetTabId })` and spins up an
   **offscreen document** (`chrome.offscreen`, reason `USER_MEDIA`). `offscreen.js`
   + `recorder.js` exchange the id for the tab audio and record it.
3. **Optional mic mix.** If you tick *"Também gravar meu microfone"*, a second
   `getUserMedia({ audio: true })` stream is mixed with the tab audio through one
   `AudioContext`. Tab audio is routed back to your speakers (so capturing it
   doesn't mute the call); the mic is not (to avoid echo). If mic permission is
   denied, recording continues with **tab audio only** rather than failing.
4. **WebM/Opus out.** `MediaRecorder` encodes `audio/webm;codecs=opus`. On stop
   the assembled `Blob` is handed to `api.js`.
5. **Auto-stop.** `content.js` runs on `meet.google.com` and detects you leaving
   the call (SPA URL change or the "you left the meeting" screen) to stop
   automatically; you can also click **"Parar gravação"**. While recording a red
   **REC** badge shows on the toolbar icon.
6. **Upload.** `api.js` POSTs the multipart form to
   `{backendUrl}/api/recordings/upload` with `Authorization: Bearer <token>`.
7. **Worker transcribes; UI serves the TXT.** The worker picks up the pending job
   and transcribes the recording; the transcript is downloaded from the web UI.

## The backend endpoint

`POST /api/recordings/upload` (`upload_recording` in `app/web/main.py`).

This route is **token-authenticated, not session-authenticated** — the extension
is not logged in. It validates, streams the upload to the shared recordings dir,
and enqueues a job. It never downloads, transcribes, or uploads to Drive.

### Authentication

- Header: `Authorization: Bearer <EXTENSION_UPLOAD_TOKEN>` (the extension sends
  this). `X-Upload-Token: <token>` is also accepted as a fallback
  (`_extract_upload_token`).
- The provided token is compared to `EXTENSION_UPLOAD_TOKEN` with
  `secrets.compare_digest` (constant time). It is **never** logged or echoed in
  any error.

### Request (multipart/form-data)

| Field | Type | Notes |
| --- | --- | --- |
| `file` | file | The recording. Field name is exactly **`file`**. WebM/Opus by default. |
| `meeting_url` | string | Meet URL (optional). |
| `meeting_title` | string | Tab title; becomes `source_file_name`, defaulting to *"Gravação do Meet"*. |
| `started_at` | string | ISO 8601 (optional). |
| `ended_at` | string | ISO 8601 (optional). |
| `duration_seconds` | number | Recording length (optional). |
| `source` | string | Always `"chrome-extension"`; defaults to that if omitted. |

The stored file extension is derived by `_recording_suffix` from the upload
filename, then the `content_type`, falling back to `.webm` (the extension's
default). Recognized: `.webm`, `.ogg`, `.opus`, `.m4a`, `.mp4`, `.wav`, `.mp3`.

The body is streamed to disk in 1 MiB chunks; if the running total exceeds
`EXTENSION_UPLOAD_MAX_MB`, the partial file is deleted and the request fails with
`413`. An empty upload (0 bytes) is deleted and rejected with `400`.

### Responses

| Status | When | Body |
| --- | --- | --- |
| `201 Created` | Accepted: media stored, metadata sidecar written, pending job created (and enqueued if Redis is up). | `{"job_id": <int>, "status": "pending", "recording_id": "<uuid hex>"}` |
| `400 Bad Request` | Empty recording (0 bytes). | `{"detail": "Gravação vazia."}` |
| `401 Unauthorized` | Missing or wrong Bearer token. | `{"detail": "Token de upload inválido."}` |
| `413 Payload Too Large` | Upload exceeds `EXTENSION_UPLOAD_MAX_MB`. | `{"detail": "Gravação excede o limite de <N> MB."}` |
| `503 Service Unavailable` | Feature disabled (no `EXTENSION_UPLOAD_TOKEN`), owner account missing/inactive, or the worker repositories/queue can't be resolved. | `{"detail": "Upload da extensão desativado."}` / *"Conta de upload indisponível."* / queue error |

If Redis is up, the new `job_id` is enqueued; if `enqueue` fails the job simply
**stays pending** and the worker reconciles it later (`requeue_pending_jobs`) —
Postgres is the source of truth, so the upload still succeeds with `201`.

### What gets stored

The endpoint writes two things to the shared recordings dir
(`EXTENSION_RECORDINGS_DIR`, default `/app/data/recordings`, under the `./data`
volume that **web and worker both mount**):

- the media: `<recording_id>.<ext>` (e.g. `<uuid>.webm`); and
- a JSON sidecar: `<recording_id>.json` — a `RecordingMetadata` record holding the
  meeting fields and stored filename. **The sidecar never contains secrets.**

The media file is written **before** the job row is created, so the worker can
never claim a job whose recording is not yet on disk.

### The `chrome-extension:<uuid>` sentinel

The job's `source_file_id` is set to the sentinel
`chrome-extension:<recording_id>` (`source_file_id_for`). This is kept
deliberately distinct from any Google Drive id, so the worker can tell an upload
from a Drive file with one unambiguous check — **no schema change, no migration**.
Helpers live in `app/recordings.py`:

| Helper | Purpose |
| --- | --- |
| `source_file_id_for(id)` | Build `chrome-extension:<id>` for the job row. |
| `is_upload_source(sid)` | True iff `sid` starts with `chrome-extension:`. |
| `recording_id_from_source(sid)` | Strip the prefix back to the `recording_id`. |
| `resolve_recording_file(dir, id)` | Locate the stored media (sidecar filename, else `glob`). |
| `cleanup_recording(dir, id)` | Best-effort removal after a terminal job state. |

## Worker handling (no Drive, no token)

In `JobProcessor.process` (`app/worker/processor.py`) the worker detects an upload
purely from the sentinel: `is_upload = is_upload_source(job.source_file_id)`. When
true:

- it does **not** require user `settings` or a Google token (both `DriveFolderMissingError`
  and `GoogleTokenMissingError` preconditions are skipped for uploads);
- it does **not** build a Drive client or download anything; instead it copies the
  local recording into the job workspace (`_prepare_upload_media`, via
  `resolve_recording_file`). Copy (not move) keeps the original until the job
  reaches a terminal state so a transient failure can retry. A missing file raises
  `RecordingNotFoundError` → a friendly failed job;
- it resolves the provider with the **same** local/Deepgram rule as Drive jobs
  (see [Local transcription](06-local-transcription.md)) and transcribes the
  WebM/Opus media directly;
- it saves the transcript to Postgres but **never copies it to Drive** — the
  `save_copy_to_drive` branch is gated on `not is_upload`, because the recording
  never came from Drive. **Upload-job transcripts are downloaded from the web UI**
  (Download TXT), served straight from Postgres.

On any terminal state the recording + sidecar are cleaned up
(`cleanup_recording`); a leftover file is harmless because Postgres owns job
state.

## Configuration

All of these are read by the **web** service (`WebSettings.from_env` in
`app/web/config.py`). The feature is OFF until `EXTENSION_UPLOAD_TOKEN` is set.

| Variable | Default | Purpose / risk if wrong |
| --- | --- | --- |
| `EXTENSION_UPLOAD_TOKEN` | *(empty → feature OFF)* | **Enables the feature and authenticates uploads.** A **secret** — treat it like an API key. Unset → every upload returns `503`. Anyone with this token can submit recordings, so keep it long and random. |
| `EXTENSION_UPLOAD_MAX_MB` | `500` | Max upload size in MB; larger uploads get `413`. A non-positive/invalid value falls back to `500`. |
| `EXTENSION_UPLOAD_USER_EMAIL` | `ADMIN_USERNAME` | Email of the account that **owns** uploaded jobs (and thus can download the transcript). Must match an **active** user, else `503`. |
| `EXTENSION_RECORDINGS_DIR` | `/app/data/recordings` | Where media + sidecars are stored. Must be on the **shared `./data` volume** so the worker can read what web wrote. |

Example (`.env`, placeholders only — never commit a real token):

```bash
EXTENSION_UPLOAD_TOKEN=replace-with-a-long-random-secret
EXTENSION_UPLOAD_MAX_MB=500
EXTENSION_UPLOAD_USER_EMAIL=admin@example.com
EXTENSION_RECORDINGS_DIR=/app/data/recordings
```

> **Never commit secrets.** Keep `EXTENSION_UPLOAD_TOKEN` in `.env` (git-ignored),
> not in source or `manifest.json`. The token is sent only in the `Authorization`
> header and is never logged on either side.

## Loading and configuring the extension

### Load unpacked

1. Open `chrome://extensions`.
2. Toggle **Developer mode** (top-right) ON.
3. Click **Load unpacked**.
4. Select `chrome-extension/meet-audio-recorder/` (the folder with
   `manifest.json`).
5. Pin the extension so the toolbar icon and **REC** badge are visible.

Requires **Chrome 116+** (for the offscreen `USER_MEDIA` reason).

### Set Backend URL + token

Open the popup, expand **Configurações**, and set:

- **URL do backend** — your backend origin, e.g. `http://localhost:8000`.
  Recordings are POSTed to `${backendUrl}/api/recordings/upload`.
- **Token de upload** — your `EXTENSION_UPLOAD_TOKEN`. The field is **masked**
  (`type=password`); once saved it shows as `••••••••` and the real value is never
  read back into the popup or logged.

Click **Salvar configurações** (both values persist in `chrome.storage.local`).
Tick *"Também gravar meu microfone"* to mix your mic in.

> **Changing the backend origin:** No manifest edit needed. The extension uses
> `optional_host_permissions` to request access to your backend origin at runtime.
> When you enter the Backend URL and click **Salvar**, Chrome will prompt you to
> grant permission for that origin. If denied, the extension shows a clear error.

### Permissions rationale

| Permission | Why |
| --- | --- |
| `tabCapture` | Capture the Meet tab's audio (gated behind your click). |
| `offscreen` | Host `MediaRecorder` / `getUserMedia`, which the SW cannot run. |
| `storage` | Persist backend URL, upload token, and mic preference. |
| `activeTab` | Identify the Meet tab you clicked from. |
| `scripting` | Reserved for injecting helpers into the Meet page when needed. |
| host `https://meet.google.com/*` | Run the content script on Meet. |
| optional_host `https://*/*` | Upload recordings to any HTTPS backend (granted at runtime). |
| optional_host `http://localhost/*` | Upload recordings to a local dev backend (granted at runtime). |

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Upload returns `503` *"Upload da extensão desativado."* | `EXTENSION_UPLOAD_TOKEN` is not set on the web service. | Set the token in `.env`, restart **web**. |
| Upload returns `401` *"Token de upload inválido."* | Token in the popup doesn't match `EXTENSION_UPLOAD_TOKEN`. | Re-enter the exact token under Configurações. |
| Upload returns `413` | Recording larger than `EXTENSION_UPLOAD_MAX_MB`. | Record shorter sessions or raise the limit. |
| Upload returns `503` *"Conta de upload indisponível."* | `EXTENSION_UPLOAD_USER_EMAIL` (or admin) has no active user. | Point it at an existing active account. |
| `fetch` never reaches the backend / network error in popup | Backend origin permission not granted or backend unreachable. | Re-enter the Backend URL and click Salvar; grant the permission prompt. Ensure the backend is running and reachable. |
| Recording won't start | `tabCapture` needs a user gesture; no active Meet tab. | Click **"Iniciar gravação"** from a focused Meet tab. |
| Job created but stuck `pending` | Redis enqueue failed; worker reconciles from Postgres. | Wait — `requeue_pending_jobs` picks it up; check the worker is running. |
| Job `failed` with "Recording media not found" | Media missing from `EXTENSION_RECORDINGS_DIR`. | Ensure web and worker share the same `./data` volume / dir. |
| Job `failed` mentioning Deepgram/local model | Provider not configured (no Deepgram key and no valid local engine). | Configure a Deepgram key or a valid local engine — see [05](05-deepgram.md) / [06](06-local-transcription.md). |
| No transcript copy in Drive | **By design.** Upload-job transcripts are not copied to Drive. | Use **Download TXT** in the web UI. |
