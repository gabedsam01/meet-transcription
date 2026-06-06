# 22 · Google Gemini provider

`app/transcription/gemini_provider.py` — `provider_id = "gemini"`.

Gemini is a **multimodal** model: it transcribes audio and can *attempt* speaker
labels when asked in the prompt, but that is **not real diarization** and is never
stored as structured `speaker` data (`speaker = null` in every segment).

## Models

`gemini-2.5-flash` (default), `gemini-2.5-flash-lite`, `gemini-3-flash-preview`,
`gemini-3.1-flash-lite`, `gemini-3.5-flash`.

## API key

Per‑user, required, configured in the **Models** tab and stored encrypted. The key
is sent as the `?key=` query parameter to the Generative Language API and is never
logged or rendered.

## File‑size handling

Base64 inflates an inline request ~33%, so the request path depends on size
(`select_gemini_upload_mode`):

| Raw size | Mode | Behaviour |
|---|---|---|
| ≤ **70 MB** (`GEMINI_INLINE_MAX_BYTES`) | `inline` | base64 inline `generateContent` |
| > 70 MB and ≤ **99 MB** (`GEMINI_FILES_MAX_BYTES`) | `files` | upload via Files API, then reference the file URI |
| > 99 MB | `too_large` | `ProviderFileTooLargeError` (friendly message; chunking is a separate branch) |

Both limits are constants in `app/transcription/provider_models.py` and are surfaced
in the Models tab ("Limite: inline ~70 MB, até ~99 MB").

## Error mapping

| Condition | Exception | Retryable |
|---|---|---|
| missing key at construction | `ProviderCredentialMissingError` | no |
| HTTP 401 / 403 | `ProviderCredentialInvalidError` | no |
| HTTP 429 | `ProviderRateLimitedError` | yes |
| HTTP 413 / file over Files limit | `ProviderFileTooLargeError` | no |
| HTTP 5xx / network error | `ProviderUnavailableError` | yes |
| bad/empty JSON, other status | `ProviderResponseError` | no |

Only HTTP status codes go into the technical message — never the response body.

## Output

The generated text becomes a single segment via `normalize_gemini`; the `.txt`
download uses the shared header layout. The provider runs **only in the worker**;
the web layer never calls it. HTTP is injectable for tests.
