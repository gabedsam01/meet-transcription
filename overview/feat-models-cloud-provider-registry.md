# Overview — `feat/models-cloud-provider-registry`

## 1. Branch

`feat/models-cloud-provider-registry` (forked from `main`).

## 2. Objective

Replace the isolated **Deepgram** settings tab with a **Models** tab where each
user picks a transcription **provider**, **model**, **API key**, and **fallback**,
and test the configuration. Add a **provider registry** with **Deepgram**,
**OpenRouter** and **Gemini** cloud providers, per‑provider **encrypted
credentials**, normalized output, fallback resolution, the worker integration,
tests and documentation.

## 3. Files created

- `app/transcription/provider_models.py` — provider/model registry + size limits.
- `app/transcription/provider_config.py` — `ModelSettings` + normalization.
- `app/transcription/errors.py` — re‑export of the provider error subset.
- `app/transcription/openrouter_provider.py` — OpenRouter provider.
- `app/transcription/gemini_provider.py` — Gemini provider + size‑mode selection.
- `app/transcription/registry.py` — `ProviderResolver` / `resolve_cloud_provider`.
- `app/web/provider_keys.py` — `ProviderKeyStore` + `verify_provider_key`.
- `app/web/templates/models.html` — the Models tab.
- `alembic/versions/0002_provider_registry_tables.py` — migration (+ backfill).
- Docs: `documentation/20-models-tab.md`, `21-provider-registry.md`,
  `22-gemini-provider.md`, `23-openrouter-provider.md`.
- Tests: `tests/test_provider_errors.py`, `test_provider_models.py`,
  `test_provider_config.py`, `test_openrouter_provider.py`,
  `test_gemini_provider.py`, `test_provider_registry.py`, `test_provider_keys.py`,
  `test_models_routes.py`.

## 4. Files changed

- `app/errors.py` — `ProviderError` base + 8 concrete provider errors
  (`code`/`user_message`/`technical_message`/`retryable`/`docs_url`).
- `app/transcription/normalizer.py` — `normalize_openrouter`, `normalize_gemini`,
  `render_transcript_text` alias.
- `app/database/models.py` — `ProviderCredential`, `UserModelSettings` ORM models.
- `app/database/repositories.py` — `ProviderCredentialRepository` (legacy‑aware),
  `UserModelSettingsRepository`.
- `app/web/repositories.py` — new Protocols + `RepositoryBundle` fields
  (`provider_credentials`, `model_settings`, default `None`).
- `app/db/postgres.py` — Pg adapters for both new repos; bundle wired.
- `app/core/models.py` — `Settings.model_settings` + `Settings.provider_credentials`.
- `app/repositories/postgres.py` — worker `Settings.get` populates model settings +
  decrypted credentials (legacy Deepgram fallback preserved).
- `app/web/main.py` — `/models`, `/models/provider`, `/models/credentials`,
  `/models/test`, `/models/fallback`; `/settings/deepgram` GET→redirect, POST alias;
  dashboard shows the Models provider status.
- `app/worker/processor.py` — explicit OpenRouter/Gemini selection uses the cloud
  resolver (with Deepgram fallback); legacy local/Deepgram path unchanged.
- `app/worker/container.py` — `build_cloud_provider` (lazy provider imports).
- `app/services/job_service.py` — run‑once gate accepts any cloud credential.
- Templates `base.html` / `settings.html` / `dashboard.html` — Deepgram → Models.
- `app/db/_auth_contract.py`, `app/repositories/_worker_contract.py` — optional
  fields mirrored on the standalone fallback contracts.
- `tests/fakes.py`, `tests/support.py` — in‑memory fakes + cloud builder/fake.
- Deleted `app/web/templates/settings_deepgram.html` (superseded by `models.html`).

## 5. Migrations

- `0002_provider_registry` (down‑revision `0001_initial`, single head):
  - creates `provider_credentials` (`UNIQUE(user_id, provider)`, `ix` on `user_id`);
  - creates `user_model_settings` (`UNIQUE`/`ix` on `user_id`);
  - **backfills** existing `deepgram_credentials` rows into `provider_credentials`
    with `provider='deepgram'` (`ON CONFLICT DO NOTHING`);
  - keeps `deepgram_credentials` (no drop) for backward‑compatible reads.

## 6. Environment variables added

**None.** Cloud provider keys (Deepgram/OpenRouter/Gemini) are **per‑user**, saved
encrypted via the Models tab — there is no global key and no new env var. A
documentation note was added to `.env.example`. No new Python dependencies
(`requests` was already required).

## 7. Tests added / changed

- Added 8 new test modules (errors, registry/models, config, both cloud providers,
  resolver, key store, Models routes) — covers all 14 mandatory cases:
  save provider/model, encrypted per‑provider key, masked UI, Deepgram
  compatibility, OpenRouter text‑only + 429 + 401, Gemini 70 MB/99 MB limits,
  resolver fallback + friendly no‑key error, `/settings/deepgram` redirect/alias,
  and run‑once using the configured provider.
- Updated `test_web_ui.py`, `test_web_routes.py`, `test_models_metadata.py`,
  `test_job_service.py`, `test_normalizer.py`, `test_worker_processor.py`.

## 8. Commands executed

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q app scripts
docker compose config
docker compose build
```

## 9. Test results

- `pytest`: **331 passed, 37 skipped** (the 37 are PostgreSQL‑backed tests that
  skip when no database is reachable — never a SQLite fallback).
- `compileall app scripts`: **OK**.
- `docker compose config`: **OK**.
- `docker compose build`: see the CI/log (unaffected by these changes — no
  Dockerfile/requirements edits).

## 10. Risks & limitations

- Cloud providers run **only in the worker**; they are exercised in tests via
  injected HTTP fakes (no live calls). Real‑world auth/response shapes for
  OpenRouter/Gemini may need tuning against the live APIs.
- Gemini Files API path is implemented and unit‑tested for mode selection +
  2‑step flow, but not validated against the live service.
- Files larger than the Gemini Files limit (~99 MB) are **refused** — chunking is
  intentionally out of scope (a separate preprocessing branch).
- Model lists are point‑in‑time; update `provider_models.py` as providers change.
- DB‑touching tests require PostgreSQL; they skip otherwise (by design).

## 11. How to test manually

1. `cp .env.example .env`, set `APP_SECRET_KEY` and DB/OAuth vars; start the stack.
2. Sign in → **Models** (`/models`).
3. Pick **OpenRouter** + a model, **Save provider**; paste an API key, **Save**,
   **Test**. Confirm only `…abcd` is shown and the raw key never appears.
4. Enable **fallback** = Deepgram + `nova-3`, save.
5. Configure Drive + connect Google, click **Run once** on Jobs; the worker uses
   the configured provider (or the fallback if the primary key is absent).
6. Visit `/settings/deepgram` → redirected to `/models?provider=deepgram`.

## 12. Next steps

- Validate OpenRouter/Gemini against live APIs and adjust response parsing.
- Optional: audio chunking for Gemini files > 99 MB; per‑provider language config;
  real local diarization module.

## 13. PR

Opened against `main` (see the branch PR link in the repository).

## 14. Confirmations

- ✅ **No SQLite reintroduced** — PostgreSQL only; tests use dict‑backed in‑memory
  fakes; the new tables are PostgreSQL ORM + Alembic.
- ✅ **No secrets logged** — keys encrypted at rest (Fernet), masked to the last 4
  chars in the UI, never written to logs/UI/errors; verification never logs keys;
  provider error technical messages carry only HTTP status codes.
- ✅ **No heavy transcription in the Web UI** — the web layer only validates,
  stores settings/keys, and enqueues jobs; download/transcribe/upload run in the
  worker. Cloud providers are constructed and called only there.
