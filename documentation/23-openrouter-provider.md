# 23 · OpenRouter provider

`app/transcription/openrouter_provider.py` — `provider_id = "openrouter"`.

OpenRouter routes to many ASR models through one **OpenAI‑compatible** audio
transcriptions endpoint: `POST https://openrouter.ai/api/v1/audio/transcriptions`.

## Models

`microsoft/mai-transcribe-1.5`, `nvidia/parakeet-tdt-0.6b-v3`,
`mistralai/voxtral-mini-transcribe`, `qwen/qwen3-asr-flash-2026-02-10`,
`openai/whisper-large-v3-turbo` (default), `openai/whisper-large-v3`,
`nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free`.

## API key

Per‑user, required, configured in the **Models** tab and stored encrypted. Sent as
`Authorization: Bearer <key>`; never logged or rendered.

## Diarization & output

No real diarization is promised — it depends entirely on the chosen model.
`normalize_openrouter`:

- response with `segments` (each `start`/`end`/`text`) → mapped to segments
  (`speaker = null`);
- response with only `text` → a single segment;
- `language` from the response when present, else the requested language.

## Error mapping

| Condition | Exception | Retryable |
|---|---|---|
| missing key at construction | `ProviderCredentialMissingError` | no |
| file larger than the configured ceiling (default ~100 MB) | `ProviderFileTooLargeError` (before upload) | no |
| HTTP 401 / 403 | `ProviderCredentialInvalidError` | no |
| HTTP 429 | `ProviderRateLimitedError` | yes |
| HTTP 413 | `ProviderFileTooLargeError` | no |
| HTTP 5xx / network error | `ProviderUnavailableError` | yes |
| bad/empty JSON, other status | `ProviderResponseError` | no |

Only the HTTP status code goes into the technical message — never the response
body. The `.txt` download uses the shared header layout. The provider runs **only
in the worker**; HTTP is injectable for tests.
