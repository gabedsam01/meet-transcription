# Meet Audio Recorder (Chrome Extension)

A Manifest V3 Chrome extension that records the **tab audio** of a Google Meet
call (and, optionally, your **microphone**) with a single click, then uploads the
recording to the Meet Transcription backend for transcription.

The recording is encoded as **WebM/Opus** (`audio/webm;codecs=opus`).

---

## One‑click requirement (read this first)

`chrome.tabCapture` **requires a user gesture**. The extension therefore can
**never** start recording on its own — recording only begins when **you click
"Iniciar gravação"** in the popup while a Google Meet tab is active. There is no
background auto-start, and any documentation/UI that implies otherwise would be
wrong.

Stopping is automatic when you leave the call (the content script detects the URL
change or the "call ended" screen), or manual via the **"Parar gravação"** button.

---

## Loading the extension (unpacked)

1. Open `chrome://extensions`.
2. Toggle **Developer mode** (top-right) ON.
3. Click **Load unpacked**.
4. Select the folder `chrome-extension/meet-audio-recorder/` (the folder that
   contains `manifest.json`).
5. Pin the extension so the toolbar icon (and the **REC** badge) is visible.

---

## Configure backend URL + upload token

Open the popup, expand **Configurações**, and set:

- **URL do backend** — the origin of your backend, e.g. `http://localhost:8000`.
  Recordings are POSTed to `${backendUrl}/api/recordings/upload`.
- **Token de upload** — your `EXTENSION_UPLOAD_TOKEN`. The field is **masked**
  (`type=password`); once saved it shows as `••••••••` and the real value is
  never read back into the popup or logged anywhere.

Click **Salvar configurações**. Both values are stored in
`chrome.storage.local`. To capture your mic too, tick **"Também gravar meu
microfone"** (Chrome will ask for mic permission the first time).

> **Changing the backend origin:** `manifest.json` ships with the placeholder
> host permission `http://localhost:8000/*`. If your backend lives elsewhere
> (e.g. `https://meet.example.com`), edit `host_permissions` in `manifest.json`
> to include that origin and reload the unpacked extension. The Backend URL field
> alone is not enough — the host must be in `host_permissions` for `fetch` to
> reach it.

---

## How recording works (architecture)

MV3 service workers have **no DOM** and cannot use `MediaRecorder` /
`getUserMedia`. So the flow spans three contexts:

1. **popup.js** — you click "Iniciar gravação" (the required user gesture) and it
   messages the service worker.
2. **background.js** (service worker) — calls
   `chrome.tabCapture.getMediaStreamId({ targetTabId })` to mint a stream id for
   the active Meet tab, then creates an **offscreen document**
   (`chrome.offscreen`, reason `USER_MEDIA`) and forwards the stream id to it.
3. **offscreen.js** + **recorder.js** — exchange the stream id for the tab audio
   via `navigator.mediaDevices.getUserMedia({ audio: { mandatory: {
   chromeMediaSource: "tab", chromeMediaSourceId } } })`. If you opted into the
   mic, a second `getUserMedia({ audio: true })` stream is **mixed** with the tab
   audio through a single `AudioContext` (tab audio is also routed back to your
   speakers so capturing it doesn't mute the call; the mic is not, to avoid
   echo). Everything is recorded with `MediaRecorder` as **WebM/Opus**, and on
   stop the assembled `Blob` is handed to **api.js** for upload.

**content.js** runs on `meet.google.com` and watches for you leaving the call
(SPA URL change or the "you left the meeting" screen) to auto-stop.

While recording, a red **REC** badge appears on the toolbar icon
(`chrome.action.setBadgeText`) and the popup shows a pulsing red dot.

---

## Permissions rationale

| Permission | Why |
| --- | --- |
| `tabCapture` | Capture the Google Meet tab's audio (gated behind your click). |
| `offscreen` | Host `MediaRecorder` / `getUserMedia`, which the SW cannot run. |
| `storage` | Persist backend URL, upload token, and mic preference. |
| `activeTab` | Identify the Meet tab you clicked from. |
| `scripting` | Reserved for injecting helpers into the Meet page when needed. |
| host `https://meet.google.com/*` | Run the content script on Meet. |
| host `http://localhost:8000/*` | Upload recordings to the backend (edit me). |

---

## Backend endpoint contract

The recording is uploaded as **`multipart/form-data`** to:

```
POST {backendUrl}/api/recordings/upload
Authorization: Bearer <EXTENSION_UPLOAD_TOKEN>
```

Form fields:

| Field | Type | Notes |
| --- | --- | --- |
| `file` | file | The audio blob (`audio/webm;codecs=opus`). Field name is exactly `file`. |
| `meeting_url` | string | The Meet URL. |
| `meeting_title` | string | The tab title. |
| `started_at` | string | ISO 8601 timestamp. |
| `ended_at` | string | ISO 8601 timestamp. |
| `duration_seconds` | number | Recording length in seconds. |
| `source` | string | Always `"chrome-extension"`. |

The backend is expected to authorize with the **Bearer `EXTENSION_UPLOAD_TOKEN`**
and to reject files larger than **`EXTENSION_UPLOAD_MAX_MB`** (HTTP 413). Non-2xx
responses surface a short, secret-free error in the popup.

---

## Caveats

- **WebM/Opus only.** The recording container is WebM with the Opus codec; the
  backend / transcription pipeline must accept (or transcode) WebM/Opus.
- **Tab capture needs a click.** See the one-click requirement above.
- **Mic is opt-in.** If permission is denied, recording continues with **tab
  audio only** rather than failing.
- **Secrets.** The upload token is sent only in the `Authorization` header and is
  never logged, never echoed in errors, and never displayed after saving.
- **Minimum Chrome 116** for the offscreen `USER_MEDIA` reason used here.
