# faster-whisper Engine

`faster-whisper` is the default **local CPU transcription engine** for
meet-transcription. When local transcription is enabled and valid, the worker
transcribes Google Meet MP4 recordings entirely on the host CPU — no Deepgram
key is required for those jobs. This page documents exactly how the engine is
configured, validated, and operated, based on
`app/transcription/faster_whisper_provider.py`,
`app/transcription/config.py`, and `app/transcription/local_validation.py`.

For the bigger picture, see [Architecture](01-architecture.md). For the
alternative local engine, see [whisper.cpp Engine](08-whisper-cpp.md). For how
the provider is chosen at runtime (local vs Deepgram), see
[Transcription Providers](06-local-transcription.md).

## Where it runs

`faster-whisper` runs **inside the worker** (`python -m app.worker.main`),
never in an HTTP request. The web service only validates settings and creates a
pending job. The worker then resolves the provider, downloads the MP4 from
Google Drive, and calls the engine.

The implementation is `FasterWhisperProvider` in
`app/transcription/faster_whisper_provider.py`. The heavy `WhisperModel` class
is imported **lazily** — importing the module never requires the
`faster_whisper` package, and the model is built only once on first use.

## How the model is built

On the first transcription, `FasterWhisperProvider._build_model()` constructs a
`faster_whisper.WhisperModel` with these fixed and configurable parameters:

```python
WhisperModel(
    self._config.model,                 # LOCAL_TRANSCRIPTION_MODEL
    device="cpu",                        # always CPU — no GPU path
    compute_type=self._config.compute_type,   # LOCAL_TRANSCRIPTION_COMPUTE_TYPE
    cpu_threads=self._config.threads,    # LOCAL_TRANSCRIPTION_THREADS
    download_root=self._config.model_dir,     # LOCAL_TRANSCRIPTION_MODEL_DIR
    local_files_only=not self._config.auto_download,  # see below
)
```

Key facts:

| Aspect | Behavior |
| --- | --- |
| Device | Always `device="cpu"`. There is no GPU code path in this project. |
| Default compute type | `int8` (set in `config.py` when `LOCAL_TRANSCRIPTION_COMPUTE_TYPE` is empty). |
| CPU threads | `cpu_threads` from `LOCAL_TRANSCRIPTION_THREADS` (default `4`; any value `<= 0` or non-numeric falls back to `4`). |
| Model cache directory | `download_root` from `LOCAL_TRANSCRIPTION_MODEL_DIR` (default `/models`, mounted read-only in Docker). |
| Offline mode | `local_files_only=not auto_download`. With `LOCAL_TRANSCRIPTION_AUTO_DOWNLOAD=false` (the default), `local_files_only=True` — the engine never contacts Hugging Face at runtime and uses only cached models. |

### Auto-download (`local_files_only`)

`faster-whisper` is the **only** engine that can auto-download a model
(whisper.cpp cannot). The download behavior is controlled by
`LOCAL_TRANSCRIPTION_AUTO_DOWNLOAD`:

- `LOCAL_TRANSCRIPTION_AUTO_DOWNLOAD=false` (default) →
  `local_files_only=True`. The model **must already exist** under
  `download_root` (`/models`). If it is not cached, building the model fails.
- `LOCAL_TRANSCRIPTION_AUTO_DOWNLOAD=true` → `local_files_only=False`. On first
  use, the engine fetches the model from Hugging Face into `download_root`.

For production, pre-cache the model and keep `AUTO_DOWNLOAD=false` so the worker
stays fully offline at runtime.

## Transcription behavior

`FasterWhisperProvider.transcribe()` calls the model with:

```python
model.transcribe(str(source_path), language=language, beam_size=5, vad_filter=True)
```

- `language` is `None` when `LOCAL_TRANSCRIPTION_LANGUAGE=auto` (the default),
  letting the model auto-detect; otherwise the configured language code (e.g.
  `pt`, `en`) is passed through.
- `beam_size=5` and `vad_filter=True` (voice-activity-detection filtering) are
  fixed.
- Empty/whitespace-only segments are dropped.

The result is normalized into the project's transcript schema
(`provider="local"`, `engine="faster-whisper"`, the configured `model`, the
detected `language`, joined `text`, and `segments` with `start`/`end`/`text`).
The local MVP has **no diarization**, so `speaker` is `null`. A human-readable
`.txt` is rendered via `render_local_text` and is what the **Download TXT**
button serves.

## Supported models

Multilingual checkpoints only — `.en` models are intentionally **not**
supported because the project needs pt-BR *and* English
(`ALLOWED_MODELS` in `config.py`):

| Model | Notes |
| --- | --- |
| `tiny` | Fastest, lowest accuracy. |
| `base` | |
| `small` | **Default** when `LOCAL_TRANSCRIPTION_MODEL` is empty. |
| `medium` | |
| `large-v1` | |
| `large-v2` | |
| `large-v3` | Highest accuracy, slowest on CPU. |
| `large-v3-turbo` | Faster large-class model. |

Any value outside this list makes the local config **invalid** (see
Validation), which forces Deepgram for that job.

## Compute types (CPU)

`faster-whisper` compute types are CPU-appropriate only (no GPU `float16`)
— `ALLOWED_COMPUTE_TYPES` in `config.py`:

| `LOCAL_TRANSCRIPTION_COMPUTE_TYPE` | Notes |
| --- | --- |
| `int8` | **Default.** Smallest memory footprint, fastest on CPU. |
| `int8_float32` | Mixed precision; higher accuracy than pure `int8`. |
| `float32` | Full precision; slowest and most memory-heavy on CPU. |

An unsupported compute type makes the config invalid.

## Ready-to-paste env example

A minimal, offline-by-default faster-whisper configuration for the
`worker` (and `web`, which reads the same vars to show status):

```dotenv
# --- Local transcription (faster-whisper, CPU) ---
LOCAL_TRANSCRIPTION_ENABLED=true
LOCAL_TRANSCRIPTION_ENGINE=faster-whisper
LOCAL_TRANSCRIPTION_MODEL=small
LOCAL_TRANSCRIPTION_LANGUAGE=auto
LOCAL_TRANSCRIPTION_THREADS=4
LOCAL_TRANSCRIPTION_MODEL_DIR=/models
LOCAL_TRANSCRIPTION_COMPUTE_TYPE=int8
LOCAL_TRANSCRIPTION_AUTO_DOWNLOAD=false
# Optional: link shown in the UI when the local model is invalid
# LOCAL_TRANSCRIPTION_DOC_URL=https://github.com/gabedsam01/meet-transcription/blob/main/docs/architecture/local-transcription.md
```

The `quantization`, `model_path`, and `whisper_cpp_binary` variables are
**whisper.cpp-only** and are ignored by faster-whisper.

> The `faster_whisper` Python package must be present in the image — it is gated
> by the **build arg** `INSTALL_FASTER_WHISPER` (see below), not by a runtime
> env var.

### Build the image with faster-whisper installed

`INSTALL_FASTER_WHISPER` is a **Docker build argument** (default `false`), not a
runtime variable. When `true`, the Dockerfile `pip install`s `faster-whisper`
into the shared web/worker image:

```bash
docker compose build --build-arg INSTALL_FASTER_WHISPER=true
```

Both `web` and `worker` mount `./models:/models:ro`, so place pre-downloaded
model files under the host `./models` directory.

## How validation works

The provider is only used if the local config is **valid**. Validation lives in
`app/transcription/local_validation.py` and is what
`get_transcription_provider_status` / `resolve_provider` rely on to decide
between the local engine and Deepgram.

For `faster-whisper`, `validate_local_config()` performs these checks in order
(`_validate_faster_whisper`):

1. **Engine** must be in `ALLOWED_ENGINES`
   (`faster-whisper`, `whisper-cpp`). Otherwise:
   `Engine de transcrição local não suportado: ...`
2. **Model** must be in `ALLOWED_MODELS`. Otherwise:
   `Modelo local não suportado: ...`
3. **Compute type** must be in `ALLOWED_COMPUTE_TYPES`. Otherwise:
   `compute_type não suportado para CPU: ...`
4. **Package presence** — `faster_whisper` must be importable. The probe is
   `importlib.util.find_spec("faster_whisper") is not None`. If missing:
   `O pacote faster-whisper não está instalado nesta imagem (INSTALL_FASTER_WHISPER=true).`

When all checks pass, validation returns:

```text
valid=True, summary="faster-whisper <model> <compute_type>"   # e.g. "faster-whisper small int8"
```

That summary is what the dashboard surfaces as **"Modelo local ativo:
faster-whisper small int8"**.

> Validation checks that the **package** is installed; it does **not** check
> that the model file is cached on disk. A missing cached model surfaces only at
> transcription time (see Common problems).

### What happens when invalid

There is **no silent fallback**. If the local config is invalid while
`LOCAL_TRANSCRIPTION_ENABLED=true`:

- The provider falls back to **Deepgram**, which then requires a per-user
  encrypted Deepgram key.
- The UI shows **"Modelo local inválido. Consulte a documentação de modelos
  locais."** plus a link to `LOCAL_TRANSCRIPTION_DOC_URL`.
- **Run once** is blocked unless a Deepgram key is set.

## Common problems

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Dashboard says **"Modelo local inválido"** and mentions the package not installed | `faster_whisper` is not in the image (`find_spec("faster_whisper")` is `None`). The message is `O pacote faster-whisper não está instalado nesta imagem (INSTALL_FASTER_WHISPER=true).` | Rebuild the image with `docker compose build --build-arg INSTALL_FASTER_WHISPER=true`, then recreate the worker/web containers. |
| Job **fails at transcription time** even though validation passed | Model not cached and `LOCAL_TRANSCRIPTION_AUTO_DOWNLOAD=false` → `local_files_only=True`, so `WhisperModel` can't find the model under `download_root` (`/models`). | Pre-download the model into `./models`, or temporarily set `LOCAL_TRANSCRIPTION_AUTO_DOWNLOAD=true` for a one-time fetch. |
| `Modelo local não suportado: ...` | `LOCAL_TRANSCRIPTION_MODEL` is not in `ALLOWED_MODELS` (e.g. an `.en` variant). | Use a supported multilingual model (`tiny`…`large-v3-turbo`). |
| `compute_type não suportado para CPU: ...` | `LOCAL_TRANSCRIPTION_COMPUTE_TYPE` is not `int8` / `int8_float32` / `float32`. | Set a supported CPU compute type (default `int8`). |
| Transcription is **very slow** | CPU-only inference; large models (`large-v3`) and full precision (`float32`) are heavy. Too few `cpu_threads` also hurts. | Use a smaller model (`small`/`base`), keep `int8`, and raise `LOCAL_TRANSCRIPTION_THREADS` toward the host's physical core count. |
| First job after deploy stalls/downloads unexpectedly | `LOCAL_TRANSCRIPTION_AUTO_DOWNLOAD=true` triggers a Hugging Face download on first use. | Pre-cache models and keep `AUTO_DOWNLOAD=false` so the worker stays offline at runtime. |

### Notes on slowness

This engine is **CPU-only by design** (`device="cpu"`). Performance scales with:

- **Model size** — `tiny`/`base`/`small` are far faster than `large-*`.
- **Compute type** — `int8` is the fastest; `float32` is the slowest.
- **Threads** — set `LOCAL_TRANSCRIPTION_THREADS` to match available CPU cores.

If accuracy/speed on CPU is unacceptable, the alternatives are the
[whisper.cpp Engine](08-whisper-cpp.md) or keeping
`LOCAL_TRANSCRIPTION_ENABLED=false` and using Deepgram per user.
