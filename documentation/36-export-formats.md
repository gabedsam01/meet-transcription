# Transcript export formats

A completed job has exactly one transcript stored in Postgres, but operators and
users want it in different shapes: a flat `.txt` to paste into notes, a `.json`
for downstream tooling, subtitle files (`.srt` / `.vtt`) to overlay on the
recording, or `.md` for a clean rendered page. Instead of storing five copies,
the web layer renders the format **on demand** from the one stored transcript.

This page documents the download route, the supported formats and where each
one gets its data, the subtitle timestamp rules, and why PDF is on the roadmap
but intentionally absent. The render functions live in
[`app/exports/__init__.py`](../app/exports/__init__.py); the ownership/status
gate lives in
[`app/services/download_service.py`](../app/services/download_service.py); the
route lives in [`app/web/main.py`](../app/web/main.py).

Rendering is **pure**: nothing in `app/exports/` touches the database, Drive, or
any provider — it takes the stored text plus the normalized payload and returns
a string. That keeps export trivially testable and keeps the **web layer free of
any transcription work** (Redis is only the queue/lock; the worker did the
transcribing long before this download is ever served).

## The download route

```
GET /jobs/{job_id}/download?format=txt|json|srt|vtt|md
```

Defined at `app/web/main.py:472` (`download_transcript`). The `format` query
parameter is optional; it is lower-cased and stripped, and **defaults to `txt`**
(`DEFAULT_FORMAT` in `app/exports/__init__.py:32`). So the plain
`/jobs/{id}/download` link on the job detail page (`Download TXT`) is just the
`txt` case of the same route.

The route is **strictly per-user owned**. It calls
`get_transcript_export(worker_repos, job_id, user.id, fmt, is_admin=False)`
(`app/services/download_service.py:57`), which runs the shared gate in
`_resolve_completed_transcript` (`app/services/download_service.py:25`):

1. `repositories.jobs.get_job(job_id)` — if the job does not exist **or** belongs
   to another user, it raises `DownloadError("not_found", ...)`. Note `is_admin`
   is hard-coded `False` at the call site: even an admin gets a 404 for someone
   else's job, so **the existence of other users' jobs never leaks**.
2. Job must be `JobStatus.COMPLETED` (`status == "completed"`), else
   `DownloadError("not_completed", ...)`.
3. The transcript row must exist (`transcripts.get_by_job`), else
   `DownloadError("no_transcript", ...)`.

An unsupported `format` is caught **before** any DB read by `is_supported(fmt)`
and raises `DownloadError("bad_format", ...)`.

`DownloadError.code` (`app/services/download_service.py:11`) is a stable reason
string the route maps to HTTP status (`app/web/main.py:485`):

| `code`          | HTTP | Meaning                                              |
| --------------- | ---- | ---------------------------------------------------- |
| `not_found`     | 404  | Unknown job id, or not owned by the requester        |
| `no_transcript` | 404  | Job is completed but has no transcript row           |
| `not_completed` | 409  | Job exists and is owned but is still pending/running |
| `bad_format`    | 400  | `format` is not one of `txt/json/srt/vtt/md`         |
| (any other)     | 400  | Defensive fallback                                   |

On success the route returns a `Response` with the format's media type and a
`Content-Disposition: attachment; filename="..."` header. The filename is
`{sanitized source name}_Transcricao.{ext}` (see `build_export`,
`app/exports/__init__.py:75`) — e.g. `Reuniao_Equipe_Transcricao.srt`.

A request that arrives before the worker repositories are available (no DB
configured) gets a `503` from `_resolve_worker_repositories` — that is an
infrastructure error, not a transcript error.

## Supported formats

`EXPORT_FORMATS` (`app/exports/__init__.py:21`) is the single registry; its key
order is also the order the buttons render in the UI. `available_formats()`
feeds those `(fmt, label)` pairs to the template.

| Format | Media type                         | Ext    | What it contains                                                                            |
| ------ | ---------------------------------- | ------ | ------------------------------------------------------------------------------------------- |
| `txt`  | `text/plain; charset=utf-8`        | `.txt` | The stored human-readable transcript (`transcript.text`) verbatim — headers, speaker lines. |
| `json` | `application/json; charset=utf-8`  | `.json`| The full normalized payload (`transcript_json`), `indent=2`, `ensure_ascii=False`.          |
| `srt`  | `application/x-subrip; charset=utf-8` | `.srt` | SubRip subtitle cues built from `segments`; timestamps `HH:MM:SS,mmm`.                    |
| `vtt`  | `text/vtt; charset=utf-8`          | `.vtt` | WebVTT subtitle cues (`WEBVTT` header) from `segments`; timestamps `HH:MM:SS.mmm`.          |
| `md`   | `text/markdown; charset=utf-8`     | `.md`  | A rendered Markdown page: title, motor/idioma metadata, then `[HH:MM:SS] Speaker N:` lines. |

The user-facing labels are Portuguese, as in the running app: `Texto (.txt)`,
`JSON (.json)`, `Legendas SRT (.srt)`, `Legendas WebVTT (.vtt)`, `Markdown
(.md)`.

## Where the data comes from

There are two inputs, both columns on the transcript row:

- **`transcripts.transcript_text`** — the human-readable `.txt` the worker wrote
  (the Deepgram/local header + timestamped speaker lines; see
  `render_local_text` in
  [`app/transcription/normalizer.py`](../app/transcription/normalizer.py)). The
  `txt` export returns this **unchanged**.
- **`transcripts.transcript_json`** (JSONB) — the normalized payload, the same
  internal schema for Deepgram and every local engine
  (`normalized_payload`, `app/transcription/normalizer.py:25`):

```json
{
  "provider": "deepgram",
  "engine": "deepgram",
  "model": "nova-2",
  "language": "pt",
  "text": "full transcript text ...",
  "segments": [
    { "start": 0.0, "end": 4.12, "speaker": 0, "text": "Bom dia a todos." }
  ],
  "words": [],
  "utterances": [],
  "raw": {}
}
```

`json`, `srt`, `vtt`, and `md` all read **`payload["segments"]`** — a list of
`{start, end, speaker, text}` where `start`/`end` are **seconds** (floats,
rounded to 3 places) and `speaker` is a label or `null`.

### Segments → subtitle cues

`_cues` (`app/exports/__init__.py:138`) turns segments into `(start, end, text)`
tuples for SRT/VTT:

- Empty-text segments are dropped.
- A zero-length or inverted segment (`end <= start`) is given a 2-second window
  so the cue is readable.
- A non-null `speaker` is prepended inline as `[Speaker N] ...`.

`_timestamp` (`app/exports/__init__.py:164`) formats seconds into the two
subtitle conventions, differing only in the milliseconds separator:

- **SRT** uses a comma: `HH:MM:SS,mmm` (e.g. `00:01:04,120`).
- **VTT** uses a dot: `HH:MM:SS.mmm` (e.g. `00:01:04.120`).

Rounding that spills into 1000 ms is carried into the next second. The Markdown
renderer uses the coarser `_clock` helper (`HH:MM:SS`, no millis,
`app/exports/__init__.py:176`) for its `[HH:MM:SS]` prefixes.

### Full-text fallback (no segments)

Some transcripts have no usable segments (e.g. a Deepgram result with only a
flat channel transcript, normalized to a single `(0.0, 0.0)` segment, or none).
Each renderer degrades gracefully so the download **always succeeds**:

- `json` — falls back to `{"text": transcript_text}` when the payload is empty.
- `srt` / `vtt` — `_cues` emits **one cue** spanning the whole text, with an
  estimated end time of `max(2.0, word_count * 0.4)` seconds.
- `md` — when no segment renders, it prints the payload `text` (or the `.txt`
  fallback, or `"Transcrição não disponível."`).

This is why subtitle exports are valid even for a transcript with no timing — the
file is coarse (one long cue) but never empty or malformed.

## Privacy

Exports carry only the transcript content and benign metadata (provider name,
engine, model, language). **No secret is ever rendered into an export, a
filename, or an error** — Google tokens and per-user Deepgram keys stay encrypted
at rest and never enter this path. `DownloadError` messages are generic on
purpose (`"Job not found"`) so they cannot confirm another user's job.

## PDF is on the roadmap (not implemented)

`PLANNED_FORMATS = ("pdf",)` (`app/exports/__init__.py:30`) records PDF as a
**known future format that is intentionally not built**. It is not in
`EXPORT_FORMATS`, so `is_supported("pdf")` is `False` and
`GET /jobs/{id}/download?format=pdf` returns **400 `bad_format`** — the same as
any unknown format.

The reason is deliberate, consistent with the project's lazy-dependency rule:
PDF rendering needs a **heavy dependency** (a headless HTML/PDF renderer or a
report library) that the base image does not ship. Pulling it in unconditionally
would bloat the web image for a rarely-used format, so PDF stays gated until
there is demand. See [19-roadmap.md](19-roadmap.md).

## Example downloads

Authenticated downloads carry your session cookie. Replace `42` with the job id
and `localhost:8000` with your host.

```bash
# Plain text (default — no format param needed)
curl -L -b cookies.txt -OJ http://localhost:8000/jobs/42/download

# Normalized JSON payload
curl -L -b cookies.txt -OJ "http://localhost:8000/jobs/42/download?format=json"

# SubRip subtitles (HH:MM:SS,mmm)
curl -L -b cookies.txt -OJ "http://localhost:8000/jobs/42/download?format=srt"

# WebVTT subtitles (HH:MM:SS.mmm)
curl -L -b cookies.txt -OJ "http://localhost:8000/jobs/42/download?format=vtt"

# Markdown
curl -L -b cookies.txt -OJ "http://localhost:8000/jobs/42/download?format=md"
```

`-OJ` honors the server's `Content-Disposition` filename
(`Reuniao_Transcricao.srt`). Error cases behave as mapped above:

```bash
# Job not completed yet -> 409
curl -i -b cookies.txt "http://localhost:8000/jobs/42/download?format=srt"

# Unknown format -> 400
curl -i -b cookies.txt "http://localhost:8000/jobs/42/download?format=pdf"
```

## In the UI

On the job detail page (`app/web/templates/job_detail.html`), a completed job
shows the primary `Download TXT` button plus an `Exportar como:` row of small
ghost buttons — one per `available_formats()` entry, each linking to
`/jobs/{{ job.id }}/download?format={{ fmt }}`. The labels are the Portuguese
strings from `EXPORT_FORMATS`. See [12-web-ui.md](12-web-ui.md).

## See also

- [00-overview.md](00-overview.md) — what the system is and the end-to-end flow.
- [11-worker-flow.md](11-worker-flow.md) — how the transcript and its normalized
  payload get written before any download is possible.
- [12-web-ui.md](12-web-ui.md) — the job detail page and the export buttons.
