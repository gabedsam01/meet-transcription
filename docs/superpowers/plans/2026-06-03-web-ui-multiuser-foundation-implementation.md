# Web UI Multiuser Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a FastAPI web UI, SQLite persistence, encrypted per-user OAuth token storage, and manual per-user processing without breaking the existing CLI worker.

**Architecture:** Keep the current CLI worker modules untouched except for reusable helper additions. Add `app/db.py` for all SQLite access, `app/web/` for FastAPI routes/templates/static CSS, and `app/web/services.py` for web-mode processing using stored OAuth tokens. Web mode is additive; simple worker mode continues using env config, `DriveClient`, `DeepgramClient`, `FileProcessor`, and JSON state.

**Tech Stack:** Python 3.11+, FastAPI, Starlette sessions, Jinja2 templates, local CSS, SQLite WAL, cryptography Fernet, Google OAuth libraries, pytest, Docker Compose.

---

## File Map

- Create `app/db.py`: SQLite connection helper, schema initialization, WAL/busy timeout, repository functions for users/settings/tokens/jobs.
- Create `app/web/__init__.py`: web package marker.
- Create `app/web/config.py`: web-only environment parsing for admin credentials, app secret, OAuth web credentials, DB path, and cookie settings.
- Create `app/web/security.py`: Fernet key derivation, token encryption/decryption, session helpers.
- Create `app/web/token_store.py`: encrypted OAuth token persistence abstraction.
- Create `app/web/services.py`: web run-once service that processes only the authenticated user and writes job records.
- Create `app/web/main.py`: FastAPI app, route registration, startup DB init, auth redirects, OAuth flow, settings/jobs routes.
- Create templates under `app/web/templates/`: `base.html`, `login.html`, `dashboard.html`, `settings.html`, `jobs.html`, `error.html`.
- Create `app/web/static/styles.css`: local responsive CSS.
- Modify `app/drive_client.py`: add a constructor/helper for credentials/service from stored OAuth credentials or explicit folder IDs without changing current constructor behavior.
- Modify `requirements.txt`: add FastAPI, Uvicorn, Jinja2, python-multipart, itsdangerous/session dependency if needed, cryptography.
- Modify `.env.example`: add web env vars and keep existing simple worker vars.
- Modify `docker-compose.yml`: replace single service with `worker` and `web` services sharing `/app/data`.
- Modify `README.md`: add Simple Worker Mode, Web UI Mode, Single User, Multi User Roadmap, Google OAuth Setup, Dokploy Deployment.
- Add tests: `tests/test_db.py`, `tests/test_web_config.py`, `tests/test_token_store.py`, `tests/test_web_routes.py`, `tests/test_web_services.py`.

## Task 1: Web Config And Secret Derivation

**Files:**
- Create: `tests/test_web_config.py`
- Create: `tests/test_token_store.py`
- Create: `app/web/__init__.py`
- Create: `app/web/config.py`
- Create: `app/web/security.py`
- Create: `app/web/token_store.py`

- [ ] **Step 1: Write failing tests for web config and Fernet encryption**

Tests must assert:

```python
from app.web.config import WebSettings
from app.web.security import decrypt_value, encrypt_value, fernet_from_secret


def test_web_settings_requires_admin_and_secret(tmp_path):
    settings = WebSettings.from_env({
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD": "secret",
        "APP_SECRET_KEY": "a-long-secret-for-tests",
        "SESSION_COOKIE_SECURE": "false",
        "GOOGLE_WEB_CLIENT_ID": "client-id",
        "GOOGLE_WEB_CLIENT_SECRET": "client-secret",
        "GOOGLE_REDIRECT_URI": "http://localhost:8000/oauth/google/callback",
        "DATABASE_URL": str(tmp_path / "app.db"),
        "DEEPGRAM_API_KEY": "dg-key",
        "TMP_DIR": str(tmp_path / "tmp"),
    })
    assert settings.admin_username == "admin"
    assert settings.session_cookie_secure is False
    assert settings.database_path.name == "app.db"


def test_encrypt_value_does_not_store_plaintext():
    fernet = fernet_from_secret("a-long-secret-for-tests")
    encrypted = encrypt_value(fernet, "secret-token")
    assert encrypted != "secret-token"
    assert decrypt_value(fernet, encrypted) == "secret-token"
```

- [ ] **Step 2: Verify tests fail before implementation**

Run: `python -m pytest tests/test_web_config.py tests/test_token_store.py -v`
Expected: import failures for `app.web` modules.

- [ ] **Step 3: Implement config and encryption modules**

Implement `WebSettings.from_env()`, boolean parsing, default `DATABASE_URL=/app/data/app.db`, default `TMP_DIR=/app/tmp`, and Fernet derivation via SHA-256 over `APP_SECRET_KEY`.

- [ ] **Step 4: Verify tests pass**

Run: `python -m pytest tests/test_web_config.py tests/test_token_store.py -v`
Expected: pass.

## Task 2: SQLite Layer And TokenStore

**Files:**
- Create: `tests/test_db.py`
- Create/modify: `tests/test_token_store.py`
- Create: `app/db.py`
- Modify: `app/web/token_store.py`

- [ ] **Step 1: Write failing DB tests**

Tests must assert schema creation, WAL mode, busy timeout, admin user upsert, settings persistence, job lifecycle, and encrypted token storage through `TokenStore`.

- [ ] **Step 2: Verify tests fail**

Run: `python -m pytest tests/test_db.py tests/test_token_store.py -v`
Expected: failures for missing DB functions/classes.

- [ ] **Step 3: Implement `app/db.py`**

Implement `connect_db(path)`, `init_db(path)`, and repository helpers: `get_or_create_user`, `get_user_by_id`, `get_settings`, `save_settings`, `create_job`, `update_job`, `list_jobs`, `get_latest_jobs`, `save_google_token`, `get_google_token`.

- [ ] **Step 4: Implement `TokenStore`**

`TokenStore` encrypts `access_token`, `refresh_token`, and `client_secret`, stores OAuth metadata, and returns decrypted token dicts to service code.

- [ ] **Step 5: Verify DB/token tests pass**

Run: `python -m pytest tests/test_db.py tests/test_token_store.py -v`
Expected: pass.

## Task 3: Web Routes, Sessions, Templates, Static CSS

**Files:**
- Create: `tests/test_web_routes.py`
- Create: `app/web/main.py`
- Create templates and CSS under `app/web/templates/` and `app/web/static/styles.css`
- Modify: `requirements.txt`

- [ ] **Step 1: Write failing route tests**

Tests must use FastAPI `TestClient` and assert:
- `GET /health` returns `{"status":"ok"}`.
- `GET /` redirects to `/login` when unauthenticated.
- `POST /login` with valid credentials redirects to `/` and sets an HttpOnly session cookie.
- Authenticated `GET /settings` and `GET /jobs` return 200.
- `GET /connect-google` redirects to Google auth URL and stores OAuth state in session.
- `GET /oauth/google/callback` rejects mismatched state.

- [ ] **Step 2: Verify route tests fail**

Run: `python -m pytest tests/test_web_routes.py -v`
Expected: import failures for `app.web.main`.

- [ ] **Step 3: Implement FastAPI app and templates**

Use `SessionMiddleware`, local templates, local CSS, startup DB init, protected-route dependency, login/logout, dashboard/settings/jobs pages, OAuth state creation, and callback state validation.

- [ ] **Step 4: Verify route tests pass**

Run: `python -m pytest tests/test_web_routes.py -v`
Expected: pass.

## Task 4: Web Run-Once Processing Service

**Files:**
- Create: `tests/test_web_services.py`
- Create: `app/web/services.py`
- Modify: `app/drive_client.py` only if needed for a non-breaking helper.

- [ ] **Step 1: Write failing service tests**

Tests must assert `run_once_for_user()` requires settings and tokens, creates job records, processes only files for that user, marks completed with `transcript_drive_file_id`, marks failed with `error_message`, and uses global Deepgram key.

- [ ] **Step 2: Verify service tests fail**

Run: `python -m pytest tests/test_web_services.py -v`
Expected: import failures for `app.web.services` or missing functions.

- [ ] **Step 3: Implement processing service**

Build OAuth credentials from token dict, create a Drive client for explicit source/destination folders, call existing Deepgram client and transcript formatting logic, write temp files under configured tmp dir, upload TXT, and update jobs.

- [ ] **Step 4: Wire `POST /jobs/run-once`**

Route calls `run_once_for_user()` for the authenticated user and redirects back to `/jobs` with a flash/status message.

- [ ] **Step 5: Verify service and route tests pass**

Run: `python -m pytest tests/test_web_services.py tests/test_web_routes.py -v`
Expected: pass.

## Task 5: Docker, README, Compatibility Verification, Browser Smoke

**Files:**
- Modify: `Dockerfile`, `docker-compose.yml`, `.env.example`, `README.md`, tests if needed.

- [ ] **Step 1: Update runtime dependencies and compose**

Add web dependencies to `requirements.txt`. Ensure Docker image can run both `python -m app.main --watch` and `uvicorn app.web.main:app --host 0.0.0.0 --port 8000`. Update Compose with `worker` and `web` services sharing `/app/data`.

- [ ] **Step 2: Update README and env example**

Document Simple Worker Mode, Web UI Mode, Single User, Multi User Roadmap, Google OAuth Setup, Dokploy Deployment, required web env vars, and existing worker compatibility.

- [ ] **Step 3: Run full validation**

Run:

```bash
python -m pytest -v
python -m compileall app scripts
docker compose config
docker compose build
```

Expected: all pass. Use a temporary placeholder `.env` for Compose validation if needed, then remove it.

- [ ] **Step 4: Browser smoke validation**

Start local server with placeholder env vars and run:

```bash
uvicorn app.web.main:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000`, `/login`, `/settings`, `/jobs`, and `/health`. Confirm pages load, protected pages redirect before login, login works, forms render, and health returns JSON.

- [ ] **Step 5: Final status**

Do not push. Report files created/changed, routes, tables, commands, test/build results, local run instructions, Dokploy setup, and known limitations.
