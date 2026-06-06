# Observability: Health, Readiness, Version, and Logging

meet-transcription is meant to run unattended on a CPU-bound VPS, behind a load
balancer and an orchestrator (Dokploy / Docker Compose / Kubernetes). To deploy
and operate it safely you need three cheap, secret-free HTTP probes and one
discipline for logs. This page documents the three endpoints exposed by the web
service — `GET /health`, `GET /ready`, `GET /version` — and the structured,
**secret-free** logging used by both web and worker.

Two hard rules shape everything here: **PostgreSQL is the single source of
truth** (no SQLite — the readiness probe checks Postgres, never a local file),
and **no secret is ever logged, shown in the UI, or placed in an error
message**. Tracebacks stay in the logs; the UI and webhook payloads only ever
receive friendly messages.

The endpoints live in `app/web/main.py` (`health` at line 193, `ready` at line
199, `version` at line 235, with the helpers `_queue_status` at line 171 and
`_queue_backend_name` at line 182). Logging lives in `app/observability/__init__.py`
(driven by `app/logger.py`) and version metadata in `app/version.py`.

See also: [Redis Queue and Lock](09-redis-queue.md),
[Postgres and Migrations](10-postgres-and-migrations.md),
[Troubleshooting](15-troubleshooting.md), [Security](37-security.md),
[Webhooks](35-webhooks.md).

## The three endpoints at a glance

| Method & path | Auth | Touches DB / Redis | Purpose | Status codes |
| --- | --- | --- | --- | --- |
| `GET /health` | none | no | Liveness — is the process up? | `200` |
| `GET /ready` | none | yes (Postgres + queue) | Readiness — can it serve real work? | `200` ready / `503` degraded |
| `GET /version` | none | no | Build + provider posture | `200` |

All three are unauthenticated by design so a load balancer or orchestrator can
probe them without credentials, and all three are deliberately secret-free.

## `GET /health` — liveness

Liveness only: it confirms the Python process is up and serving HTTP. It does
**not** touch Google OAuth, PostgreSQL, or Redis, so a load balancer can probe
it as often as it likes at near-zero cost. Use it as the container liveness
probe — a failure here means "restart the process", not "wait".

```bash
curl -fsS http://localhost:8000/health
```

```json
{"status": "ok"}
```

The shape is fixed and asserted by tests. Wire it to a liveness probe with a
short interval; do **not** gate traffic on it (use `/ready` for that).

## `GET /ready` — readiness

Readiness answers a harder question: *can this instance actually do useful
work?* It checks the dependencies the web layer needs to enqueue and serve jobs
and **never raises** — every probe is wrapped so a broken dependency yields a
`503`, not a `500`. It runs three checks:

- **database** — calls `worker_repos.jobs.list_pending_jobs()` against
  PostgreSQL. A success means the DB is reachable.
- **migrations** — derived from the same query: a successful `jobs` query touched
  a migrated table, so a present schema implies `alembic upgrade head` ran. If
  the DB check fails the detail is `"schema unverified"`.
- **queue** — via `_queue_status()` / `_queue_backend_name()`. In `poll` mode
  (no Redis) the queue is always considered OK; in `queue` mode it reflects the
  Redis `health()` probe.

`is_ready` is `True` only when **database AND queue** are OK. When ready it
returns `200` with `{"status": "ready", ...}`; otherwise `503` with
`{"status": "degraded", ...}` so an orchestrator holds traffic until the dependency
recovers.

```bash
curl -i http://localhost:8000/ready
```

Healthy (Redis production mode), `200`:

```json
{
  "status": "ready",
  "checks": {
    "database": {"ok": true},
    "migrations": {"ok": true, "detail": "schema present"},
    "queue": {"ok": true, "mode": "queue", "backend": "redis"}
  }
}
```

Degraded (Postgres unreachable), `503`:

```json
{
  "status": "degraded",
  "checks": {
    "database": {"ok": false, "detail": "database unreachable"},
    "migrations": {"ok": false, "detail": "schema unverified"},
    "queue": {"ok": true, "mode": "queue", "backend": "redis"}
  }
}
```

In poll mode (`QUEUE_BACKEND=none`, no Redis) the queue check reports
`{"ok": true, "mode": "poll", "backend": "none"}` — losing Redis never makes a
poll-mode instance unready, because Postgres is still the source of truth and the
worker reconciles pending jobs (see [Redis Queue and Lock](09-redis-queue.md)).
The `backend` field is one of `redis`, `memory`, `queue`, or `none`.

Wire `/ready` to the readiness probe so the `migrate` service finishing and the
DB coming up gate traffic correctly. See
[Postgres and Migrations](10-postgres-and-migrations.md) for how migrations run.

## `GET /version` — build and provider posture

`/version` reports who you are running and which transcription posture is
active. It is public and secret-free — only `app`, `version`, `commit`,
`build_time`, and a `providers` summary. The base fields come from
`get_version_info()` in `app/version.py`; the deploy pipeline injects them as
environment variables so a built image always knows what it is.

| Env var | Default | Description |
| --- | --- | --- |
| `APP_VERSION` | `0.2.0` (hardcoded `APP_VERSION`) | Release version string; overrides the built-in default in a built image. |
| `GIT_COMMIT` / `GIT_SHA` | `unknown` | Commit SHA; `GIT_COMMIT` wins, then `GIT_SHA`, then `"unknown"`. |
| `BUILD_TIME` | `null` | ISO build timestamp; absent → `null`. |

```bash
curl -fsS http://localhost:8000/version
```

```json
{
  "app": "Meet Transcription",
  "version": "0.2.0",
  "commit": "1a2b3c4",
  "build_time": "2026-06-05T12:00:00Z",
  "providers": {
    "local_enabled": false,
    "local_valid": false,
    "deepgram_required": true,
    "queue_backend": "redis",
    "summaries_enabled": false
  }
}
```

The `providers` block reflects the live transcription posture
(`app.state.transcription_status`): whether the local CPU engine is enabled and
valid, whether a Deepgram key is therefore required, the queue backend, and
whether summaries are on. It exposes posture, never keys. Use it to confirm a
deploy shipped the commit you expect and that local-vs-Deepgram is configured as
intended.

## Structured logging

Both web and worker configure logging once at startup via `setup_logging()`
(`app/logger.py`), which delegates to `configure_logging()` in
`app/observability/__init__.py`. It is idempotent: it installs exactly one root
handler and only swaps the formatter, so handlers never stack up across reloads.

### Choosing the format: `LOG_FORMAT`

| Env var | Default | Values | Description |
| --- | --- | --- | --- |
| `LOG_FORMAT` | `text` | `text`, `json` | `json` emits one JSON object per line (for log shippers / ingestion); anything other than `json` falls back to `text` (human-readable). Resolved by `resolve_log_format()`. |

`text` gives `2026-06-05 12:00:00 [INFO] event=... key=value`; `json` gives one
object per line via `JsonLogFormatter`. Use `json` wherever a collector parses
your logs; `text` is friendlier for local development and `docker logs`.

### Event fields

Meaningful events are emitted through `log_event()` — one log line per event with
a consistent set of fields. The fields you will see:

| Field | Meaning |
| --- | --- |
| `event` | Event name, e.g. `transcription.started`. |
| `job_id` | The job being processed. |
| `user_id` | Owner of the job. |
| `provider` | Transcription provider label (e.g. `deepgram` or the local engine summary). |
| `duration_seconds` | Wall-clock processing time (rounded to ms). |
| `error_code` | Stable machine code on failure (from `app/errors.py`); `null` on success. |
| `retryable` | Whether the failure is worth retrying (`is_retryable`). |

In `text` format these fields are mirrored into the message string; in `json`
they become real top-level keys, so you can filter on `event` or `error_code`
directly in your log store.

### Worker lifecycle events

The worker's `JobProcessor.process()` (`app/worker/processor.py`) emits the job
lifecycle:

- **`transcription.started`** (line 56) — after the provider is resolved, with
  `job_id`, `user_id`, `provider`.
- **`transcription.completed`** (line 97) — on success, with `duration_seconds`,
  `error_code=None`, `retryable=False`.
- **`transcription.failed`** (line 111, `ERROR` level) — on any failure, with
  `error_code`, `retryable`, `duration_seconds`. The full traceback is logged
  separately via `LOGGER.exception(...)`; only the friendly `user_message` is
  stored on the job and sent to the UI/webhook.

These three events let you reconstruct throughput, latency
(`duration_seconds`), and failure rates per provider without parsing free text.
The `web` service never transcribes in-request, so these events come only from
the worker (see [Redis Queue and Lock](09-redis-queue.md)).

### Secrets are redacted by field name — in both formats

No secret is ever logged. `redact()` masks the **value** of any field whose
**name** contains a sensitive hint (case-insensitive), replacing it with `***`.
The hint list (`_SENSITIVE_HINTS`) is deliberately broad — better to redact one
field too many:

```
token, secret, password, passwd, key, authorization, auth,
credential, cookie, session, fernet
```

Note `key` also catches `api_key`, `deepgram_key`, and `app_secret_key`.
Redaction runs inside `log_event()` *before* anything is formatted, and again
defensively inside `JsonLogFormatter` for any ad-hoc `logger.info(..., extra=...)`
call — so even a careless `log_event(..., api_key=value)` cannot leak.

Text format:

```
2026-06-05 12:00:00 [INFO] event=deepgram.verify user_id=7 api_key=***
```

JSON format:

```json
{"ts": "2026-06-05T12:00:00+0000", "level": "INFO", "logger": "app.events", "message": "event=deepgram.verify user_id=7 api_key=***", "event": "deepgram.verify", "user_id": 7, "api_key": "***"}
```

In both cases the value is `***` — never the real key. Empty or `None` values
are left as-is (there is nothing to leak). This is the same encryption-at-rest
discipline applied to logs: Google tokens and Deepgram keys are encrypted in
PostgreSQL (Fernet via `app/web/security.py`) and never appear in plaintext, in
logs, in errors, or in the UI. See [Security](37-security.md) for the full
threat model and [Webhooks](35-webhooks.md) for how outbound payloads stay
secret-free.

## Operating notes

- **Probe wiring:** `/health` → liveness probe; `/ready` → readiness probe; gate
  traffic on `/ready` so the `migrate` one-shot and DB startup are respected.
- **`/ready` is intentionally slow-ish:** it issues one real DB query and a Redis
  `health()` probe. Keep its interval modest (e.g. 10s) so probes do not add
  load; keep `/health` fast and frequent.
- **Degraded ≠ dead:** a `503` from `/ready` while `/health` is `200` means
  "process alive, dependency down" — fix Postgres/Redis, do not restart blindly.
- **Diagnosing failures:** filter logs on `event=transcription.failed` and read
  `error_code` / `retryable`; the matching traceback is the adjacent
  `LOGGER.exception` line. Cross-reference the `user_message` shown in the UI
  with the `error_code` here — see [Troubleshooting](15-troubleshooting.md).
- **Confirming a deploy:** `curl /version` and check `commit` / `build_time`
  match what you shipped, and that `providers` matches your intended
  local-vs-Deepgram posture.
