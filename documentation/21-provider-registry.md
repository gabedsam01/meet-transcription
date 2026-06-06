# 21 · Provider registry & resolution

## The registry — `app/transcription/provider_models.py`

A pure, I/O‑free catalogue of every provider and its models. It is the single
source of truth the Models tab renders from and the resolver validates against.

`ProviderSpec` fields: `provider_id`, `label`, `models`, `default_model`,
`requires_api_key`, `diarization_kind` / `diarization` (UI sentence), `notes`,
`docs_url`, `max_inline_bytes`, `max_file_bytes`, `capabilities`.

| Provider id | Models | API key | Diarization |
|---|---|---|---|
| `deepgram` | `nova-3`, `nova-2`, `whisper` | required | **real** |
| `openrouter` | `microsoft/mai-transcribe-1.5`, `nvidia/parakeet-tdt-0.6b-v3`, `mistralai/voxtral-mini-transcribe`, `qwen/qwen3-asr-flash-2026-02-10`, `openai/whisper-large-v3-turbo`, `openai/whisper-large-v3`, `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free` | required | model‑dependent (usually none) |
| `gemini` | `gemini-2.5-flash`, `gemini-2.5-flash-lite`, `gemini-3-flash-preview`, `gemini-3.1-flash-lite`, `gemini-3.5-flash` | required | pseudo (prompt) |
| `local` | `tiny`…`large-v3-turbo` | none | none (MVP) |

Helpers: `get_provider_spec`, `is_valid_provider`, `is_cloud_provider`,
`requires_api_key`, `is_valid_model`, `default_model`, `models_for`.
`CLOUD_PROVIDERS = (deepgram, openrouter, gemini)`; `local` is env‑driven, not a
per‑user selectable cloud provider.

## Normalized transcript schema — `app/transcription/normalizer.py`

Every provider returns a `TranscriptionResult(text, payload)` where `payload` is:

```json
{
  "provider": "deepgram|openrouter|gemini|local",
  "engine": "...", "model": "...", "language": "pt|en|auto|null",
  "text": "...",
  "segments": [{"start": 0.0, "end": 1.0, "speaker": null, "text": "..."}],
  "words": [], "utterances": [], "raw": {}
}
```

`normalize_deepgram` maps utterances (with real `speaker`), `normalize_openrouter`
maps OpenAI‑style `segments` or a single text segment, `normalize_gemini` stores
one segment with `speaker=null` (pseudo‑diarization is never structured). The
`.txt` download is rendered by the engine‑agnostic `render_transcript_text`.

## Resolution — `app/transcription/registry.py`

`resolve_cloud_provider(model_settings, credentials, *, build) -> ResolvedProvider`
is a **pure decision function**; the provider object is produced by the injected
`build(provider_id, model, api_key)` callback (so the resolver imports no SDK).

1. Validate the primary provider/model (invalid model → clamped to the default).
2. If it needs a key and the key is missing → try the **fallback** when enabled.
3. If neither is usable → raise the primary's friendly `ProviderError` (names the
   provider, links the docs, never leaks a key).

This is separate from `app/transcription/factory.py::resolve_provider`, which owns
the orthogonal **local‑engine vs Deepgram** rule (env‑driven). In the worker
(`app/worker/processor.py`) an explicit OpenRouter/Gemini selection uses the cloud
resolver; everything else (no selection, or Deepgram) keeps the legacy path with
its "no silent fallback" guarantee.

## Errors — `app/errors.py` (re‑exported from `app/transcription/errors.py`)

`ProviderError` subclasses each carry `code`, `user_message` (friendly, shown in
the UI), `technical_message` (logs only), `retryable`, `docs_url`:

`ProviderNotConfiguredError`, `ProviderCredentialMissingError`,
`ProviderCredentialInvalidError`, `ProviderRateLimitedError` (retryable),
`ProviderFileTooLargeError`, `ProviderResponseError`,
`ProviderUnavailableError` (retryable), `ProviderModelUnsupportedError`.
