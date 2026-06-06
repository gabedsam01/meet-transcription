# Contributing to Meet Transcription

Thanks for your interest in improving Meet Transcription! This guide covers the
project's conventions and the checks your change must pass.

## Ground rules (non-negotiable)

These mirror [`CLAUDE.md`](CLAUDE.md) and the architecture docs. A PR that breaks
one of them will not be merged:

1. **PostgreSQL is the single source of truth — no SQLite.** New code depends on
   the repository interfaces (`app/core/ports.py`, `app/web/repositories.py`).
   Tests use the in-memory dict-backed fakes, never `sqlite3`.
2. **Never transcribe in an HTTP request.** Routes validate, create a `pending`
   job, and enqueue its id. Download/transcribe/upload happen only in the worker.
3. **Secrets are encrypted at rest** (Fernet via `app/web/security.py`) and
   **never logged, shown in the UI, or put in errors/commits**. No stack traces in
   the UI.
4. **The UI is server-rendered Jinja2** with local CSS — no React/SPA, no CDN, no
   build step.
5. **Redis is the queue/lock, not a database.** Anything Redis loses must be
   recoverable from Postgres.
6. **Don't break the legacy CLI worker** (`python -m app.main`).

## Getting set up

```bash
git clone <your-fork>
cd meet-transcription
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env   # fill in secrets for a real run
```

Most tests use in-memory fakes and need no services. The PostgreSQL integration
tests skip automatically unless `TEST_DATABASE_URL` (or `DATABASE_URL`) points at
a reachable database — see `tests/conftest.py`.

## Branch & commit conventions

- Branch from `main` with a descriptive name, e.g. `feat/...`, `fix/...`,
  `docs/...`.
- Keep commits focused and write clear messages (imperative mood).
- Update `CHANGELOG.md` under **Unreleased** for user-facing changes.

## Validation (run before opening a PR)

```bash
.venv/bin/python -m pytest -v
.venv/bin/python -m compileall app scripts
docker compose config        # needs a local .env (cp .env.example .env)
docker compose build
```

All of the above must pass. CI (`.github/workflows/docker-publish.yml`) runs
`compileall` + `pytest` on every PR and only publishes the image after they pass.

## Testing conventions

- New behavior needs tests. Unit-test pure logic; use the FastAPI `TestClient`
  with the in-memory fakes for routes (see `tests/test_web_routes.py`).
- For end-to-end flows, follow the integrated style in `tests/e2e/` (real app +
  real `JobProcessor` driven through fakes — see `tests/e2e/helpers.py`).
- If you add a method to a repository Protocol, implement it in **both** adapters
  (memory + postgres) and update `tests/test_core_ports.py::_Stub`.
- Add a regression test that secrets are not logged when touching logging,
  webhooks, or error handling.

## Migrations

Schema changes go through Alembic (`alembic/versions/`). Hand-write the migration
(the existing ones are hand-authored so they can be reviewed without a live DB),
chain `down_revision` to the current head, and provide a working `downgrade()`.

## Documentation

Update the numbered guides in `documentation/` (and the index table in
`documentation/00-overview.md`) when you change behavior. Match the existing
operational tone: explain *why*, link related docs, and include risk-if-wrong
notes for configuration.

## Code review

PRs are reviewed for correctness, the ground rules above, test coverage, and docs.
Be ready to discuss trade-offs. By contributing you agree your work is licensed
under the repository's [LICENSE](LICENSE).
