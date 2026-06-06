# Cost and Quota Guardrails

Guardrails cap how much work a user can enqueue, to keep cloud transcription cost
and per-file size under control. They are checked **at job creation** — in the
Drive watcher, "Verificar agora" (`/automation/check-now`), and the auto-poll loop
— **before** a `pending` job ever reaches the worker. A blocked file is simply
skipped; guardrails never raise, never 500, and never leak a traceback.

This page is grounded in `app/services/guardrails.py` (the `Guardrails` dataclass
+ `resolve_guardrails`), `app/services/drive_watcher.py` (where they are applied
per file), `app/worker/config.py` (`MAX_FILE_SIZE_MB` / `DAILY_JOBS_LIMIT`
defaults), and `app/database/models.py` (`UserAutomationSettings` guardrail
columns).

See [Auto-polling](28-auto-polling.md) for the watcher/auto-poll loop and
[Worker Flow](11-worker-flow.md) for the per-job pipeline.

## How limits resolve

Each limit resolves **per user first, then the global env default**, with
`0`/`None` meaning **unlimited**. `resolve_guardrails` reads the user's
`user_automation_settings` row (a per-user override column) and falls back to the
worker env default when that column is `NULL`:

```python
def resolve_guardrails(automation, *, default_max_file_size_mb, default_daily_jobs_limit):
    user_size = getattr(automation, "max_file_size_mb", None) if automation else None
    user_daily = getattr(automation, "daily_jobs_limit", None) if automation else None
    return Guardrails(
        max_file_size_mb=user_size if user_size is not None else default_max_file_size_mb,
        daily_jobs_limit=user_daily if user_daily is not None else default_daily_jobs_limit,
    )
```

The auto-poll loop passes `settings.max_file_size_mb or None` /
`settings.daily_jobs_limit or None` as the defaults (so a global `0` collapses to
"unlimited"). `/automation/check-now` passes `None` defaults — there is no global
ceiling on an explicit, in-request "check now".

## Enforced now

Both checks run inside `poll_user` (`app/services/drive_watcher.py`), per file,
before `create_job`.

### `max_file_size_mb` — per-file size

Drive already returns the file `size` in the listing, so this is free. Files over
the limit are skipped with a friendly notice:

```python
def allow_file(self, file) -> tuple[bool, str | None]:
    size = getattr(file, "size", None)
    if self.max_file_size_mb and size is not None:
        if int(size) > self.max_file_size_mb * 1024 * 1024:
            return False, "Arquivo excede o limite permitido."
    return True, None
```

### `daily_jobs_limit` — jobs created since midnight UTC

The remaining daily budget is computed once per poll via
`count_jobs_created_since(user_id, since)`, counting from **midnight UTC** of the
current day (`_start_of_day` zeroes `hour/minute/second/microsecond`):

```python
def daily_room(self, repositories, user_id, now) -> int | None:
    if not self.daily_jobs_limit:
        return None                       # unlimited
    used = repositories.jobs.count_jobs_created_since(user_id, _start_of_day(now))
    return max(0, self.daily_jobs_limit - used)
```

In the loop, once `len(created_ids) >= room`, the watcher stops creating jobs and
records the daily-limit notice. `count_jobs_created_since` is part of the
`JobRepository` contract (`app/core/ports.py`) and implemented in both the
postgres and memory adapters; the postgres query filters
`transcription_jobs.created_at >= since` (backed by the
`(user_id, created_at)` index).

## Scaffolded next steps

`UserAutomationSettings` and the env layer already carry two more guardrails, but
they are **not metered yet**:

| Column                          | Status     | Needs                                                  |
| ------------------------------- | ---------- | ----------------------------------------------------- |
| `monthly_cloud_minutes_limit`   | scaffolded | per-job minute metering / cloud-minute accounting     |
| `max_file_duration_minutes`     | scaffolded | Drive `videoMediaMetadata.durationMillis` in listings |

The columns (`app/database/models.py`) and the per-user override semantics exist;
they are documented as future work and enforced only where the data is present.
See [Roadmap](19-roadmap.md).

## Friendly messages (pt-BR)

Guardrails surface as **soft notices**: the file is skipped (`skipped += 1`),
`error_code` stays `None`, and the message rides back in `PollResult.error_message`
for the UI flash / `last_error_message`.

| Message                                              | Source                            |
| ---------------------------------------------------- | --------------------------------- |
| `Arquivo excede o limite permitido.`                 | `Guardrails.allow_file` (size)    |
| `Limite diário de jobs atingido.`                    | `poll_user` (daily room reached)  |
| `Provider está rate-limited. Tentaremos novamente.`  | `DeepgramRateLimitError` (429)    |

The first two are creation-time guardrails. The rate-limit message comes from the
worker-side error policy (HTTP 429 → retry with backoff) — see
[Retries & dead-letter](31-retries-dead-letter.md) — and is included here because
it is the runtime quota counterpart of the cost guardrails.

## Guardrails never break a poll

A guardrail is **admission control, not an error**. A blocked file is counted as
skipped and the loop moves on; the poll keeps creating other eligible jobs up to
`max_files`. A real failure (no settings, not connected, Drive error) is the only
thing that sets a non-`None` `error_code`. Guardrails raise nothing, so they can
never wedge the auto-poll thread or 500 the `/automation/check-now` request.

## Environment defaults

Global defaults live on `WorkerSettings` (`app/worker/config.py`). Both use
`_non_negative_int` so **`0` is valid and means unlimited**:

```bash
MAX_FILE_SIZE_MB=0       # 0 = unlimited (per-file size cap, in MB)
DAILY_JOBS_LIMIT=0       # 0 = unlimited (jobs created per user since midnight UTC)
```

A user's `user_automation_settings.max_file_size_mb` /
`daily_jobs_limit` override these defaults; a `NULL` column inherits the env
default. See [Environment Variables](03-environment-variables.md).
