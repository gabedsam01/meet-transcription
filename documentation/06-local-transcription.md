# Local Transcription (product decision)

Meet Transcription can transcribe Google Meet recordings **locally on CPU**
instead of sending audio to Deepgram. This document explains the product
decision behind that capability, the exact provider-selection rule the web UI
and worker both honor, the no-silent-fallback principle, the supported models
and languages, the MVP limitations, and the Deepgram-vs-local trade-off so you
can choose the right transcription posture for your deployment.

See also: [Architecture](01-architecture.md) for the service topology, and the
in-repo deep dive at
[`docs/architecture/local-transcription.md`](06-local-transcription.md).

## The product decision

Transcription is a cost and privacy concern. Deepgram is fast and supports
speaker diarization, but it is a paid per-minute API, and the audio leaves your
infrastructure. For deployments that prefer to keep audio in-house or avoid
per-minute billing, Meet Transcription offers a **local, CPU-only** transcription
path that runs entirely inside the **worker** service.

The decision is governed by a few firm principles:

- **PostgreSQL stays the single source of truth.** Redis is the queue/lock, the
  **web** service only validates and enqueues jobs, and the **worker** performs
  all transcription. Nothing is ever transcribed inside an HTTP request, and no
  GPU is used.
- **CPU-only and multiarch.** Both engines run on x86_64 and ARM64 without a GPU.
- **No silent fallback.** If a local engine is requested but misconfigured, the
  system surfaces a clear message rather than quietly switching providers or
  failing deep inside a job. (See [No silent fallback](#no-silent-fallback).)
- **Local is off by default.** `LOCAL_TRANSCRIPTION_ENABLED` defaults to `false`,
  so an out-of-the-box deployment uses Deepgram with per-user encrypted keys.

Two engines are supported, both CPU-only:

- **faster-whisper** — a CTranslate2 reimplementation of Whisper, running `int8`
  (or other compute types) on CPU. It can optionally auto-download models.
- **whisper.cpp** — the C++ Whisper implementation, driven through the external
  `whisper-cli` binary, fed 16 kHz mono WAV extracted with `ffmpeg`.

## Provider selection rule

The worker and the UI agree on a single rule, implemented in
`get_transcription_provider_status` (`app/transcription/provider.py`) and applied
when choosing the engine in `resolve_provider` (`app/transcription/factory.py`).
The rule has exactly three outcomes:

| `LOCAL_TRANSCRIPTION_ENABLED` | Local config | Result |
| --- | --- | --- |
| `false` | — (not evaluated) | **Deepgram.** A per-user Deepgram key is required, as today. |
| `true` | **valid** | **Local engine is used.** A Deepgram key is **not** required. The UI shows *"Modelo local ativo: &lt;engine model compute/quant&gt;"*. |
| `true` | **invalid** | **Deepgram is required.** The UI shows *"Modelo local inválido. Consulte a documentação de modelos locais."* plus a link to `LOCAL_TRANSCRIPTION_DOC_URL`. "Run once" is blocked unless a Deepgram key is configured. |

The status object (`ProviderStatus`) carries:

- `enabled` — whether local transcription is switched on,
- `local_valid` — whether a local engine is enabled **and** fully configured,
- `deepgram_required` — whether a per-user Deepgram key is needed to transcribe,
- `summary` — the human-readable engine description (e.g. the model and
  compute/quantization), present only when local is valid,
- `message` — the friendly, secret-free string shown in the UI,
- `doc_url` — the value of `LOCAL_TRANSCRIPTION_DOC_URL`, and
- `reason` — a technical reason a local engine is invalid, kept for
  logs/diagnostics and distinct from the friendly `message`.

### How `resolve_provider` applies the rule

`resolve_provider` (`app/transcription/factory.py`) picks the concrete provider:

1. **Valid local engine** → the local provider is built and used; no Deepgram key
   is needed.
2. **Local disabled or invalid, with a Deepgram key** → Deepgram is used. If the
   local engine was requested but is invalid, the worker logs the technical
   `reason` at INFO level (e.g. `Local transcription invalid; requiring Deepgram:
   reason=...`) so the choice is auditable.
3. **Local disabled or invalid, with no Deepgram key** → it raises
   `LocalTranscriptionUnavailableError` with a friendly, docs-linked message that
   mentions Deepgram. This is the run-once block, surfaced before any download or
   transcription is attempted.

## No silent fallback

There is **never** a silent provider switch. An invalid local configuration
always produces a clear message and the documentation link; it does not quietly
fall through to Deepgram without saying so, and it does not crash mid-job.

- When local is invalid **and** a Deepgram key exists, the worker uses Deepgram
  **and logs why** (the technical `reason`), so operators can see the fallback.
- When local is invalid **and** no Deepgram key exists, run-once is **blocked**
  up front with a friendly message instead of failing later in the pipeline.
  The error raised is `LocalTranscriptionUnavailableError`; its message is built
  by `_unavailable_message` and reads (Portuguese, as shown in the app):

  > Não há provedor de transcrição disponível: Modelo local inválido. Consulte a
  > documentação de modelos locais. Configure uma Deepgram API Key ou ajuste o
  > modelo local (&lt;LOCAL_TRANSCRIPTION_DOC_URL&gt;).

`app/transcription/factory.py` exposes `LocalTranscriptionUnavailable` as a
back-compat alias for `LocalTranscriptionUnavailableError`.

## Supported models

Both engines accept the same multilingual Whisper checkpoints:

```
tiny · base · small · medium · large-v1 · large-v2 · large-v3 · large-v3-turbo
```

Engine-specific precision settings:

| Setting | Engine | Allowed values | Default |
| --- | --- | --- | --- |
| `LOCAL_TRANSCRIPTION_COMPUTE_TYPE` | faster-whisper | `int8`, `int8_float32`, `float32` | `int8` |
| `LOCAL_TRANSCRIPTION_QUANTIZATION` | whisper.cpp | `q4_0`, `q4_1`, `q5_0`, `q5_1`, `q8_0` | — |

`int8` is the right default for CPU with faster-whisper. For whisper.cpp, the MVP
validates the five quantizations above; other quantizations (`q2_*`, `q3_*`,
`q6_*`) may exist in custom builds but are not part of the validated MVP set.

## Languages

Both engines run **multilingual** checkpoints, so a single model handles
**pt-BR and English**.

- **Do NOT use `.en` models.** They are English-only and cannot transcribe pt-BR.
- Set `LOCAL_TRANSCRIPTION_LANGUAGE=auto` to auto-detect the spoken language, or
  pin it explicitly with `pt` or `en` (or another supported code).

## Limitations (MVP)

The local path is CPU-bound and intentionally minimal for the MVP:

- **CPU-only.** No GPU acceleration is used by either engine.
- **Slower than Deepgram.** CPU transcription is roughly **~1× realtime or
  slower** — a 60-minute meeting can take an hour or more. Keep
  `WORKER_CONCURRENCY=1` and raise `STALE_JOB_TIMEOUT_MINUTES` for long
  recordings so the stale-job recovery does not reclaim an in-progress job.
- **No diarization.** The MVP does not split speakers; every normalized segment
  has `speaker = null`.

The normalized transcript saved in `transcripts.transcript_json` looks like this
(local engine, no diarization):

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

`transcripts.transcript_text` holds the human-readable `.txt` that the
**Download TXT** action serves — unchanged for Deepgram and consistently
formatted for the local engines.

## Deepgram vs. local — trade-offs

| | Deepgram | Local (faster-whisper / whisper.cpp) |
| --- | --- | --- |
| Diarization (speakers) | yes | no (MVP: `speaker = null`) |
| Speed | fast | slower (CPU-bound, ~1× realtime or worse) |
| Cost | per-minute | free after model download |
| Privacy | audio leaves your infrastructure | audio stays on your infrastructure |
| Key required | per-user Deepgram key | none when local is valid |

## Configuration quick reference

All variables are read at runtime by the **worker**. See `.env.example` for the
full list; the master switch and shared options are:

```bash
LOCAL_TRANSCRIPTION_ENABLED=false            # master switch (default false)
LOCAL_TRANSCRIPTION_ENGINE=faster-whisper    # faster-whisper | whisper-cpp
LOCAL_TRANSCRIPTION_MODEL=small
LOCAL_TRANSCRIPTION_LANGUAGE=auto            # auto | pt | en | ...
LOCAL_TRANSCRIPTION_THREADS=4
LOCAL_TRANSCRIPTION_MODEL_DIR=/models
LOCAL_TRANSCRIPTION_AUTO_DOWNLOAD=false      # only faster-whisper can auto-download
LOCAL_TRANSCRIPTION_DOC_URL=https://github.com/gabedsam01/meet-transcription/blob/main/docs/architecture/local-transcription.md
```

### faster-whisper

```bash
LOCAL_TRANSCRIPTION_ENABLED=true
LOCAL_TRANSCRIPTION_ENGINE=faster-whisper
LOCAL_TRANSCRIPTION_MODEL=small
LOCAL_TRANSCRIPTION_COMPUTE_TYPE=int8
```

The dependency is baked in at **build time**, not installed at runtime:

```bash
docker build --build-arg INSTALL_FASTER_WHISPER=true -t meet-transcription:fw .
```

With `LOCAL_TRANSCRIPTION_AUTO_DOWNLOAD=false` (default), the model must already
be present under `LOCAL_TRANSCRIPTION_MODEL_DIR` (mounted at `/models`); the
worker uses `local_files_only` and never reaches HuggingFace at job time. Set it
to `true` to allow a one-time download. Auto-download applies **only** to
faster-whisper.

### whisper.cpp

```bash
LOCAL_TRANSCRIPTION_ENABLED=true
LOCAL_TRANSCRIPTION_ENGINE=whisper-cpp
LOCAL_TRANSCRIPTION_MODEL=small
LOCAL_TRANSCRIPTION_QUANTIZATION=q4_0
LOCAL_TRANSCRIPTION_MODEL_PATH=/models/ggml-small-q4_0.bin
WHISPER_CPP_BINARY=/usr/local/bin/whisper-cli
```

whisper.cpp needs **ffmpeg** (for 16 kHz mono WAV extraction) and the external
`whisper-cli` binary. **`LOCAL_TRANSCRIPTION_MODEL_PATH` is always required** for
whisper.cpp — unlike faster-whisper it cannot auto-download a model, so
`LOCAL_TRANSCRIPTION_AUTO_DOWNLOAD` does not apply. Install ffmpeg by building
with:

```bash
docker build --build-arg INSTALL_WHISPER_CPP=true -t meet-transcription:wc .
```

> **Build note:** the image does **not** compile whisper.cpp itself (heavy and
> arch-specific). The engine is fully implemented in code and driven through the
> external `WHISPER_CPP_BINARY` — mount a prebuilt `whisper-cli` (and the
> `ggml-*.bin` model) into `/models` or into your image.

The Docker build args (`INSTALL_LOCAL_TRANSCRIPTION`, `INSTALL_FASTER_WHISPER`,
`INSTALL_WHISPER_CPP`) all default to `false` and are **build-time only**, not
runtime configuration.
