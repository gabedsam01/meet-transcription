# 20 · Models tab

The **Models** tab (`/models`) is the per‑user control centre for transcription
providers. It replaces the old isolated **Deepgram** tab: every user chooses a
provider, a model, supplies the provider's API key (encrypted), tests it, and can
configure a fallback provider.

## Where it lives

- Routes: `app/web/main.py` (`/models`, `/models/provider`, `/models/credentials`,
  `/models/test`, `/models/fallback`).
- Template: `app/web/templates/models.html` (server‑rendered Jinja2, no SPA).
- Key store: `app/web/provider_keys.py` (`ProviderKeyStore`, `verify_provider_key`).
- Selection model: `app/transcription/provider_config.py` (`ModelSettings`).
- Registry of providers/models: `app/transcription/provider_models.py`.

## What the page shows

- **Primary provider** + **model** (saved via `POST /models/provider`).
- **Per‑provider credentials**: API key status (`Configurado`/`Não configurado`),
  the **masked tail** (`…abcd`, last 4 chars max — the full key is never shown
  again), a **Save** form and a **Test** button.
- **Fallback**: enable + provider + model (`POST /models/fallback`).
- **Diarization & limits** notes per provider (see below).

## Routes

| Method & path | Form fields | Effect |
|---|---|---|
| `GET /models` | `?provider=` (optional preselect) | Render the tab. |
| `POST /models/provider` | `provider`, `model` | Save the primary selection (normalized/clamped). |
| `POST /models/credentials` | `provider`, `api_key` | Save an encrypted key for that provider. |
| `POST /models/test` | `provider` | Best‑effort live key verification → flash. |
| `POST /models/fallback` | `fallback_enabled`, `fallback_provider`, `fallback_model` | Save the fallback. |

## Backward compatibility

- `GET /settings/deepgram` → **303 redirect** to `/models?provider=deepgram`.
- `POST /settings/deepgram` and `POST /settings/deepgram/test` still work as
  **aliases**, writing/reading the Deepgram key through the new per‑provider store.
- Keys saved before this tab (legacy `deepgram_credentials` table) are read
  transparently and backfilled into `provider_credentials` by migration
  `0002_provider_registry` (`provider='deepgram'`).

## Security

- Keys are encrypted with Fernet (key derived from `APP_SECRET_KEY`) **before**
  reaching the repository — plaintext keys never hit the database.
- The UI shows at most the **last 4 characters**. Keys never appear in logs,
  error messages, or rendered HTML. `verify_provider_key` never logs the key.

## Validation

`ModelSettings` is normalized by `normalize_model_settings`: an unknown provider
falls back to the default (Deepgram), an unknown model falls back to the
provider's default model, and a fallback equal to the primary (or only partially
specified) is dropped. A stale or hand‑edited row therefore can never crash the
worker.
