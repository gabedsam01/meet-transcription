# Speaker Diarization (optional, local)

Meet Transcription can optionally label **who spoke when** — speaker
diarization — by running a local, CPU-only `pyannote.audio` pipeline **after**
transcription and tagging each transcript segment with a speaker. It is **OFF by
default**, gated behind both an env switch and a heavy Docker build arg, and
imported lazily so it has **zero impact** on the default Drive + Deepgram path.

This document explains the product posture, the config/env table, the
enabled-but-invalid rule, how speakers are assigned to segments, how the `.txt`
download changes, and how to enable and troubleshoot it.

See also: [Local Transcription](06-local-transcription.md) for the CPU
transcription engines, and [Security](16-security.md) for secret hygiene.

## What it is

By default a local transcript has no speakers — every normalized segment has
`speaker = null`. With diarization enabled and valid, the worker:

1. runs the transcription provider as usual, then
2. extracts a 16 kHz mono WAV from the media,
3. runs the `pyannote.audio` pipeline to produce **speaker turns**
   (`[start, end) → label`),
4. assigns each transcript segment the speaker of the **maximally overlapping**
   turn, and
5. **re-renders the `.txt`** so the download shows `Speaker <label>` lines.

The code lives in `app/diarization/` (`config.py`, `provider.py`,
`none_provider.py`, `pyannote_provider.py`, `align.py`, `errors.py`) and is wired
into the worker by `JobProcessor._apply_diarization`
(`app/worker/processor.py`).

Firm principles, consistent with the rest of the project:

- **OFF by default.** `DIARIZATION_ENABLED` defaults to `false` and
  `DIARIZATION_ENGINE` defaults to `none`. An out-of-the-box deployment never
  diarizes.
- **CPU-only, no GPU.** `pyannote.audio` (and the `torch` it pulls in) runs on
  CPU only.
- **Lazy + build-arg gated.** `pyannote.audio` / `torch` are **not** in the base
  image; they are installed only with `INSTALL_PYANNOTE=true` at build time and
  imported lazily inside the provider, so importing the diarization modules never
  requires the package.
- **No secret leakage.** The Hugging Face access token is a secret and never
  appears in logs, errors, the UI, or stored transcripts (see
  [Security note](#security-note)).
- **Runs after transcription** and, when enabled and valid, **overrides any
  Deepgram-native speakers** by reassigning every segment from the local
  diarization turns.

## Configuration

All variables are read from the environment by the **worker** at startup
(`DiarizationConfig.from_env`, `app/diarization/config.py`). Parsing **never
raises** on a bad value — an unknown engine or unparsable number simply makes the
config invalid/ignored later, so a typo can never crash the worker.

| Variable | Default | Meaning |
| --- | --- | --- |
| `DIARIZATION_ENABLED` | `false` | Master switch. When `false` (or engine `none`), diarization is skipped entirely. |
| `DIARIZATION_ENGINE` | `none` | Engine: `none` (explicit no-op) or `pyannote`. Any other value is unsupported (invalid). |
| `DIARIZATION_MODEL` | `pyannote/speaker-diarization-3.1` | The `pyannote.audio` pipeline / model id to load. |
| `DIARIZATION_AUTH_TOKEN` | _(empty)_ | **SECRET** — Hugging Face access token used to download/load the gated pipeline. Required for `pyannote`. **Never** logged, shown, or stored. |
| `DIARIZATION_REQUIRED` | `false` | If `true`, an enabled-but-invalid config (or a pipeline failure) **fails the job**. If `false`, the job continues without speakers. |
| `DIARIZATION_MIN_SPEAKERS` | _(unset)_ | Optional lower bound passed to the pipeline. Positive integer; non-positive/unparsable is ignored. |
| `DIARIZATION_MAX_SPEAKERS` | _(unset)_ | Optional upper bound passed to the pipeline. Positive integer; non-positive/unparsable is ignored. |

Docker build arg (build-time only, default `false`):

| Build arg | Default | Effect |
| --- | --- | --- |
| `INSTALL_PYANNOTE` | `false` | When `true`, installs `pyannote.audio>=3.1,<4` (and `torch`) into the image. Large and CPU-only; the provider imports it lazily. |

## The enabled-but-invalid rule

When `DIARIZATION_ENABLED=true` and the engine is `pyannote`, the resolved
posture is computed by `get_diarization_status` (`app/diarization/provider.py`).
A config is **invalid** when:

- the engine is not in `("none", "pyannote")` (unsupported engine), or
- the `pyannote.audio` package is **not installed** in the image (the build arg
  was not set), or
- `DIARIZATION_AUTH_TOKEN` is **missing**.

What the worker does with an invalid (or failing) config depends on
`DIARIZATION_REQUIRED`:

| `DIARIZATION_REQUIRED` | Behavior on invalid/failure |
| --- | --- |
| `false` (default) | **Continue without speakers.** The worker logs a `WARNING` (`Diarization skipped (continuing without speakers): …`) with the technical reason and finishes the job normally — segments keep `speaker = null`. |
| `true` | **Fail the job** with a friendly, secret-free message. `JobProcessor._apply_diarization` raises `DiarizationUnavailableError` (`app/diarization/errors.py`); the job is marked `failed` with the short pt-BR `user_message` (e.g. *"Diarização indisponível: token de acesso ausente."*) while the technical reason stays in the logs. |

A model that fails to **load or run** raises `DiarizationModelError`
(friendly: *"Falha ao carregar o modelo de diarização."*); with
`DIARIZATION_REQUIRED=true` this fails the job, and the token never appears in the
error. There is no silent provider switch and no traceback in the UI.

## How speakers are assigned

`pyannote.audio` returns a list of `SpeakerTurn(start, end, speaker)` covering the
audio. `assign_speakers` (`app/diarization/align.py`) then tags transcript
segments:

- For each transcript segment, the assigned speaker is the label of the turn with
  the **maximum temporal overlap** with `[segment.start, segment.end]`.
- If **no** turn overlaps (overlap must be strictly positive), the segment's
  `speaker` stays `None`.
- It is pure and deterministic: ties go to the first turn in list order, and the
  input segments are never mutated (new segment dicts are returned).

`DIARIZATION_MIN_SPEAKERS` / `DIARIZATION_MAX_SPEAKERS`, when set, are passed
through to the pipeline as hints.

### The `.txt` re-render

After speakers are assigned, the worker re-renders the human-readable transcript
with `render_local_text` (`app/transcription/normalizer.py`) so the **Download
TXT** reflects them. Segments with a speaker render as:

```
[00:00:03] Speaker SPEAKER_00:
texto do trecho

[00:00:07] Speaker SPEAKER_01:
outro trecho
```

Segments without a speaker (no overlapping turn) keep the plain
`[timestamp] texto` form. The re-rendered text is what is saved to
`transcripts.transcript_text` and, when a Drive backup is enabled, uploaded.

The normalized `transcripts.transcript_json` is updated in place — only its
`segments[].speaker` fields change — so downstream consumers see the speaker
labels too.

## How it is wired into the worker

Diarization runs inside `JobProcessor.process` (`app/worker/processor.py`),
**after** `provider.transcribe(...)` returns, via `_apply_diarization`:

1. If `diarization_config` is absent or `enabled` is `false`, return the
   transcript unchanged (the common case — no WAV extraction, no provider built).
2. Otherwise resolve `get_diarization_status`; apply the
   [enabled-but-invalid rule](#the-enabled-but-invalid-rule) above.
3. When valid, extract a 16 kHz mono WAV (`extract_audio_to_wav`), build the
   provider (`build_diarization_provider`, which lazily imports
   `PyannoteDiarizationProvider`), diarize, align (`diarize_and_align`), and
   re-render the `.txt`.

The container fields are optional with disabled/None defaults
(`app/worker/container.py`): `diarization_config`, `diarization_probes`,
`build_diarization_provider`. `build_container` populates
`diarization_config=DiarizationConfig.from_env()` and the real
`build_diarization_provider`, so production reads diarization purely from the
environment while tests can inject fakes (no real package, model, network, or
token required).

Note: when diarization is enabled and valid, it **overrides Deepgram-native
speakers** — every segment is reassigned from the local turns, so the local
diarization is authoritative regardless of which transcription provider ran.

## Enabling it

Diarization needs the `pyannote.audio` package baked into the image at build time
(it is **not** installed at container startup):

```bash
docker build --build-arg INSTALL_PYANNOTE=true -t meet-transcription:diar .
```

Then enable it in the worker environment (use a placeholder for the token — never
commit a real one):

```bash
DIARIZATION_ENABLED=true
DIARIZATION_ENGINE=pyannote
DIARIZATION_MODEL=pyannote/speaker-diarization-3.1
DIARIZATION_AUTH_TOKEN=hf_your_token_here   # SECRET — keep out of git
# Optional:
DIARIZATION_REQUIRED=false                  # true = fail the job if diarization can't run
DIARIZATION_MIN_SPEAKERS=2
DIARIZATION_MAX_SPEAKERS=6
```

The `pyannote/speaker-diarization-3.1` pipeline is gated on Hugging Face: accept
its model conditions with the account that owns the token, or the load will fail.
Diarization is CPU-bound and adds time on top of transcription; keep
`WORKER_CONCURRENCY=1` and a generous `STALE_JOB_TIMEOUT_MINUTES` for long
recordings.

## Security note

`DIARIZATION_AUTH_TOKEN` is a **Hugging Face secret**. It is handled so that it
**never** appears in:

- **logs** — the `DiarizationConfig` dataclass `repr` exposes the token, so the
  config object is never logged; warnings/info log only the friendly `message`
  or the secret-free technical `reason`;
- **errors / `user_message`** — `PyannoteDiarizationProvider` passes the token to
  the pipeline factory only; load/run failures raise `DiarizationModelError`
  with the exception **type name** only, never the token or a traceback;
- **the UI** — only the short pt-BR `user_message` reaches the UI; tracebacks
  stay in logs;
- **stored transcripts** — neither `transcript_text` nor `transcript_json`
  contains the token; only speaker **labels** (e.g. `SPEAKER_00`) are stored.

Treat the token like any other secret: keep it in `.env` / a secret manager,
never in git. See [Security](16-security.md).

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Transcript has no speakers, log shows *"Diarization skipped (continuing without speakers)"* | Enabled but **invalid** with `DIARIZATION_REQUIRED=false` | Read the logged `reason`: install the package (`INSTALL_PYANNOTE=true`), set a valid engine, or set `DIARIZATION_AUTH_TOKEN`. |
| Job fails with *"Diarização indisponível: pacote pyannote.audio não instalado."* | `pyannote.audio` not in the image and `DIARIZATION_REQUIRED=true` | Rebuild with `--build-arg INSTALL_PYANNOTE=true`. |
| Job fails with *"Diarização indisponível: token de acesso ausente."* | `DIARIZATION_AUTH_TOKEN` empty and `DIARIZATION_REQUIRED=true` | Set the Hugging Face token in the worker env. |
| Job fails with *"Falha ao carregar o modelo de diarização."* | Pipeline could not load/run (bad/expired token, model conditions not accepted, model not cached offline, etc.) | Check the worker logs for the exception **type**; accept the model conditions on Hugging Face for the token's account; verify connectivity or pre-cache the model. |
| *"Engine de diarização não suportado."* | `DIARIZATION_ENGINE` is not `none` or `pyannote` | Set `DIARIZATION_ENGINE=pyannote` (or `none` to disable). |
| Diarization seems ignored entirely | `DIARIZATION_ENABLED=false` or `DIARIZATION_ENGINE=none` | This is the default OFF posture; enable both to activate. |

Diagnostics live in the worker logs (technical `reason`), while the UI only ever
shows the friendly, secret-free message.
