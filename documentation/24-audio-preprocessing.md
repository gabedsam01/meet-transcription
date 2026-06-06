# Audio Preprocessing & Compression

Meet Transcription ships an **audio preprocessing & compression layer** (`app/audio/`) that can probe a media file, extract a clean **16 kHz mono WAV**, **compress** it to fit upload size limits, **chunk** long audio into overlapping or sequential windows, and **stitch** per-chunk transcripts back into one. It is a small, tested library: every operation that runs `ffmpeg` / `ffprobe` goes through an **injectable `runner`** in tests.

The layer is active today in two ways:
1. **Fast-fail on a recording with no audio track**: Run-once and extension uploads verify an audio stream exists before transcribing.
2. **Auto-compression and chunking for cloud providers**: When a file size exceeds the cloud provider limit (e.g., 25 MB for Groq, 99 MB for OpenRouter/Gemini/AssemblyAI), the worker automatically compresses it to FLAC/MP3 and chunks it if necessary, stitching the transcripts back together.

See also: [41-audio-compression-pipeline.md](41-audio-compression-pipeline.md) for detailed pipeline flow, [06-local-transcription.md](06-local-transcription.md) for local CPU engines, and [11-worker-flow.md](11-worker-flow.md) for the job lifecycle.

## What the library does

`app/audio/__init__.py` re-exports the public building blocks. Each is a command-builder plus a runner wrapper:

| Module | Function(s) / Classes | Purpose |
| --- | --- | --- |
| `probe.py` | `probe_audio`, `build_ffprobe_command`, `AudioInfo` | Run `ffprobe -show_format -show_streams`, return the first audio stream's info. Raises `NoAudioTrackError` when there is no audio stream. |
| `preprocessor.py` | `extract_audio`, `build_extract_command` | Extract a lossless **16 kHz mono PCM WAV** (`pcm_s16le`, `-vn`) — the intermediate the local engines decode directly. |
| `compression.py` | `prepare_audio_for_provider` | Orchestrates the entire preparation pipeline: chooses backend, compresses to preferred format, fallbacks to MP3/Opus, and chunks/reduces bitrate/duration if limits are still exceeded. |
| `planner.py` | `plan_compression`, `select_backend` | Decides if a file size is within limits (`no-op` plan) or selects the best available backend (`ffmpeg_cli` or wrappers). |
| `backends/` | `ffmpeg_cli`, `ffmpeg_python_backend`, `pydub_backend`, `moviepy_backend` | Concrete implementations executing the commands/libs with path-traversal protection. |
| `stitch.py` | `stitch_transcript_chunks` | Re-assemble per-chunk transcripts shifting each segment by its chunk's `start_offset`. |
| `errors.py` | `AudioError`, `NoAudioTrackError`, `AudioProbeError`, `FfmpegError`, `FfmpegNotFoundError` | Error hierarchy. UI friendly messages without logs/stderr leakage. |
| `config.py` | `AudioConfig` | Environment-loaded, frozen config. |

## Configuration

`AudioConfig.from_env()` reads the following variables:

| Variable | Default | Meaning |
| --- | --- | --- |
| `AUDIO_PREPROCESSING_ENABLED` | `false` | Master switch. When `false`, the worker skips the audio check entirely. |
| `AUDIO_TARGET_SAMPLE_RATE` | `16000` | Target sample rate (Hz) for extraction. |
| `AUDIO_TARGET_CHANNELS` | `1` | Target channel count (mono). |
| `AUDIO_TARGET_BITRATE` | `24000` | Target bitrate (bps) for lossy compression. |
| `AUDIO_CHUNK_MAX_DURATION_SECONDS` | `900` | Maximum length (s) of a single chunk window (15 min). |
| `AUDIO_CHUNK_OVERLAP_SECONDS` | `2` | Overlap (s) between consecutive chunks for local chunking. |
| `AUDIO_MAX_INLINE_MB` | `70` | Size ceiling (MB) for an inline-upload provider path. |
| `AUDIO_MAX_FILE_API_MB` | `99` | Size ceiling (MB) for a Files-API upload provider path. |

## Injectable runner / testability

Every command-running function accepts a `runner` callable. The default runner calls `subprocess.run(cmd, capture_output=True, text=True)`; tests inject a fake runner to assert command-line options and mock outputs.
