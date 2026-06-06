# Audio Preprocessing

Meet Transcription ships an **audio preprocessing layer** (`app/audio/`) that can
probe a media file, extract a clean **16 kHz mono WAV**, **compress** it to fit
upload size limits, **chunk** long audio into overlapping windows, and **stitch**
per-chunk transcripts back into one. It is a small, tested, side-effect-free-by-
default library: every operation that runs `ffmpeg` / `ffprobe` goes through an
**injectable `runner`**, so tests need no real binary and nothing downloads at
import time.

The layer is **off by default** (`AUDIO_PREPROCESSING_ENABLED=false`) and is
deliberately under-wired into the worker: the **only** behavior active today is a
**fast-fail on a recording with no audio track**. Compression, chunking, and
stitching are a finished, tested building block kept ready for size-limited
providers (e.g. a Gemini-style inline 70 MB / Files API 99 MB path) — they are
**not** wired into the Deepgram or whisper engines, which handle long audio
natively.

See also: [06-local-transcription.md](06-local-transcription.md) for the local
CPU engines, [08-whisper-cpp.md](08-whisper-cpp.md) for the whisper.cpp WAV
extraction, and [11-worker-flow.md](11-worker-flow.md) for the job lifecycle.

## What the library does

`app/audio/__init__.py` re-exports the public building blocks. Each is a pure
command-builder plus a thin runner wrapper:

| Module | Function(s) | Purpose |
| --- | --- | --- |
| `probe.py` | `probe_audio`, `build_ffprobe_command`, `AudioInfo` | Run `ffprobe -show_format -show_streams`, return the first audio stream's info (`has_audio`, `duration_seconds`, `sample_rate`, `channels`, `codec`, `bit_rate`, `size_bytes`). Raises `NoAudioTrackError` when there is no audio stream, `AudioProbeError` on a nonzero return code or unparseable JSON. |
| `preprocessor.py` | `extract_audio`, `build_extract_command` | Extract a lossless **16 kHz mono PCM WAV** (`pcm_s16le`, `-vn`) — the intermediate the local engines decode directly. |
| `compress.py` | `needs_compression`, `compress_audio`, `build_compress_command` | Re-encode to a lossy codec (Opus / `libopus` by default, 24 kbps) to fit an upload ceiling. `needs_compression(size_bytes, target_max_mb)` is the size gate; `compress_audio` always re-encodes when invoked. |
| `chunking.py` | `plan_chunks`, `chunk_audio`, `build_chunk_command`, `AudioChunk` | Split long audio into overlapping windows. `plan_chunks` is pure: one window if the file fits, otherwise windows of at most `max_duration_seconds` that overlap by `overlap_seconds`; the final window ends exactly at the duration. `chunk_audio` stream-copies (`-c copy`) each window — exact on the extracted PCM WAV. |
| `stitch.py` | `stitch_transcript_chunks` | Re-assemble per-chunk transcripts (times relative to each chunk) into one global transcript, shifting each segment by its chunk's `start_offset` and **deduping the overlap** (a textual duplicate falling inside the previous chunk's tail is dropped). |
| `errors.py` | `AudioError`, `NoAudioTrackError`, `AudioProbeError`, `FfmpegError` | Error hierarchy. Every error is an `AppError` carrying a technical message (for logs) and a short, secret-free, pt-BR `user_message` (for the UI). `ffmpeg` / `ffprobe` stderr stays in the technical message, never in `user_message`. |
| `config.py` | `AudioConfig` | Environment-loaded, frozen config (see below). |

The typical full pipeline (when fully wired) would be: **probe** → **extract**
16 kHz mono WAV → optionally **compress** if over the size ceiling → **chunk** if
too long → transcribe each chunk → **stitch** the per-chunk transcripts. Today
only **probe** runs in the worker.

## Configuration

`AudioConfig.from_env()` reads the following variables. Like
`TranscriptionConfig`, it **never raises on a bad value**: an unparseable
number/bool falls back to the safe default (negative numbers are rejected back to
the default), so it can never crash worker startup.

| Variable | Default | Meaning |
| --- | --- | --- |
| `AUDIO_PREPROCESSING_ENABLED` | `false` | Master switch. When `false`, the worker skips the audio check entirely. |
| `AUDIO_TARGET_SAMPLE_RATE` | `16000` | Target sample rate (Hz) for extraction. |
| `AUDIO_TARGET_CHANNELS` | `1` | Target channel count (mono). |
| `AUDIO_TARGET_BITRATE` | `24000` | Target bitrate (bps) for lossy compression. |
| `AUDIO_CHUNK_MAX_DURATION_SECONDS` | `900` | Maximum length (s) of a single chunk window (15 min). |
| `AUDIO_CHUNK_OVERLAP_SECONDS` | `2` | Overlap (s) between consecutive chunks so a straddling word appears in both; `stitch` dedupes it. `0` is allowed. |
| `AUDIO_MAX_INLINE_MB` | `70` | Size ceiling (MB) for an inline-upload provider path (Gemini-style inline). |
| `AUDIO_MAX_FILE_API_MB` | `99` | Size ceiling (MB) for a Files-API upload provider path. |

> The sample-rate / channel / bitrate / chunk knobs above feed the
> `compress`/`chunk`/`extract` library functions as defaults. Because only the
> no-audio fast-fail is wired today, changing them has **no effect** on the live
> Deepgram / whisper path — they exist for the size-limited provider work.

## How it is wired into the worker (gated, OFF by default)

`build_container` (`app/worker/container.py`) loads `AudioConfig.from_env()` and
stores it on the `WorkerContainer` as `audio_config`. There is also an
`audio_runner` field (the injectable `ffmpeg`/`ffprobe` runner), left **`None`**
in production so the real `subprocess`-backed default runner is used; tests pass a
fake runner here. Both default to disabled/None so the existing Drive + Deepgram
path is byte-for-byte unchanged.

The only consumer today is `JobProcessor._check_audio`
(`app/worker/processor.py`):

```python
def _check_audio(self, media_path: Path) -> None:
    config = self.container.audio_config or AudioConfig.disabled()
    if not config.enabled:
        return
    # probe_audio raises NoAudioTrackError (an AppError) when there is no audio
    # stream, which the outer handler turns into a friendly job failure.
    probe_audio(str(media_path), runner=self.container.audio_runner)
```

It runs right after the media is obtained (Drive download or chrome-extension
upload) and before transcription. When `AUDIO_PREPROCESSING_ENABLED=false`
(default) it returns immediately — zero impact, no `ffprobe` call. When enabled,
`probe_audio` runs `ffprobe`; if the file has **no audio stream** it raises
`NoAudioTrackError`, which the processor's outer `except` turns into a `failed`
job with the friendly pt-BR `user_message`:

> O arquivo enviado não contém faixa de áudio.

(The technical message and any `ffprobe` stderr stay in the logs only.)

Nothing else from the library — `extract_audio`, `compress_audio`, `chunk_audio`,
`stitch_transcript_chunks` — is called by the worker. (`extract_audio_to_wav`
used by diarization lives in `app/transcription/audio.py`, a separate helper; see
[26-diarization.md](26-diarization.md).)

## ffmpeg / ffprobe requirements

`ffmpeg` and `ffprobe` are required **only when preprocessing is enabled** and the
worker actually probes a file. They are **not** in the base image. The `Dockerfile`
installs `ffmpeg` (which includes `ffprobe`) at **build time** when the relevant
build arg is set:

- `--build-arg INSTALL_WHISPER_CPP=true` — installs `ffmpeg` (whisper.cpp needs it
  for 16 kHz mono WAV extraction).
- `--build-arg INSTALL_LOCAL_TRANSCRIPTION=true` — installs `ffmpeg` as well, so
  preprocessing can run alongside Deepgram or faster-whisper.

```bash
# image with ffmpeg/ffprobe available for preprocessing
docker build --build-arg INSTALL_LOCAL_TRANSCRIPTION=true -t meet-transcription:audio .
```

All install build args default to `false` and are **build-time only**, never
runtime configuration. Nothing heavy is installed at container startup.

## Injectable runner / testability

Every command-running function (`probe_audio`, `extract_audio`, `compress_audio`,
`chunk_audio`) accepts a `runner` callable. The default runner calls
`subprocess.run(cmd, capture_output=True, text=True)`; tests inject a fake that
returns an object with `.returncode` / `.stdout` / `.stderr`, so:

- The command builders (`build_*_command`) are pure and asserted exactly.
- `plan_chunks` and `stitch_transcript_chunks` are pure and deterministic.
- No real `ffmpeg` / `ffprobe` binary is needed in the suite, and nothing
  downloads.

The worker tests pass a fake `audio_runner` through `make_worker_container`
(`tests/support.py`) to drive `_check_audio` without a binary — e.g.
`tests/test_worker_audio.py` simulates a video-only stream to assert the no-audio
fast-fail. The library itself is covered by `tests/test_audio_probe.py`,
`tests/test_audio_preprocess.py`, `tests/test_audio_chunking.py`, and
`tests/test_audio_stitch.py`.

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Job fails with *"O arquivo enviado não contém faixa de áudio."* | The recording genuinely has no audio stream (e.g. a screen-only capture). | Re-record with audio, or transcribe a different file. This is the intended fast-fail. |
| Job fails with *"Não foi possível analisar o áudio da reunião."* (`AudioProbeError`) | `ffprobe` returned nonzero or unparseable output (corrupt file, or `ffprobe` missing/not on PATH). | Check the worker logs for the `ffprobe` stderr. Ensure the image was built with `INSTALL_WHISPER_CPP=true` or `INSTALL_LOCAL_TRANSCRIPTION=true` so `ffmpeg`/`ffprobe` are installed. |
| `ffprobe: command not found` in logs after enabling preprocessing | Preprocessing was enabled at runtime but the image has no `ffmpeg`/`ffprobe`. | Rebuild with the build arg above; the binaries cannot be installed at container startup. |
| Changing `AUDIO_TARGET_*` / `AUDIO_CHUNK_*` / `AUDIO_MAX_*` has no visible effect | Only the no-audio fast-fail is wired today; compress/chunk/stitch are an unwired library. | Expected. These knobs feed the size-limited provider path that is not yet hooked into Deepgram/whisper. |
| Worker startup unaffected by a typo'd audio var | `AudioConfig.from_env` falls back to the default on any unparseable value. | Fix the value if you intended a non-default; nothing will crash. |
