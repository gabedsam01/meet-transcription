# whisper.cpp Engine

`whisper.cpp` is one of the two local CPU transcription engines supported by
meet-transcription (the other is documented in
[07-faster-whisper.md](07-faster-whisper.md)). When enabled and valid, it lets
the worker transcribe Google Meet recordings entirely on-host with no Deepgram
API key. This document is the operational reference for installing, configuring,
and troubleshooting the engine.

For how local transcription fits into the overall system, see
[Architecture](01-architecture.md) and
[Local Transcription](06-local-transcription.md). For the rule that decides
between Deepgram and a local engine, see the *Provider rule* section below.

Implementation lives in:

- `app/transcription/whisper_cpp_provider.py` — the provider (subprocess driver).
- `app/transcription/audio.py` — the ffmpeg WAV extraction command builder.
- `app/transcription/local_validation.py` — the validation probes.
- `app/transcription/factory.py` — `build_local_provider` / `resolve_provider`.

---

## How it works

Unlike `faster-whisper` (which decodes media itself via PyAV and can
auto-download models from HuggingFace), the `whisper.cpp` engine is driven as an
**external subprocess**. The provider does not link against any library — it
shells out to the `whisper-cli` binary you supply via `WHISPER_CPP_BINARY`.

The pipeline for a single job is:

1. **Audio extraction.** The job's MP4 is decoded to a **16 kHz mono PCM WAV**
   using `ffmpeg`. whisper.cpp only accepts 16 kHz mono PCM WAV input, so this
   step is mandatory.
2. **Transcription.** `whisper-cli` is invoked on the WAV with JSON output
   (`-oj`) written to a per-job scratch directory.
3. **Parsing.** The JSON is parsed into normalized segments (millisecond offsets
   become seconds). If JSON is unavailable, the provider falls back to a `.txt`
   sidecar, and finally to the process `stdout`.
4. **Normalization + cleanup.** The result is converted to the normalized
   transcript schema, the human-readable `.txt` is rendered, and the scratch
   directory is removed.

### Scratch directory

The provider writes everything under a `whispercpp` subfolder of the source
file's directory — in the worker this is `TMP_DIR/jobs/<job_id>/whispercpp`. It
contains:

| File | Purpose |
| --- | --- |
| `audio.wav` | The 16 kHz mono WAV produced by ffmpeg. |
| `out.json` | whisper-cli JSON output (`-oj`, prefix `out`). |
| `out.txt` | Optional `.txt` sidecar (fallback parsing). |

The whole `whispercpp` directory is **always removed** in a `finally` block,
even on failure (`shutil.rmtree(workdir, ignore_errors=True)`).

### ffmpeg command

The exact ffmpeg command is built by `build_ffmpeg_command` in
`app/transcription/audio.py`:

```bash
ffmpeg -nostdin -y -i <source.mp4> -ar 16000 -ac 1 -c:a pcm_s16le <dest.wav>
```

- `-ar 16000` — 16 kHz sample rate.
- `-ac 1` — mono (1 channel).
- `-c:a pcm_s16le` — signed 16-bit little-endian PCM.

If ffmpeg returns a non-zero exit code, the provider raises a `RuntimeError`
with the first 500 chars of stderr (`ffmpeg failed to extract audio`).

### whisper-cli command

`_build_command` in `whisper_cpp_provider.py` assembles the call:

```bash
<WHISPER_CPP_BINARY> \
  -m <LOCAL_TRANSCRIPTION_MODEL_PATH> \
  -f <audio.wav> \
  -t <LOCAL_TRANSCRIPTION_THREADS> \
  -l <LOCAL_TRANSCRIPTION_LANGUAGE> \
  -oj \
  -of <out_prefix>
```

| Flag | Source env var | Notes |
| --- | --- | --- |
| `-m` | `LOCAL_TRANSCRIPTION_MODEL_PATH` | Path to the ggml model file. **Always required.** |
| `-f` | (internal) | The extracted `audio.wav`. |
| `-t` | `LOCAL_TRANSCRIPTION_THREADS` | CPU threads. |
| `-l` | `LOCAL_TRANSCRIPTION_LANGUAGE` | `auto`, `pt`, `en`, … (`auto` passed through verbatim). |
| `-oj` | — | Emit JSON output. |
| `-of` | (internal) | Output file prefix (`<job_dir>/whispercpp/out`). |

If `WHISPER_CPP_BINARY` is empty the provider falls back to `whisper-cli` on
`PATH`.

### Output parsing (`-oj` JSON, txt fallback)

`_parse_output` reads results in this order of preference:

1. **`out.json`** — each item under `transcription[]` becomes a segment. The
   `offsets.from` / `offsets.to` values are in **milliseconds** and are divided
   by `1000.0` to produce second-precision `start`/`end`. Empty-text items are
   skipped. The language comes from `result.language`.
2. **`out.txt`** — if no usable JSON, the `.txt` sidecar (if present) is read as
   a single segment.
3. **`stdout`** — if neither file exists, whatever `whisper-cli` printed to
   stdout becomes a single segment.

The normalized payload is emitted with `provider="local"` and
`engine="whisper-cpp"`. The local MVP has **no diarization**, so every segment's
`speaker` is `null`.

---

## Models

The engine ships ggml model files; you choose which model to load with
`LOCAL_TRANSCRIPTION_MODEL` (the logical name) and `LOCAL_TRANSCRIPTION_MODEL_PATH`
(the actual file on disk). Supported model names (shared with `faster-whisper`):

```
tiny  base  small  medium  large-v1  large-v2  large-v3  large-v3-turbo
```

> **Use multilingual models only.** This deployment needs both pt-BR and
> English, so do **not** use `.en` (English-only) model variants.

### Quantizations

whisper.cpp ggml models can be quantized to trade accuracy for size/speed.
`LOCAL_TRANSCRIPTION_QUANTIZATION` is validated against:

```
q4_0  q4_1  q5_0  q5_1  q8_0
```

The quantization is a label for the model you point `LOCAL_TRANSCRIPTION_MODEL_PATH`
at (it must match the file you actually downloaded/built). It is surfaced in the
UI status string `whisper.cpp <model> <quantization>` and used by validation —
it is **not** passed to `whisper-cli` as a flag. The full status summary appears
on the dashboard as `Modelo local ativo: whisper.cpp <model> <quant>`.

---

## No auto-download — `MODEL_PATH` is always required

whisper.cpp **cannot fetch a ggml model itself**. Auto-download
(`LOCAL_TRANSCRIPTION_AUTO_DOWNLOAD`, default `false`) only applies to
`faster-whisper`, which can pull from HuggingFace. For whisper.cpp you must
provision the model file yourself and point `LOCAL_TRANSCRIPTION_MODEL_PATH` at
it.

This is enforced in two places:

- **Validation** (`local_validation.py`): `LOCAL_TRANSCRIPTION_MODEL_PATH` must
  be set and exist on disk, otherwise the config is invalid:
  `Arquivo de modelo whisper.cpp ausente (LOCAL_TRANSCRIPTION_MODEL_PATH).`
- **The provider** (`whisper_cpp_provider.py`): even if validation is bypassed,
  `transcribe()` raises `ModelNotFoundError` rather than building a broken
  `-m <empty>` command:
  `whisper.cpp requires LOCAL_TRANSCRIPTION_MODEL_PATH (no auto-download).`

In Docker, the `./models` directory is mounted **read-only** into both `web` and
`worker` (`./models:/models:ro`), and `LOCAL_TRANSCRIPTION_MODEL_DIR` defaults to
`/models`. Place your ggml file under `./models` on the host and reference it by
its in-container path.

---

## Environment variables

Ready-to-paste `.env` block for the whisper.cpp engine (place the ggml file under
`./models` on the host so it appears at `/models/...` in the containers):

```dotenv
# --- Local transcription: whisper.cpp ---
LOCAL_TRANSCRIPTION_ENABLED=true
LOCAL_TRANSCRIPTION_ENGINE=whisper-cpp
LOCAL_TRANSCRIPTION_MODEL=large-v3-turbo
LOCAL_TRANSCRIPTION_LANGUAGE=auto
LOCAL_TRANSCRIPTION_THREADS=4
LOCAL_TRANSCRIPTION_MODEL_DIR=/models
LOCAL_TRANSCRIPTION_QUANTIZATION=q5_0

# whisper.cpp REQUIRES the model file path (no auto-download):
LOCAL_TRANSCRIPTION_MODEL_PATH=/models/ggml-large-v3-turbo-q5_0.bin

# Path to the external whisper-cli binary:
WHISPER_CPP_BINARY=/usr/local/bin/whisper-cli

# Auto-download is faster-whisper only; keep false for whisper.cpp:
LOCAL_TRANSCRIPTION_AUTO_DOWNLOAD=false

# Shown in the UI when the local config is invalid:
LOCAL_TRANSCRIPTION_DOC_URL=https://example.com/docs/local-transcription
```

Relevant variables:

| Variable | Applies to | Meaning |
| --- | --- | --- |
| `LOCAL_TRANSCRIPTION_ENABLED` | both engines | `true` to use a local engine instead of Deepgram. Default `false`. |
| `LOCAL_TRANSCRIPTION_ENGINE` | both engines | `whisper-cpp` to select this engine (`faster-whisper` otherwise). |
| `LOCAL_TRANSCRIPTION_MODEL` | both engines | Logical model name (see model list). |
| `LOCAL_TRANSCRIPTION_LANGUAGE` | both engines | `auto`, `pt`, `en`, … |
| `LOCAL_TRANSCRIPTION_THREADS` | both engines | CPU threads (`-t`). |
| `LOCAL_TRANSCRIPTION_MODEL_DIR` | both engines | Model directory, default `/models`. |
| `LOCAL_TRANSCRIPTION_QUANTIZATION` | whisper.cpp | `q4_0 / q4_1 / q5_0 / q5_1 / q8_0`. |
| `LOCAL_TRANSCRIPTION_MODEL_PATH` | whisper.cpp | **Required.** Path to the ggml model file. |
| `WHISPER_CPP_BINARY` | whisper.cpp | Path to the external `whisper-cli` binary. |
| `LOCAL_TRANSCRIPTION_AUTO_DOWNLOAD` | faster-whisper only | Default `false`; ignored by whisper.cpp. |
| `LOCAL_TRANSCRIPTION_DOC_URL` | both engines | Link surfaced in the UI when the config is invalid. |

`LOCAL_TRANSCRIPTION_COMPUTE_TYPE` (int8 / int8_float32 / float32) is a
**faster-whisper** setting and has no effect on whisper.cpp.

---

## Build args: `INSTALL_WHISPER_CPP`

These are **Docker build args, not runtime env vars**. All default to `false`:

| Build arg | Effect |
| --- | --- |
| `INSTALL_LOCAL_TRANSCRIPTION` | Umbrella flag for local-transcription deps. |
| `INSTALL_FASTER_WHISPER` | `pip install faster-whisper`. |
| `INSTALL_WHISPER_CPP` | **`apt-get install ffmpeg` only.** |

> **Important:** `INSTALL_WHISPER_CPP` installs **only ffmpeg** into the image.
> It does **not** compile or install the `whisper-cli` binary. The binary is
> **external** — you provide it yourself and point `WHISPER_CPP_BINARY` at it
> (e.g. mount a host-built binary or bake it in via a custom Dockerfile stage).

Example build:

```bash
docker compose build --build-arg INSTALL_WHISPER_CPP=true
```

### Roadmap note

Full multi-architecture compilation of `whisper-cli` into the shared image is a
**roadmap item**, not a current feature. Today the image only guarantees ffmpeg;
the `whisper-cli` binary must be supplied externally via `WHISPER_CPP_BINARY`.

---

## Validation and the provider rule

`validate_local_config` → `_validate_whisper_cpp` checks, in order:

1. `LOCAL_TRANSCRIPTION_MODEL` is in the supported model list.
2. `LOCAL_TRANSCRIPTION_QUANTIZATION` is in the allowed set.
3. `WHISPER_CPP_BINARY` is set **and** executable.
4. `LOCAL_TRANSCRIPTION_MODEL_PATH` is set **and** the file exists.

If all pass, the engine is reported valid with summary
`whisper.cpp <model> <quantization>`.

The provider rule (`get_transcription_provider_status` /
`resolve_provider`) then decides what the system uses:

| State | Behavior |
| --- | --- |
| `LOCAL_TRANSCRIPTION_ENABLED=false` | Deepgram is used; a per-user key is required. |
| Enabled **and** valid | The local engine is used; **no Deepgram key required**. UI shows `Modelo local ativo: whisper.cpp <model> <quant>`. |
| Enabled **but** invalid | Deepgram is required. UI shows `Modelo local inválido. Consulte a documentação de modelos locais.` plus a link to `LOCAL_TRANSCRIPTION_DOC_URL`. *Run once* is blocked unless a Deepgram key is set. **No silent fallback.** |

---

## Common errors

| Symptom / message | Cause | Fix |
| --- | --- | --- |
| `Binário whisper.cpp ausente ou não executável (WHISPER_CPP_BINARY).` (validation) | `WHISPER_CPP_BINARY` is unset, points at a missing file, or the file is not executable. | Set `WHISPER_CPP_BINARY` to a real, `+x` `whisper-cli` binary. |
| `WhisperCppBinaryNotFoundError: whisper.cpp binary not found: '<path>'` (runtime) | The subprocess raised `FileNotFoundError` — the binary was gone at transcribe time. | Verify the binary path inside the container; remember the image does not ship `whisper-cli`. |
| `Arquivo de modelo whisper.cpp ausente (LOCAL_TRANSCRIPTION_MODEL_PATH).` (validation) | `LOCAL_TRANSCRIPTION_MODEL_PATH` is unset or the file does not exist. | Provision the ggml file under `./models` and set the correct `/models/...` path. |
| `ModelNotFoundError: whisper.cpp requires LOCAL_TRANSCRIPTION_MODEL_PATH (no auto-download).` (runtime) | `transcribe()` was reached with no model path. | Set `LOCAL_TRANSCRIPTION_MODEL_PATH`; remember whisper.cpp never auto-downloads. |
| `RuntimeError: ffmpeg failed to extract audio (rc=...)` | ffmpeg missing, not on `PATH`, or rejected the input. | Build with `INSTALL_WHISPER_CPP=true` (installs ffmpeg), or install ffmpeg in the image; check the source MP4 is valid. |
| `RuntimeError: whisper.cpp failed (rc=...): <stderr>` | `whisper-cli` exited non-zero (bad model file, wrong quantization, corrupt WAV). | Inspect the stderr snippet; confirm the model file matches `LOCAL_TRANSCRIPTION_QUANTIZATION` and the WAV is 16 kHz mono. |

All of these surface to the user as a **friendly, secret-free `user_message`**
stored in `transcription_jobs.error_message` — full tracebacks stay in the
worker logs only. The error classes (`ModelNotFoundError`,
`WhisperCppBinaryNotFoundError`, etc.) live in `app/errors.py`.

### Quick host check

Before enabling the engine, confirm both prerequisites are present where the
worker runs:

```bash
# ffmpeg available?
ffmpeg -version

# whisper-cli present and executable at the configured path?
test -x "$WHISPER_CPP_BINARY" && echo "binary OK" || echo "binary MISSING"

# model file present at the configured path?
test -f "$LOCAL_TRANSCRIPTION_MODEL_PATH" && echo "model OK" || echo "model MISSING"
```

---

## See also

- [Architecture](01-architecture.md)
- [Local Transcription](06-local-transcription.md)
- [faster-whisper Engine](07-faster-whisper.md)
