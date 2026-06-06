# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Onboarding wizard** at `/onboarding`: a guided, 7-step setup with a live
  checklist (Google connected, Drive folder valid, provider valid, queue online,
  worker online, automation active) computed from the user's real state.
- **Friendly error UI**: a reusable error component (error code, human message,
  suggested action, docs link, retry button) and an HTML error handler for browser
  navigation that never exposes a stack trace and preserves the login redirect.
- **Operational endpoints**: `GET /health` (liveness, no auth), `GET /ready`
  (Postgres + schema/migrations + queue backend, returns 503 when degraded), and
  `GET /version` (app version, commit, build time, provider posture).
- **Structured, secret-free logging** (`app/observability`): `log_event` with
  consistent fields (`event`, `job_id`, `user_id`, `provider`, `duration_seconds`,
  `error_code`, `retryable`), automatic redaction of secret-looking fields, and an
  optional JSON format via `LOG_FORMAT=json`.
- **Outbound webhooks** (`app/webhooks`, optional): `WEBHOOK_URL` +
  `WEBHOOK_EVENTS` (`job.completed`, `job.failed`). Delivery is best-effort, never
  blocks a job, retries transient failures (429/5xx/network), and the payload is
  secret-free.
- **Transcript exports**: download a completed job's transcript as `txt`, `json`,
  `srt`, `vtt`, or `md` via `GET /jobs/{id}/download?format=...` (PDF documented as
  a future format).
- **User-scoped transcript search**: `GET /search` over `transcript_text`
  (case-insensitive substring in the in-memory fake; PostgreSQL full-text search
  backed by a new GIN index migration `0002`).
- **Meeting summaries scaffold** (`app/summaries`): provider contract, config, and
  status — off by default, no LLM call yet (roadmap).
- **Repo hygiene**: `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, this
  changelog, GitHub issue templates, and a pull request template.
- **Documentation**: new guides `33-onboarding`, `34-observability`,
  `35-webhooks`, `36-export-formats`, `37-security`, `38-e2e-testing`, plus an
  `tests/e2e/` integrated end-to-end suite.
- Error classes now carry a stable `code`, a `retryable` flag, and an optional
  `doc_url` for the friendly-error UI and structured logging.

### Changed
- `app/logger.py` now delegates to `app/observability.configure_logging` and honors
  `LOG_FORMAT` (`text` default, or `json`).
- The transcript download route serves multiple export formats while keeping the
  default TXT behavior unchanged.
- `docker-compose.yml` passes the new observability/webhook/summary env vars and
  adds a `/health` healthcheck to the web service.

### Security
- Verified, with tests, that secrets never reach logs, the UI, webhook payloads,
  or error messages; tracebacks stay in server logs only.

### Notes
- No SQLite was introduced. PostgreSQL remains the single source of truth and the
  legacy CLI worker is unchanged.
