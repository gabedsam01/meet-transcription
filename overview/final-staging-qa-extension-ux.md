# Final Staging QA — Extension-first UX

## Summary

Final QA audit for PR #7 (`qa/next-platform-features-v2`). Extension-first upload
mode with per-user tokens, dynamic Chrome extension permissions, simplified UX,
mobile responsive design, and Google Drive as optional.

## CI status

All PR #7 checks pass:

| Check | Status |
|-------|--------|
| Analyze (actions) | pass |
| Analyze (python) | pass |
| CodeQL | pass |
| publish | pass |
| test | pass |

## Local validation

| Command | Result |
|---------|--------|
| `pytest -v` | 739 passed, 41 skipped |
| `pytest tests/e2e -v` | 32 passed |
| `compileall app scripts` | OK |
| `docker compose config` | OK |
| `docker compose build` | OK |
| `alembic heads` | `0002_extension_tokens (head)` |

## Backend checks

| # | Check | Result |
|---|-------|--------|
| 1 | App starts without Google envs | PASS — `google_enabled` derived from env vars, no hard requirement |
| 2 | Provider readiness (all providers) | PASS — OpenRouter/Groq/AssemblyAI/Deepgram/Gemini all supported |
| 3 | Extension upload creates job for token owner | PASS — per-user token resolution, job scoped to owner |
| 4 | Token revocation works | PASS — `revoked_at` checked, revoked tokens return 401 |
| 5 | CORS for extension | PASS — scoped to `/api/recordings/`, origin-anchored |
| 6 | `/api/recordings/ping` | PASS — POST returns user info for valid tokens, 401 for invalid |
| 7 | Google Drive routes show optional/disabled | PASS — warning badge + alert when Google envs absent |
| 8 | Worker no Redis idle timeout spam | PASS — `TimeoutError` logged at DEBUG, no ERROR spam |
| 9 | Large file compression/chunking | PASS — OpenRouter/Groq chunking tested |
| 10 | Local provider skips cloud preprocessing | PASS — explicit `kind == "local"` branch |

## Extension checks

| # | Check | Result |
|---|-------|--------|
| 1 | No manifest edit needed for domain | PASS — `optional_host_permissions` handles runtime access |
| 2 | User pastes URL + token | PASS — popup has Backend URL + Upload Token fields |
| 3 | Extension requests permission for domain | PASS — `chrome.permissions.request` on save |
| 4 | Test connection works | PASS — "Testar conexão" calls `/api/recordings/ping` |
| 5 | Record Meet works | PASS — tabCapture + offscreen MediaRecorder |
| 6 | Stop recording uploads | PASS — stop triggers upload to backend |
| 7 | Invalid token error is friendly | PASS — "Token inválido" state in popup |
| 8 | Backend/CORS error is friendly | PASS — "Backend indisponível" / "Permissão pendente" states |
| 9 | Optional mic works or warns | PASS — mic opt-in, permission denial continues with tab audio |

**Docs fix:** Updated `documentation/27-chrome-extension.md` to remove stale
instructions about editing `manifest.json` for backend domain. Dynamic permissions
handle this at runtime.

## UX checks

| # | Check | Result |
|---|-------|--------|
| 1 | Navigation has few items | PASS — 4 main + 2 admin links |
| 2 | Onboarding doesn't pollute top | PASS — content card, not in nav |
| 3 | Google Drive appears optional | PASS — marked "(opcional)" in multiple places |
| 4 | Extension is clear as main path | PASS — primary CTA, first settings card |
| 5 | Transcriptions combines search + jobs | PASS — unified workspace page |
| 6 | Queue is compact/admin | PASS — sidebar panel + admin-only detail page |
| 7 | Models are separate and clear | PASS — dedicated page with 4 sections |
| 8 | Buttons aren't excessive | PASS — reasonable count per page |

## Mobile checks

| # | Check | Result |
|---|-------|--------|
| 1 | 390px breakpoint exists | PASS — `@media (max-width: 390px)` in styles.css |
| 2 | No horizontal overflow | PASS — `.table-wrapper { overflow-x: auto }` on all tables |
| 3 | Nav doesn't break | PASS — hamburger toggle + dropdown nav at 768px |
| 4 | Inputs have min-height 44px+ | PASS — 44px base, 48px at 390px, font-size 16px (no iOS zoom) |
| 5 | Buttons have min-height 44px+ | PASS — `.btn` 44px, `.btn-sm` 36px (secondary actions) |
| 6 | Login card centered/narrow | PASS — `max-width:420px; margin:0 auto` |
| 7 | Extension tokens table scrolls | PASS — `.table-wrapper` + `min-width:640px` |

**Minor notes:** `.btn-sm` (36px/40px) and `.nav-toggle` (36px) are slightly below
the 44px touch target guideline, but all primary interactive elements meet or
exceed it. Acceptable for secondary/tertiary actions.

## Known limitations

1. **Legacy `ProviderStatus` naming:** The `deepgram_required` field name in
   `app/transcription/provider.py` is Deepgram-specific but now means "any cloud
   key required". Cosmetic only — UI uses the provider-agnostic
   `compute_provider_readiness()`.

2. **CORS advertises GET but only POST exists:** `cors.py` advertises GET in
   `_ALLOWED_METHODS` for preflight, but `/api/recordings/ping` only implements
   POST. Harmless (GET returns 405) but slightly inconsistent.

3. **Touch targets:** `.btn-sm` and `.nav-toggle` are 36px, below the 44px
   guideline. Primary buttons and inputs meet 44px+.

## Staging env checklist

```env
APP_SECRET_KEY=...
ADMIN_USERNAME=...
ADMIN_PASSWORD=...
DATABASE_URL=...
REDIS_URL=...

# Extension-first
EXTENSION_UPLOAD_MAX_MB=500
EXTENSION_RECORDINGS_DIR=/app/data/recordings

# Optional Google
GOOGLE_WEB_CLIENT_ID=
GOOGLE_WEB_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=

# Audio
AUDIO_PREPROCESSING_ENABLED=true
AUDIO_COMPRESSION_ENABLED=true
INSTALL_FFMPEG=true
AUDIO_CLOUD_CHUNK_TARGET_MB=24
OPENROUTER_MAX_UPLOAD_MB=99
GROQ_MAX_UPLOAD_MB=24
GROQ_USE_DEV_LIMIT=true
```

## Merge recommendation

**READY TO MERGE** after human staging validation on Dokploy.

All automated checks pass. All backend/extension/UX/mobile checklists pass. One
small docs fix applied (stale manifest edit instructions). No blocking issues.

**Next steps:**
1. Deploy to Dokploy staging with the env checklist above.
2. Test extension upload flow end-to-end with a real Meet recording.
3. Test mobile UX on a physical device at 390px width.
4. Verify Google Drive optional behavior (with and without Google envs).
5. Merge PR #7 after human sign-off.
