# Chrome Recorder Dynamic Permissions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let ordinary Chrome extension users paste a backend URL and upload token without editing `manifest.json`, while preserving secure token handling and extension-first upload flow.

**Architecture:** Keep backend behavior unchanged because `/api/recordings/ping`, `/api/recordings/upload`, per-user token auth, and scoped extension CORS already exist. Move host access to MV3 optional host permissions, add small pure helper modules for URL validation and API form construction, and wire the popup/background/offscreen flow through those helpers.

**Tech Stack:** Chrome MV3 extension, vanilla HTML/CSS/JS modules, Node's built-in `node:test` runner for dependency-free JS tests, existing Python/FastAPI backend tests.

---

## File Structure

- Modify `chrome-extension/meet-audio-recorder/manifest.json`: remove hardcoded backend host permissions and add `optional_host_permissions` for runtime backend grants.
- Create `chrome-extension/meet-audio-recorder/src/config.js`: pure backend URL normalization and optional-permission origin helpers.
- Modify `chrome-extension/meet-audio-recorder/src/api.js`: add ping client, use `upload_token` in `FormData`, add metadata fields, and map friendly errors.
- Modify `chrome-extension/meet-audio-recorder/src/popup.html`: expose settings and test controls clearly.
- Modify `chrome-extension/meet-audio-recorder/src/popup.js`: request runtime host permission on save, call ping, render connection/status states.
- Modify `chrome-extension/meet-audio-recorder/src/background.js`: validate saved config/ping before recording, expose test connection to popup, pass include-mic/version metadata.
- Modify `chrome-extension/meet-audio-recorder/src/offscreen.js`: include upload metadata values from background.
- Modify `chrome-extension/meet-audio-recorder/src/styles.css`: support clearer popup layout and status/error states.
- Create `chrome-extension/meet-audio-recorder/test/*.test.mjs`: dependency-free unit tests for pure helpers.
- Modify `chrome-extension/meet-audio-recorder/README.md`: document no manifest editing, token setup, connection test, and recording flow.
- Modify `overview/qa-next-platform-features-v2.md`: document PR #7 extension runtime-permission fix.

## Tasks

### Task 1: Test URL Normalization And Upload Form Behavior

**Files:**
- Create: `chrome-extension/meet-audio-recorder/test/config.test.mjs`
- Create: `chrome-extension/meet-audio-recorder/test/api.test.mjs`
- Create: `chrome-extension/meet-audio-recorder/test/run-tests.mjs`
- Later modify: `chrome-extension/meet-audio-recorder/src/config.js`
- Later modify: `chrome-extension/meet-audio-recorder/src/api.js`

- [ ] **Step 1: Write failing JS tests**

Add tests that import helpers which do not exist yet:

```js
import test from "node:test";
import assert from "node:assert/strict";
import { normalizeBackendUrl, backendOriginPattern } from "../src/config.js";

test("normalizes https backend URL and builds exact origin permission", () => {
  const normalized = normalizeBackendUrl("https://staging-auto.gabedsam01.shop/");
  assert.equal(normalized, "https://staging-auto.gabedsam01.shop");
  assert.equal(backendOriginPattern(normalized), "https://staging-auto.gabedsam01.shop/*");
});

test("allows localhost http URL for development", () => {
  const normalized = normalizeBackendUrl("http://localhost:8000/");
  assert.equal(normalized, "http://localhost:8000");
  assert.equal(backendOriginPattern(normalized), "http://localhost:8000/*");
});

test("rejects non-https non-localhost backends", () => {
  assert.throws(
    () => normalizeBackendUrl("http://example.com"),
    /Informe uma URL válida começando com https:\/\//,
  );
});
```

Add API tests for `buildRecordingForm` and `describeFetchError` behavior.

- [ ] **Step 2: Verify RED**

Run: `node chrome-extension/meet-audio-recorder/test/run-tests.mjs`

Expected: FAIL because `src/config.js` and exported API helpers are missing.

- [ ] **Step 3: Implement minimal helpers**

Create `config.js`; export `normalizeBackendUrl`, `backendOriginPattern`, and `isLocalhostUrl`. Update `api.js` to export `pingBackend`, `buildRecordingForm`, `describeHttpError`, and `describeFetchError`, and to upload token in `FormData`.

- [ ] **Step 4: Verify GREEN**

Run: `node chrome-extension/meet-audio-recorder/test/run-tests.mjs`

Expected: PASS.

### Task 2: Runtime Permissions And Popup UX

**Files:**
- Modify: `chrome-extension/meet-audio-recorder/manifest.json`
- Modify: `chrome-extension/meet-audio-recorder/src/popup.html`
- Modify: `chrome-extension/meet-audio-recorder/src/popup.js`
- Modify: `chrome-extension/meet-audio-recorder/src/styles.css`

- [ ] **Step 1: Update manifest**

Set `host_permissions` to only `https://meet.google.com/*`. Add `optional_host_permissions` with `https://*/*` and Chrome's MV3-compatible any-port localhost pattern, `http://localhost/*`.

- [ ] **Step 2: Wire save flow**

On save, normalize the URL, request `chrome.permissions.request({ origins: [backendOriginPattern(normalized)] })`, show the exact denied-permission message if rejected, and save only after permission is granted.

- [ ] **Step 3: Wire test connection**

Add `Testar conexão` button that calls background `test-connection`, which pings `/api/recordings/ping` and displays `Conectado como <email>` or a friendly token/backend error.

- [ ] **Step 4: Render clear states**

Render the required labels: `Parado`, `Gravando`, `Enviando`, `Upload concluído`, `Erro no upload`, `Token inválido`, `Backend indisponível`, and `Permissão pendente` through status text and CSS classes.

- [ ] **Step 5: Run manifest and JS tests**

Run: `python -m json.tool chrome-extension/meet-audio-recorder/manifest.json >/tmp/manifest.ok`

Run: `node chrome-extension/meet-audio-recorder/test/run-tests.mjs`

Expected: both PASS.

### Task 3: Background/Offscreen Upload Flow

**Files:**
- Modify: `chrome-extension/meet-audio-recorder/src/background.js`
- Modify: `chrome-extension/meet-audio-recorder/src/offscreen.js`
- Modify: `chrome-extension/meet-audio-recorder/src/api.js`

- [ ] **Step 1: Validate config before recording**

Before capturing the tab, load saved config, normalize it, verify host permission is still granted, and ping the backend. If missing or invalid, show friendly errors without starting capture.

- [ ] **Step 2: Upload complete metadata**

Pass `include_microphone`, `extension_version`, `mime_type`, `meeting_url`, `started_at`, `ended_at`, and `duration_seconds` into `buildRecordingForm`.

- [ ] **Step 3: Preserve microphone behavior**

Keep existing mixer behavior: tab audio always records; microphone is mixed only when enabled; if mic permission is denied, continue tab-only and surface a friendly warning.

- [ ] **Step 4: Run targeted tests**

Run: `node chrome-extension/meet-audio-recorder/test/run-tests.mjs`

Run: `.venv/bin/python -m pytest tests/test_cors_middleware.py tests/test_extension_tokens.py tests/e2e/test_extension_flow_e2e.py -v`

Expected: PASS.

### Task 4: Documentation And Required Validation

**Files:**
- Modify: `chrome-extension/meet-audio-recorder/README.md`
- Modify: `overview/qa-next-platform-features-v2.md`

- [ ] **Step 1: Update extension README**

Document unpacked installation, token generation at `/extensao`, pasting backend URL/token, runtime permission prompt, `Testar conexão`, recording Meet, and viewing transcription. State that editing `manifest.json` per domain is no longer necessary.

- [ ] **Step 2: Update QA overview**

Add a PR #7 entry summarizing optional host permissions, runtime permission requests, ping-before-record, FormData token upload, and clearer UX/errors.

- [ ] **Step 3: Run requested extension checks**

Run: `node --version || true`

Run: `find chrome-extension/meet-audio-recorder -type f -maxdepth 4 -print`

Run: `python -m json.tool chrome-extension/meet-audio-recorder/manifest.json >/tmp/manifest.ok`

Run package commands only if a package file appears; none exists at plan time.

- [ ] **Step 4: Run minimum full validations**

Run: `git status`

Run: `alembic heads`

Run: `.venv/bin/python -m pytest -v`

Run: `.venv/bin/python -m pytest tests/e2e -v`

Run: `.venv/bin/python -m compileall app scripts || .venv/bin/python -m compileall app`

Run: `docker compose config`

Run: `docker compose build`

- [ ] **Step 5: Commit, push, and watch PR checks**

Run: `git add .`

Run: `git commit -m "improve chrome recorder setup and dynamic permissions"`

Run: `git push`

Run: `gh pr checks 7 --watch`

Expected: command completes or any failure is reported clearly.

## Self-Review

- Spec coverage: manifest optional host permissions, runtime permission, URL normalization, ping, upload FormData token, metadata, mic behavior, friendly errors, README, overview, tests, validations, commit, push, and PR checks are all covered.
- Placeholder scan: no placeholders or incomplete task descriptions remain.
- Type consistency: helper names are stable across tasks: `normalizeBackendUrl`, `backendOriginPattern`, `pingBackend`, `buildRecordingForm`, `describeHttpError`, and `describeFetchError`.
