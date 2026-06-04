# Local transcription (CPU-only)

Meet Transcription can transcribe recordings **locally on CPU** instead of sending
audio to Deepgram. Two engines are supported, both CPU-only and multiarch
(x86_64 + ARM64):

- **faster-whisper** — CTranslate2 reimplementation of Whisper, `int8` on CPU.
- **whisper.cpp** — C++ Whisper, driven via the `whisper-cli` binary.

PostgreSQL stays the single source of truth, Redis is the queue/lock, the **web**
service only enqueues jobs, and the **worker** does all transcription. Nothing is
ever transcribed inside an HTTP request, and no GPU is used.

## Provider selection rule

The worker and UI agree on one rule (`get_transcription_provider_status`):

| `LOCAL_TRANSCRIPTION_ENABLED` | Local config | Result |
| --- | --- | --- |
| `false` | — | **Deepgram** (per-user key required, as today). |
| `true`  | **valid**   | **Local engine** used. Deepgram key **not** required. |
| `true`  | **invalid** | **Deepgram required.** UI shows *"Modelo local inválido. Consulte a documentação de modelos locais."* with a link here, and "Run once" is blocked until a Deepgram key is configured. |

There is **no silent fallback**: an invalid local configuration always surfaces a
clear message; if there is also no Deepgram key, run-once is blocked with a
friendly message instead of failing later.

## Languages

Both engines run **multilingual** checkpoints so the same model handles **pt-BR
and English**. Do **not** use `.en` models — they are English-only. Set
`LOCAL_TRANSCRIPTION_LANGUAGE=auto` to auto-detect, or pin `pt` / `en`.

## Supported models (both engines)

```
tiny · base · small · medium · large-v1 · large-v2 · large-v3 · large-v3-turbo
```

## whisper.cpp quantizations

MVP-supported: `q4_0`, `q4_1`, `q5_0`, `q5_1`, `q8_0`.

> `q2_*`, `q3_*`, `q6_*` may exist in custom whisper.cpp builds, but the official
> MVP only validates the five quantizations above.

## faster-whisper compute types (CPU)

`int8` (default), `int8_float32`, `float32`. `int8` is the right default for CPU.

## VPS recommendations

| Tier | Hardware | faster-whisper | whisper.cpp |
| --- | --- | --- | --- |
| **Minimum**     | 4 GB RAM, 1–2 vCPU   | `base`/`small` `int8`        | `base`/`small` `q4_0`        |
| **Recommended** | 8 GB RAM, 4 vCPU     | `small`/`medium` `int8`      | `small`/`medium` `q5_0`      |
| **Comfortable** | 16–24 GB RAM, 4+ vCPU| `medium`/`large-v3-turbo` `int8` | `medium`/`large` `q5_0`/`q8_0` |

CPU transcription is roughly **~1× realtime or slower** — a 60-minute meeting can
take an hour or more. Keep `WORKER_CONCURRENCY=1` and raise
`STALE_JOB_TIMEOUT_MINUTES` for long recordings.

## Deepgram vs local — trade-offs

| | Deepgram | Local (faster-whisper / whisper.cpp) |
| --- | --- | --- |
| Diarization (speakers) | ✅ yes | ❌ no (MVP: `speaker = null`) |
| Speed | fast | slower (CPU-bound) |
| Cost | per-minute | free after model download |
| Privacy | audio leaves your infra | audio stays on your infra |

## Configuration

All variables (see `.env.example`):

```bash
LOCAL_TRANSCRIPTION_ENABLED=false            # master switch
LOCAL_TRANSCRIPTION_ENGINE=faster-whisper    # faster-whisper | whisper-cpp
LOCAL_TRANSCRIPTION_MODEL=small
LOCAL_TRANSCRIPTION_LANGUAGE=auto            # auto | pt | en | ...
LOCAL_TRANSCRIPTION_THREADS=4
LOCAL_TRANSCRIPTION_MODEL_DIR=/models
LOCAL_TRANSCRIPTION_AUTO_DOWNLOAD=false      # do NOT fetch models at runtime unless true
LOCAL_TRANSCRIPTION_DOC_URL=https://github.com/gabedsam01/meet-transcription/blob/main/docs/architecture/local-transcription.md
```

### faster-whisper

```bash
LOCAL_TRANSCRIPTION_ENABLED=true
LOCAL_TRANSCRIPTION_ENGINE=faster-whisper
LOCAL_TRANSCRIPTION_MODEL=small
LOCAL_TRANSCRIPTION_COMPUTE_TYPE=int8
```

Build an image with the dependency baked in (it is **not** installed at runtime):

```bash
docker build --build-arg INSTALL_FASTER_WHISPER=true -t meet-transcription:fw .
```

With `LOCAL_TRANSCRIPTION_AUTO_DOWNLOAD=false` (default) the model must already be
present under `LOCAL_TRANSCRIPTION_MODEL_DIR` (mounted at `/models`); the worker
uses `local_files_only` and never reaches out to HuggingFace at job time. Set it
to `true` to allow a one-time download.

### whisper.cpp

```bash
LOCAL_TRANSCRIPTION_ENABLED=true
LOCAL_TRANSCRIPTION_ENGINE=whisper-cpp
LOCAL_TRANSCRIPTION_MODEL=small
LOCAL_TRANSCRIPTION_QUANTIZATION=q4_0
LOCAL_TRANSCRIPTION_MODEL_PATH=/models/ggml-small-q4_0.bin
WHISPER_CPP_BINARY=/usr/local/bin/whisper-cli
```

whisper.cpp needs **ffmpeg** (16 kHz mono WAV extraction) and the `whisper-cli`
binary. ffmpeg is installed when you build with:

```bash
docker build --build-arg INSTALL_WHISPER_CPP=true -t meet-transcription:wc .
```

> **Build status:** this image does **not** compile whisper.cpp itself (heavy and
> arch-specific). The engine is fully implemented in code and driven through an
> **external `WHISPER_CPP_BINARY`** — mount a prebuilt `whisper-cli` (and the
> `ggml-*.bin` model) into `/models` / your image. Compiling whisper.cpp into a
> dedicated multiarch image is the documented next step.

## How it is stored

Every engine normalizes into one internal schema saved in
`transcripts.transcript_json`:

```json
{
  "provider": "local",
  "engine": "faster-whisper",
  "model": "small",
  "language": "pt",
  "text": "…",
  "segments": [{"start": 0.0, "end": 3.2, "speaker": null, "text": "…"}],
  "words": [],
  "utterances": [],
  "raw": {}
}
```

`transcripts.text` holds the human-readable `.txt` (the TXT download is unchanged
for Deepgram and consistently formatted for local engines).
