# Auto-Polling (Per-User Drive Watcher)

Auto-polling lets each user opt in to **automatic** transcription: instead of
clicking "Run once", the worker periodically lists that user's source Drive
folder and enqueues any new media on their behalf. It is the automation layer on
top of the existing PostgreSQL-authoritative, Redis-queue worker.

This page is grounded in `app/worker/auto_poll.py`,
`app/services/drive_watcher.py`, `app/worker/config.py`,
`app/database/models.py` (`UserAutomationSettings`), and the
`/settings/automation` + `/automation/check-now` routes in `app/web/main.py`.

For the queue itself see [Redis Queue and Lock](09-redis-queue.md); for the
worker process see [Worker Flow](11-worker-flow.md).

## It is a thread, not a sixth container

There is **no new service**. The five services stay: postgres, redis, migrate,
web, worker. Auto-polling runs as a **daemon thread inside the worker process**.
`app/worker/main.py:run()` starts it — but only in queue mode
(`QUEUE_BACKEND=redis|memory`), and only when `AUTO_POLL_ENABLED` is true — right
after recovering stale jobs and reconciling pending jobs:

```python
if container.settings.auto_poll_enabled:
    poller = threading.Thread(
        target=run_auto_poll_loop, args=(container, stop_event),
        name="auto-poll", daemon=True,
    )
    poller.start()
```

The loop (`run_auto_poll_loop`) runs one `auto_poll_tick` every
`AUTO_POLL_INTERVAL_SECONDS` until `stop_event` is set. A tick error is logged
and swallowed — **the thread never dies**.

## What one tick does

`auto_poll_tick` (`app/worker/auto_poll.py`):

1. **Acquire the single-poller lock** `lock:auto_poll` via
   `queue.acquire_named_lock(..., AUTO_POLL_LOCK_TTL_SECONDS)`. If another poller
   already holds it, the tick is skipped (logged at debug). This keeps exactly
   one poller active across processes/threads.
2. **Re-sweep due retry jobs** — `requeue_pending_jobs(repos, queue, now)`
   re-enqueues `pending` jobs whose backoff window has elapsed, on a bounded
   cadence even when the queue is busy. (See
   [Retries & Dead-Letter](31-retries-dead-letter.md).)
3. **Enumerate due users** — `automation.list_due_for_poll(now, AUTO_POLL_MAX_USERS_PER_TICK)`
   returns up to N users with `auto_poll_enabled=true` whose `last_poll_at` is
   NULL or older than their own `poll_interval_seconds`.
4. **Poll each user** via `poll_user` (see below) and record the outcome on
   `user_automation_settings`.
5. **Release** `lock:auto_poll` (token-checked) in a `finally`.

Per-user failures are isolated: one user's Drive error becomes a friendly
`POLL_ERROR` on their row and does not stop the others.

## Per-user settings: `user_automation_settings`

The `UserAutomationSettings` table (1:1 with `users`, `user_id` unique, FK
CASCADE) holds each user's config, status, and guardrails:

| Column                  | Default        | Meaning                                              |
| ----------------------- | -------------- | ---------------------------------------------------- |
| `auto_poll_enabled`     | `false`        | Opt-in flag; off by default.                          |
| `poll_interval_seconds` | `300`          | Minimum gap between this user's polls.                 |
| `max_files_per_poll`    | `5`            | Cap on jobs created per poll for this user.            |
| `last_poll_at`          | NULL           | When the last poll ran (gates `list_due_for_poll`).   |
| `last_success_at`       | NULL           | When the last poll succeeded.                          |
| `last_error_code`       | NULL           | Code of the last failure (cleared on success).         |
| `last_error_message`    | NULL           | Friendly pt-BR message of the last failure.            |

Guardrail columns (`daily_jobs_limit`, `max_file_size_mb`,
`monthly_cloud_minutes_limit`, `max_file_duration_minutes`) are NULL by default,
meaning "use the global env default". See
[Cost Guardrails](32-cost-guardrails.md).

`list_due_for_poll` orders by `last_poll_at` (NULLs first) and keeps a user only
if `last_poll_at IS NULL` or `last_poll_at <= now - poll_interval_seconds`, so a
shorter interval is honored per user.

## The polling watcher: `poll_user`

`app/services/drive_watcher.py:poll_user` generalizes the single-job run-once
path to create **up to `max_files`** jobs in one pass. Like run-once it only
**lists Drive metadata and creates `pending` jobs** — it never downloads or
transcribes. The caller enqueues the returned `job_ids`.

It returns a `PollResult(created, skipped, error_code, error_message, job_ids)`:

- Hard preconditions yield a friendly `error_code` (never a traceback):
  `NO_SETTINGS`, `NOT_CONNECTED`, `NO_DEEPGRAM_KEY`, `DRIVE_ERROR`.
- A guardrail limit (file too big, daily quota reached) is a **soft notice** in
  `error_message` with `error_code=None`.
- Dedupe is by `find_existing_job(user_id, source_file_id, BLOCKING_STATUSES)`,
  so a file already pending/processing/completed is skipped, not re-created.

## The UI

Two server-rendered Jinja2 pages (no SPA, no CDN):

- **Automação settings** — `GET /settings/automation` renders
  `automation_settings.html` with the user's `UserAutomationSettings`; `POST
  /settings/automation` upserts `auto_poll_enabled`, `poll_interval_seconds`
  (clamped to 60..86400s), and `max_files_per_poll` (clamped to 1..100). The page
  also shows the last poll / last success / last error for the user.
- **"Verificar agora"** — `POST /automation/check-now` runs `poll_user`
  **in-request** for the signed-in user, enqueues the new ids, records the poll
  result, and redirects to `/jobs` with a flash ("N novo(s) job(s)
  enfileirado(s)", "Nenhum vídeo novo...", or the friendly error). This is the
  same lightweight class of work as run-once — a Drive *listing* plus pending-job
  creation — and never transcribes in the request.

## Environment variables

Set on the **worker** (`WorkerSettings.from_env`). Defaults shown; all have
`${VAR:-default}` fallbacks in `docker-compose.yml`.

| Variable                        | Default | Meaning                                                        |
| ------------------------------- | ------- | -------------------------------------------------------------- |
| `AUTO_POLL_ENABLED`             | `false` | Master switch; the loop thread only starts when true.          |
| `AUTO_POLL_INTERVAL_SECONDS`    | `300`   | Seconds between ticks of the global poll loop.                 |
| `AUTO_POLL_MAX_USERS_PER_TICK`  | `50`    | Cap on users processed in one tick.                            |
| `AUTO_POLL_MAX_FILES_PER_USER`  | `5`     | Fallback per-user file cap when the user has not set their own. |
| `AUTO_POLL_LOCK_TTL_SECONDS`    | `240`   | TTL of `lock:auto_poll`; a crashed poller's lock auto-expires. |

The per-user `poll_interval_seconds` / `max_files_per_poll` columns take
precedence over the env defaults; the env values are the global cadence and the
per-user fallback.

## The single-poller lock `lock:auto_poll`

Even with multiple worker threads (and, in principle, multiple worker
processes), only **one** poller runs at a time. The lock is a token-checked
named lock acquired with `SET NX EX` for `AUTO_POLL_LOCK_TTL_SECONDS` and
released only if the token still owns the key — so a poller never frees a lock it
has lost. If a poller dies mid-tick, the TTL lets Redis expire the key and the
next tick recovers.

## Next step: Drive Changes API

The current MVP **lists the whole folder each poll** and relies on `last_poll_at`
gating plus the `(user_id, source_file_id)` dedupe to keep the work bounded. The
incremental **Drive Changes API** (pageToken sync) is a documented next step; a
`drive_watch_state` table to persist that pageToken is **intentionally deferred**
(see [Roadmap](19-roadmap.md)). Until then, polling is correct but not
incremental.
