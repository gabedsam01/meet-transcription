<!--
Thanks for contributing! Keep PRs focused. See CONTRIBUTING.md for conventions.
-->

## Summary

<!-- What does this PR change and why? Link any related issue (e.g. Closes #123). -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Docs only
- [ ] Refactor / chore
- [ ] Migration (Alembic)

## How was it tested?

<!-- Commands run and what you observed. -->

```
.venv/bin/python -m pytest -v
.venv/bin/python -m compileall app scripts
docker compose config
docker compose build
```

## Ground-rules checklist

- [ ] **PostgreSQL is the source of truth** — no SQLite / `sqlite3` / `app.db`.
- [ ] **No heavy transcription in an HTTP request** — routes only validate, create a `pending` job, and enqueue.
- [ ] **Secrets stay encrypted at rest** and are **never logged, shown in the UI, or in errors**; no tracebacks in the UI.
- [ ] **UI is server-rendered Jinja2** — no React/SPA, no CDN, no build step.
- [ ] **Redis is queue/lock only**; anything it loses is recoverable from Postgres.
- [ ] **Legacy CLI worker** (`python -m app.main`) still works.
- [ ] If a repository Protocol changed, both adapters (memory + postgres) and `tests/test_core_ports.py::_Stub` were updated.
- [ ] New behavior has tests; secret-not-logged regression added where relevant.
- [ ] `CHANGELOG.md` updated under **Unreleased** (for user-facing changes).
- [ ] Docs updated (`documentation/` + the index in `00-overview.md`).

## Notes for reviewers

<!-- Anything to call out: trade-offs, follow-ups, screenshots. -->
