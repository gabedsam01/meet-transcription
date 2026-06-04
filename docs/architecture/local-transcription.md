# Local transcription (proposal — not implemented)

This document describes a **future** option to transcribe recordings locally
with [faster-whisper](https://github.com/SYSTRAN/faster-whisper) instead of
Deepgram. It is a design note only. **No Whisper code exists yet and none should
be added before the PostgreSQL architecture is stable.**

## Motivation

Deepgram is fast and accurate but is a paid, external service that receives the
raw meeting audio. A local engine would:

- remove per-minute transcription cost,
- keep audio on infrastructure you control (privacy), and
- allow offline / air-gapped operation.

The trade-off is CPU/RAM usage and slower, lower-accuracy transcription.

## Proposed shape

- A new optional service, **`local-transcriber`**, packaged like web/worker
  (same repo, possibly the same image with extra dependencies, or a dedicated
  image to keep the base image small).
- Engine: **faster-whisper** with a small model (`tiny` or `base`) using `int8`
  quantization for CPU inference. Larger models only on machines with a GPU.
- The **worker chooses the engine per job**: `deepgram` (default) or `local`,
  selected by configuration (e.g. a per-user setting or an env flag). The
  worker keeps owning download → transcribe → upload; only the transcribe step
  swaps implementations behind a common interface.

```
worker job:
  download MP4 from Drive
  engine = settings.engine  # "deepgram" | "local"
  transcript = engine.transcribe(mp4)
  upload transcript to Drive
```

## Risks and constraints

- **CPU/RAM**: even `tiny`/`base` int8 models use significant CPU and hundreds of
  MB to a few GB of RAM. Concurrent jobs can starve the host. The local engine
  must run in the worker (never in the web request) and likely needs a
  concurrency limit of 1.
- **Latency**: CPU transcription is much slower than Deepgram, especially for
  long meetings. Job timeouts and `STALE_JOB_TIMEOUT_MINUTES` need revisiting.
- **Image size / build time**: Whisper dependencies (and any model weights)
  inflate the image. Prefer a separate image or lazy model download.
- **Accuracy**: small models are weaker than Deepgram, particularly for
  Portuguese and multi-speaker audio (no diarization out of the box).

## Sequencing

Do this **only after** the PostgreSQL architecture (web + worker + postgres) is
stable and the worker reliably processes Deepgram jobs end to end. The engine
selection should reuse the same job model and settings storage, so it depends on
the database and worker work landing first.

## Out of scope for now

- No faster-whisper dependency in `requirements.txt`.
- No engine-selection setting in the UI or database.
- No `local-transcriber` service in `docker-compose.yml`.

These are intentionally deferred; this file just records the intended direction.
