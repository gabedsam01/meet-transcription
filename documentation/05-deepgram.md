# Deepgram Provider

Deepgram is the **cloud** transcription provider for Meet Transcription. In the
web + worker deployment, Deepgram is used whenever a valid local engine is **not**
active, and each user supplies their **own** Deepgram API key. Keys are per-user,
encrypted at rest, and never used from a global environment variable.

This page documents how the per-user key works end to end (Settings → Deepgram,
the Test button, run-once gating), how the provider is wired, its strengths, and
the most common errors.

> See also: [Architecture](01-architecture.md), and the local engine alternative
> in the local transcription docs. The decision between Deepgram and a local
> engine is described under [Provider rule](#provider-rule-deepgram-vs-local).

---

## At a glance

| Aspect | Behavior |
| --- | --- |
| Scope | **Per user** — one key per user, stored in `deepgram_credentials.encrypted_api_key` |
| Storage | Encrypted at rest with Fernet, key derived from `APP_SECRET_KEY` (`app/web/security.py`) |
| Where you set it | Web UI → **Settings → Deepgram** (`/settings/deepgram`) |
| Visibility | The raw key is **never shown again** after saving — only a masked tail (`…ab12`) |
| Required? | Required **unless** a valid local engine is active (see [Provider rule](#provider-rule-deepgram-vs-local)) |
| Used by | The **worker**, never the HTTP request path |
| Global env key | The web/worker deployment does **not** use `DEEPGRAM_API_KEY` (legacy CLI only) |

---

## Per-user key lifecycle

### 1. Paste the key — Settings → Deepgram

1. Sign in to the web app and open **Settings → Deepgram** (`GET /settings/deepgram`).
2. Paste your Deepgram API key into the form and submit (`POST /settings/deepgram`,
   form field `deepgram_api_key`).
3. On submit the route trims the value; an empty key is rejected with the flash
   message **`Deepgram API Key não pode ser vazia.`** A non-empty key is saved and
   the flash **`Deepgram API Key salva.`** is shown.

The key is associated with the **current logged-in user** (`user.id`), so every
user maintains an independent credential.

### 2. Encrypted at rest (Fernet / APP_SECRET_KEY)

The route never writes the key in plaintext. It passes through `DeepgramKeyStore`
(`app/web/deepgram_key.py`), which encrypts on the way in and decrypts on the way
out:

```python
def save_for_user(self, user_id: int, api_key: str) -> None:
    self._repo.save_for_user(user_id, encrypt_value(self._fernet, api_key))

def get_key(self, user_id: int) -> str | None:
    encrypted = self._repo.get_encrypted_for_user(user_id)
    return decrypt_value(self._fernet, encrypted) if encrypted else None
```

- The Fernet instance comes from `fernet_from_secret(web_settings.app_secret_key)`
  (built once in `create_app`), so the encryption key is derived from
  **`APP_SECRET_KEY`** (`app/web/security.py`).
- Only the ciphertext is persisted, in the `deepgram_credentials` table column
  **`encrypted_api_key`**. PostgreSQL is the single source of truth — there is no
  SQLite anywhere.
- Secrets are never logged (a test enforces this); the live-check helper is
  explicitly documented as *"Never raises; never logs the key."*

> **Operational note:** `APP_SECRET_KEY` is the decryption key for every stored
> Deepgram key (and Google token). If you rotate or lose `APP_SECRET_KEY`, the
> existing encrypted keys become undecryptable and users must re-enter them.
> Keep it stable and secret.

### 3. The Test button

Settings → Deepgram has a **Test** action (`POST /settings/deepgram/test`). It
decrypts the saved key and runs a best-effort live check against Deepgram, without
sending any audio:

```python
@app.post("/settings/deepgram/test")
def test_deepgram(request: Request, user=Depends(require_user)):
    key = deepgram_store.get_key(user.id)
    if not key:
        _set_flash(request, "Configure sua Deepgram API Key antes de iniciar uma transcrição.")
    else:
        _set_flash(request, DEEPGRAM_TEST_MESSAGES[verify_deepgram_key(key)])
    return RedirectResponse("/settings/deepgram", status_code=303)
```

`verify_deepgram_key` (`app/web/deepgram_key.py`) issues a `GET` to the Deepgram
projects endpoint and maps the result to one of three outcomes:

| HTTP result from `https://api.deepgram.com/v1/projects` | Return value | Flash message (UI) |
| --- | --- | --- |
| `200` | `valid` | **`Deepgram API Key válida.`** |
| `401` or `403` | `invalid` | **`Deepgram API Key inválida.`** |
| network error / timeout / other status | `unverifiable` | **`Não foi possível verificar agora.`** |

The check uses a short 5-second timeout and **never raises** — a network failure
degrades to `unverifiable` rather than breaking the page. It also never logs the
key.

### 4. Key is never shown again

After a key is saved, the UI only displays a **masked** form of it. `masked`
(`app/web/deepgram_key.py`) returns the last four characters:

```python
def masked(self, user_id: int) -> str | None:
    key = self.get_key(user_id)
    if not key:
        return None
    return f"…{key[-4:]}" if len(key) >= 4 else "…"
```

The Settings → Deepgram page receives `configured` (`has_key(...)`) and `masked`,
never the plaintext. The Dashboard likewise only shows `deepgram_configured`
(boolean). To change a key, paste a new one — there is no "reveal" path.

---

## Provider rule (Deepgram vs. local)

Whether the per-user Deepgram key is **required** depends on the transcription
provider posture, computed once at app startup and stored on
`app.state.transcription_status`
(`get_transcription_provider_status(TranscriptionConfig.from_env())`).

| Local engine state (`LOCAL_TRANSCRIPTION_ENABLED` + validity) | Provider used | Deepgram key required? | UI signal |
| --- | --- | --- | --- |
| Disabled (`false`) | Deepgram | **Yes** | Deepgram is the only provider |
| Enabled **and valid** | Local engine | **No** | `Modelo local ativo: <engine model compute/quant>` |
| Enabled **but invalid** | Deepgram | **Yes** | `Modelo local inválido. Consulte a documentação de modelos locais.` (+ link) |

There is **no silent fallback**: when the local engine is enabled but invalid, the
system requires Deepgram and run-once is blocked unless a Deepgram key is set.

`run-once` enforces this with `deepgram_required` propagated into the job-creation
service:

```python
result = create_next_pending_job(
    worker_repos,
    build_drive_client=app.state.build_drive_client,
    credentials_from_token=app.state.credentials_from_token,
    user_id=user.id,
    # A valid local engine drops the Deepgram-key requirement.
    deepgram_required=app.state.transcription_status.deepgram_required,
)
```

When `deepgram_required` is true and the user has no key, run-once returns the
status `no_deepgram_key`, which the UI renders as
**`Configure sua Deepgram API Key antes de iniciar uma transcrição.`**

---

## How the worker uses the key

The web layer **never transcribes in-request** — run-once only validates and
creates a `pending` job (then enqueues the id). The **worker** later picks the job
up, decrypts that user's key, and calls Deepgram.

The Deepgram call is wrapped by `DeepgramClient` (`app/deepgram_client.py`). Its
request:

- `POST https://api.deepgram.com/v1/listen`
- `Authorization: Token <api_key>` header
- `Content-Type: video/mp4`, streaming the downloaded MP4 as the request body
- query params from `_params()`: `model`, `language`, `smart_format`, `punctuate`,
  `diarize`, `utterances`

```python
def transcribe(self, video_path, api_key=None):
    key = api_key or self.api_key
    if not key:
        raise DeepgramError("Deepgram API key is required")
    ...
```

The `from_api_key` factory documents the default request shape used when building
a client from a bare key:

| Parameter | Default |
| --- | --- |
| `model` | `nova-3` |
| `language` | `pt-BR` |
| `smart_format` | `True` |
| `punctuate` | `True` |
| `diarize` | `True` |
| `utterances` | `True` |

In the providers layer, `deepgram_provider.py` wraps `DeepgramClient`, keeps the
legacy `.txt` `format_transcript` output, and stores the raw response under
`payload.raw`. The transcript is normalized into the shared schema (`provider:
deepgram`, `engine: deepgram`, plus `text`, `segments`, `words`, `utterances`,
`raw`) and persisted in `transcripts` — `transcript_text` is what **Download TXT**
serves from the web UI.

---

## Strengths

- **Diarization** — Deepgram is requested with `diarize=true` and
  `utterances=true`, so transcripts carry speaker-attributed segments. (By
  contrast, the local MVP has no diarization and emits `speaker=null`.)
- **Smart formatting & punctuation** — `smart_format=true` and `punctuate=true`
  produce readable, properly punctuated text out of the box.
- **Speed** — being a managed cloud service, Deepgram returns results quickly and
  needs no local CPU/model footprint, no model download, and no `./models` mount.
- **Multilingual** — defaults target `pt-BR`, suitable for the project's mixed
  pt-BR + English audio.
- **Zero local setup** — no `INSTALL_LOCAL_TRANSCRIPTION` build args, no engine
  binaries; the per-user key is the only thing to configure.

---

## Common errors

### Key ausente (missing key)

The user has not saved a Deepgram key while Deepgram is required.

- **Where it shows:**
  - Test button → **`Configure sua Deepgram API Key antes de iniciar uma transcrição.`**
  - Run-once (status `no_deepgram_key`) → same message.
  - Saving an empty key → **`Deepgram API Key não pode ser vazia.`**
- **At the client level:** `DeepgramClient.transcribe` raises
  `DeepgramError("Deepgram API key is required")` if no key is present.
- **Fix:** Settings → Deepgram → paste a valid key → save. (Or activate a valid
  local engine to drop the requirement entirely — see
  [Provider rule](#provider-rule-deepgram-vs-local).)

### Key inválida (invalid key)

The key is present but rejected by Deepgram.

- **Where it shows:** Test button → **`Deepgram API Key inválida.`** This maps to a
  `401`/`403` from the Deepgram projects endpoint.
- **At job time:** an invalid key surfaces as a non-2xx from
  `POST /v1/listen`; `DeepgramClient` raises `DeepgramError` with the status code,
  the job is marked `failed`, and the worker stores a **friendly, secret-free**
  `user_message` as the job `error_message` (the traceback stays in logs only).
- **Fix:** re-issue or correct the key in the Deepgram console, paste the new key
  in Settings → Deepgram, and re-test.

### Não foi possível verificar agora (unverifiable)

The Test button could not reach Deepgram (network down, timeout, or an unexpected
status). This is **not** a verdict on the key — it means the live check could not
complete.

- **Where it shows:** Test button → **`Não foi possível verificar agora.`**
- **Fix:** check outbound network/DNS from the `web` container to
  `api.deepgram.com` and retry. The key may still be valid; this status never
  blocks saving.

---

## Quick reference

| Item | Value |
| --- | --- |
| Settings page | `GET /settings/deepgram` |
| Save key | `POST /settings/deepgram` (field `deepgram_api_key`) |
| Test key | `POST /settings/deepgram/test` |
| Verify endpoint | `GET https://api.deepgram.com/v1/projects` |
| Transcribe endpoint | `POST https://api.deepgram.com/v1/listen` |
| Storage table / column | `deepgram_credentials.encrypted_api_key` (PostgreSQL) |
| Encryption | Fernet via `app/web/security.py`, key from `APP_SECRET_KEY` |
| Store/helpers | `app/web/deepgram_key.py` (`DeepgramKeyStore`, `verify_deepgram_key`) |
| Client | `app/deepgram_client.py` (`DeepgramClient`, `DeepgramError`) |
| Required when | `LOCAL_TRANSCRIPTION_ENABLED=false`, or local engine enabled-but-invalid |
| Not required when | A valid local engine is active |
