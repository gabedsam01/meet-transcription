# Local Model Manager + model-init

Local CPU transcription needs the whisper model **on disk before a job runs**.
This page documents the **model manager** (`app/models/`) — the small, pure,
injectable layer that names, validates and (optionally) downloads local whisper
models — and the **`model-init`** one-shot service (`app/model_init.py`) that
prepares a model ahead of time. It is the companion to
[06-local-transcription.md](06-local-transcription.md),
[07-faster-whisper.md](07-faster-whisper.md) and
[08-whisper-cpp.md](08-whisper-cpp.md).

Everything here is **opt-in and OFF by default**: with
`LOCAL_TRANSCRIPTION_ENABLED=false` (the default) the manager is a no-op and the
`model-init` service never runs.

## `app.models` is the model manager (not the ORM)

There are **two different "models"** in this codebase — do not confuse them:

| Package | What it is |
| --- | --- |
| `app.models` | The **local transcription model manager** (this page): download / validate / name whisper.cpp ggml files and faster-whisper HuggingFace snapshots. |
| `app.database.models` | The **SQLAlchemy ORM** (users, jobs, transcripts). Unrelated. |

The package docstring and module top-comments say this explicitly. `app.models`
imports **no heavy dependency** — importing it only needs `requirements.txt`.
Heavy optional deps (`huggingface_hub`) are imported **lazily**, inside the
default downloader only.

## What the manager does

All side-effecting work — HTTP downloads, HF snapshots, filesystem checks — is
**injectable**, so the web service and the test suite never hit the network or
the disk. The public surface (`app/models/__init__.py`):

- **`manifest.py`** — pure, side-effect-free naming. Given a model + quantization
  (validated against the allow-lists in `app/transcription/config.py`), it
  computes the on-disk filename, the HuggingFace download URL and the
  faster-whisper repo id. `resolve_spec(config)` turns a `TranscriptionConfig`
  into a `ModelSpec`.
- **`validators.py`** — presence checks (`whisper_cpp_model_present`,
  `faster_whisper_model_present`, `expected_whisper_cpp_path`) through an
  injectable `path_exists` callable (defaults to `os.path.exists`).
- **`downloader.py`** — the actual fetch, behind injectable callables:
  `fetcher` (for whisper.cpp ggml HTTP) and `downloader` (for faster-whisper HF
  snapshots).
- **`manager.py`** — `ensure_model(config) -> ModelStatus`, the single entry
  point. This is what `model-init` calls.
- **`errors.py`** — `ModelManagerError` and subclasses (`UnknownModelError`,
  `ModelDownloadError`, `AutoDownloadDisabledError`). Each subclasses
  `app.errors.AppError`, so each carries a short, friendly, secret-free pt-BR
  `user_message`; technical detail stays in `str(exc)`.

### `ensure_model` behavior

`ensure_model(config, *, fetcher=None, fw_downloader=None, path_exists=os.path.exists)`
returns a frozen `ModelStatus(engine, model, ready, path, message, reason)`. The
outcomes:

| Situation | Result |
| --- | --- |
| Local transcription **disabled** (`enabled=False`) | `ready=True` **no-op** — nothing to prepare (`path=None`). |
| Unknown engine or unknown model | raises `UnknownModelError`. |
| Model **present** on disk | `ready=True`, `path` set to the model location. |
| Model **missing** + `auto_download=True` | **downloads** it, then `ready=True`. |
| Model **missing** + `auto_download=False` | `ready=False`, `reason="modelo ausente"`, friendly `message` pointing at `LOCAL_TRANSCRIPTION_AUTO_DOWNLOAD`. |

For **faster-whisper**, "not ready" is non-fatal in practice: faster-whisper
also auto-downloads at first `WhisperModel` use, so a missing snapshot will be
fetched lazily at the first transcription **only if** auto-download is on. The
`message` says so.

### Where models live and how they are named

**whisper.cpp** — official ggml checkpoints are published under the HuggingFace
repo `ggerganov/whisper.cpp` (`WHISPER_CPP_HF_REPO`). Filenames follow the
`ggml-<model>[-<quant>].bin` scheme:

| Config | Filename | Download URL |
| --- | --- | --- |
| model `small`, quant `q4_0` | `ggml-small-q4_0.bin` | `https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small-q4_0.bin` |
| model `small`, no quant | `ggml-small.bin` | `https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin` |

The on-disk target is `config.model_path` if set, otherwise
`model_dir/<filename>`. The download goes through `urllib`
(`urllib.request.urlretrieve`, imported lazily) — no heavy dependency. After the
fetch the file is re-checked; if still missing it raises `ModelDownloadError`.

**faster-whisper** — the repo id is `Systran/faster-whisper-<model>`. There is no
single file: the artifact is a HuggingFace **snapshot directory** fetched via
`huggingface_hub.snapshot_download` (imported lazily, so it needs the
`INSTALL_FASTER_WHISPER` build arg present). Presence is a best-effort check for
the HF cache directory `models--Systran--faster-whisper-<model>` under
`model_dir`.

### Downloads never happen in the web service or in tests

This is a hard rule, enforced structurally:

- The **web** service mounts `./models:/models` **read-only** and never calls a
  downloader — it only validates model presence for the UI status badge.
- **`model-init` is the only place allowed to download.** Its module docstring
  states this directly.
- Because `fetcher`, `fw_downloader` and `path_exists` are all injectable,
  **tests pass fakes** and never touch the network or the filesystem. Importing
  `app.models` pulls in no heavy dependency.

## The `model-init` service

`app/model_init.py` is the `model-init` entrypoint, run as
`python -m app.model_init`. It loads `TranscriptionConfig.from_env()`, calls
`ensure_model`, and maps the result to a process exit code:

| Exit code | Meaning |
| --- | --- |
| `0` | **Ready or disabled** — local transcription off (nothing to prepare), or the model is present / was downloaded. |
| `1` | **Manager error** — a `ModelManagerError` was raised (e.g. unsupported model, download failure). Logs the friendly `user_message` plus technical detail; never a secret or traceback to stdout. |
| `2` | **Not ready** — model missing and not downloadable (auto-download off). Logs `status.reason` / `status.message`. |

### How to run it

In Compose, `model-init` is **profile-gated** so a plain `docker compose up`
**never** runs it. It has no `depends_on` (it only prepares model files — no
Postgres/Redis needed) and mounts `./models:/models` **writable** so an
auto-download can populate the volume.

```bash
# one-shot run, removed on exit (recommended):
docker compose --profile model-init run --rm model-init

# or, equivalently:
docker compose --profile model-init up model-init
```

`restart: "no"` keeps it a one-shot job. The **web** service does **not** depend
on it.

### It is optional — the worker validates at runtime anyway

`model-init` is a convenience, not a requirement. The **worker validates its
local config at runtime** on its own (`validate_local_config` via
`get_transcription_provider_status` in `app/transcription/`), and an invalid /
missing model surfaces the same no-silent-fallback message described in
[06-local-transcription.md](06-local-transcription.md). So if running the
service in Compose is inconvenient, skip it: let the worker (or, for
faster-whisper with auto-download, the first job) surface the state instead.

## Allowed models and quantizations

The manager validates against the allow-lists in `app/transcription/config.py`
(shared by both engines):

- **Models** (`ALLOWED_MODELS`, multilingual only — **no `.en`**):
  `tiny`, `base`, `small`, `medium`, `large-v1`, `large-v2`, `large-v3`,
  `large-v3-turbo`.
- **Engines** (`ALLOWED_ENGINES`): `faster-whisper`, `whisper-cpp`.
- **whisper.cpp quantizations** (`ALLOWED_QUANTIZATIONS`): `q4_0`, `q4_1`,
  `q5_0`, `q5_1`, `q8_0`. An empty value means the full-precision (unquantized)
  ggml file. Quantization does **not** apply to faster-whisper (use
  `LOCAL_TRANSCRIPTION_COMPUTE_TYPE` there instead).

Anything outside these lists raises `UnknownModelError` (→ `model-init` exit `1`).

## Configuration

The model manager and `model-init` read the same `LOCAL_TRANSCRIPTION_*`
environment as the worker (`TranscriptionConfig.from_env`). `from_env` never
raises on a bad value — an unknown engine/model just makes the config invalid
later — so it can never crash startup.

| Variable | Default | Used by the manager for |
| --- | --- | --- |
| `LOCAL_TRANSCRIPTION_ENABLED` | `false` | Master switch. `false` → `ensure_model` is a ready **no-op** and `model-init` exits `0`. |
| `LOCAL_TRANSCRIPTION_ENGINE` | `faster-whisper` | `faster-whisper` or `whisper-cpp` — selects the prepare path. |
| `LOCAL_TRANSCRIPTION_MODEL` | `small` | Checkpoint name → filename / repo id (must be in `ALLOWED_MODELS`). |
| `LOCAL_TRANSCRIPTION_QUANTIZATION` | `q4_0` | whisper.cpp only → `ggml-<model>-<quant>.bin`. Empty = unquantized. |
| `LOCAL_TRANSCRIPTION_MODEL_DIR` | `/models` | Where models live / are downloaded; HF cache root for faster-whisper. |
| `LOCAL_TRANSCRIPTION_MODEL_PATH` | (unset) | whisper.cpp explicit ggml path; wins over `model_dir/<filename>`. |
| `LOCAL_TRANSCRIPTION_AUTO_DOWNLOAD` | `false` | Gate for downloads. `false` → missing model = **not ready** (no fetch). `true` → `ensure_model` downloads. |
| `LOCAL_TRANSCRIPTION_COMPUTE_TYPE` | `int8` | faster-whisper precision (not a manager concern, passed through config). |
| `LOCAL_TRANSCRIPTION_THREADS` | `4` | CPU threads (not used by the manager). |
| `LOCAL_TRANSCRIPTION_LANGUAGE` | `auto` | Language hint (not used by the manager). |
| `WHISPER_CPP_BINARY` | (unset) | whisper.cpp `whisper-cli` path (worker-only, not used to prepare files). |
| `LOCAL_TRANSCRIPTION_DOC_URL` | repo docs URL | Friendly docs link in error messages. |

> `LOCAL_TRANSCRIPTION_AUTO_DOWNLOAD` is the single switch that decides
> download-vs-not-ready in `ensure_model`. It applies to **both** engines in the
> manager, but note whisper.cpp can only be auto-fetched via the manager (it has
> no lazy fallback), while faster-whisper also auto-downloads lazily at first use.

### Enabling it

```bash
# enable local transcription + allow model-init to download:
LOCAL_TRANSCRIPTION_ENABLED=true
LOCAL_TRANSCRIPTION_ENGINE=whisper-cpp        # or faster-whisper
LOCAL_TRANSCRIPTION_MODEL=small
LOCAL_TRANSCRIPTION_QUANTIZATION=q4_0         # whisper-cpp only
LOCAL_TRANSCRIPTION_AUTO_DOWNLOAD=true
```

```bash
docker compose --profile model-init run --rm model-init
# -> downloads ggml-small-q4_0.bin into ./models, exits 0
```

(For faster-whisper, the heavy dependency must be present in the image — build
with `--build-arg INSTALL_FASTER_WHISPER=true` — or the lazy
`huggingface_hub` import fails.)

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `model-init` exits `2` ("Model not ready") | Model missing and `LOCAL_TRANSCRIPTION_AUTO_DOWNLOAD=false`. | Set auto-download to `true` and re-run, or place the model file under `LOCAL_TRANSCRIPTION_MODEL_DIR` / `LOCAL_TRANSCRIPTION_MODEL_PATH` manually. |
| `model-init` exits `1` ("Model preparation failed") | `ModelManagerError`: unsupported model/quant (`UnknownModelError`) or a failed download (`ModelDownloadError`). | Check the value is in the allow-lists; verify network access to `huggingface.co` and free disk space. |
| `ModuleNotFoundError: huggingface_hub` during a faster-whisper download | The heavy dep is not in the image. | Rebuild with `--build-arg INSTALL_FASTER_WHISPER=true`. The default downloader imports it lazily. |
| Downloaded file but worker still says model invalid | whisper.cpp `LOCAL_TRANSCRIPTION_MODEL_PATH` / dir mismatch, or the `whisper-cli` binary is missing. | Confirm the file is at `expected_whisper_cpp_path(config)`; set `WHISPER_CPP_BINARY`. See [08-whisper-cpp.md](08-whisper-cpp.md). |
| `model-init` did nothing (exit `0`, no download) | `LOCAL_TRANSCRIPTION_ENABLED=false` (default). | Set it to `true`. |
| `docker compose up` skips model preparation | Expected — `model-init` is profile-gated and never runs on a plain `up`. | Run it explicitly with `--profile model-init`, or rely on the worker's runtime validation / faster-whisper lazy download. |
