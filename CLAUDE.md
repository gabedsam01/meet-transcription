# CLAUDE.md

Guidance for Claude Code (and humans) working in this repository.

## What this is

Meet Transcription watches a Google Drive folder for Google Meet recordings,
sends the MP4 to Deepgram, and uploads a plain-text transcript back to Drive.
It ships in two forms:

- a **web app** (FastAPI) for signing in, connecting Google, configuring folders,
  and triggering/inspecting jobs; and
- a **worker** that processes transcription jobs out of band.

## Architecture

Target architecture is three containers (see `docker-compose.yml`):

- **web** — `uvicorn app.web.main:app` (HTTP, OAuth, UI). Code in `app/web/`.
- **worker** — `python -m app.worker.main` (DB-driven job processor). *Owned by
  the `feat/postgres-worker` branch; not present on every branch yet.*
- **postgres** — production database.

The **legacy worker** is the original env-driven CLI `python -m app.main`
(`--once` / `--watch` / `--reprocess`). It stores state in
`data/processed_files.json` and does not use the web database.

## Hard rules

1. **Do not break the legacy worker CLI** (`python -m app.main`). Its flags and
   behavior must keep working; it is still a supported deployment.
2. **PostgreSQL is the single source of truth — NO SQLite in the architecture.**
   The SQLAlchemy repository layer defined by `feat/postgres-core` is the
   contract; `app/db.py` / `sqlite3` is legacy and is being removed. New code
   depends on repository interfaces; tests use dict-backed in-memory fakes
   (never sqlite in-memory).
3. **Tokens and API keys are always encrypted at rest** (Fernet via
   `app/web/security.py`, key derived from `APP_SECRET_KEY`). Never store Google
   tokens or Deepgram keys in plaintext.
4. **Never run transcription inside an HTTP request.** Download/Deepgram/upload
   must happen in the worker or a background task, never synchronously in a
   route handler. The request path only validates and enqueues.
5. **The UI does not use React** (or any SPA framework). It is server-rendered
   Jinja2 templates in `app/web/templates/` with local CSS in
   `app/web/static/styles.css`. No CDN assets, no build step.
6. **web and worker are separate services** sharing one image with different
   commands. Keep web (request/response) and worker (long-running jobs) concerns
   separate.
7. **Never commit secrets.** `.env`, `secrets/*.json`, `token.json`,
   `data/app.db`, and `data/processed_files.json` are git-ignored and must stay
   that way.
8. **Run the tests before finishing.** See validation commands below.

## Conventions

- Long Drive ids and ISO timestamps must not blow out layout: ids render
  truncated/monospace (`mid`/`mono`), timestamps via the `dt` filter; full
  values live on the job detail page. Helpers are in `app/web/helpers.py`.
- DB access ultimately goes through the repository interfaces from
  `feat/postgres-core`. On this branch the legacy `app/db.py` is a temporary
  bridge; UI job reads are user-scoped and use the contract method name
  `get_job` (matches the worker branch's `JobRepository`).

## Validation commands

```bash
python -m pytest -v
python -m compileall app scripts
docker compose config        # needs a local .env (cp .env.example .env)
docker compose build
```

## Scope note (multi-branch effort)

Work is split across branches forked from the same commit:

- `feat/ui-devops-polish` — this branch: UI, Docker, CI, docs.
- `feat/auth-users-settings` — auth, users/roles, per-user Google OAuth,
  **per-user encrypted Deepgram key (no env fallback)**, Drive settings by URL.
- `feat/postgres-core` — SQLAlchemy + PostgreSQL repositories and tables. The
  `JobRepository` contract names: `create_job`, `get_job`,
  `claim_next_pending_job`, `mark_completed`, `mark_failed`, `find_existing_job`,
  `completed_source_file_ids`, `reset_stale_processing_jobs`,
  `list_jobs_for_user`. Do not invent conflicting names.
- `feat/postgres-worker` — the real `app.worker.main` job processing.

When touching the database, per-user Deepgram keys, or the worker module,
coordinate with those branches rather than duplicating the work here.
