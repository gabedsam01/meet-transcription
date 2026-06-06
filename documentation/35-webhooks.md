# Outbound Webhooks

Webhooks are an **optional** integration: when configured, the **worker** sends an
HTTP `POST` to a URL you control every time a job reaches a terminal state
(`completed` or `failed`). This lets you wire transcription into the rest of your
stack — notify a Slack channel, kick off a downstream pipeline, update a ticket —
without polling the database or scraping the web UI.

Webhooks are **off by default**. Nothing is sent unless you set `WEBHOOK_URL`.

Why the worker and not the web app: transcription happens out of band in the
worker (`python -m app.worker.main`), and a job only becomes `completed` or
`failed` there. The web layer never transcribes in-request, so it has nothing to
report. The webhook fires from `JobProcessor.process` in
`app/worker/processor.py` **after** the job has already been written to its
terminal state in Postgres — see [11-worker-flow.md](11-worker-flow.md).

## Configuration

All configuration is via environment variables, parsed once by
`WebhookSettings.from_env` (`app/webhooks/config.py:24`). The worker builds the
notifier at startup in `app/worker/container.py:42` and treats a blank
`WEBHOOK_URL` as **disabled** (`webhook_notifier` stays `None`).

| Name | Default | Description |
| --- | --- | --- |
| `WEBHOOK_URL` | _(empty)_ | Destination URL. **Blank disables webhooks entirely.** |
| `WEBHOOK_EVENTS` | `job.completed,job.failed` | Comma-separated allow-list of events to deliver. |
| `WEBHOOK_TIMEOUT_SECONDS` | `10` | Per-request HTTP timeout. Must be `> 0` or the worker fails to start. |
| `WEBHOOK_MAX_RETRIES` | `2` | Extra delivery attempts after the first (so 3 attempts total). `0` = deliver once, no retry; negatives clamp to `0`. |

Notes:

- `WEBHOOK_EVENTS` is an allow-list, not a subscription request. Listing an event
  the worker never emits simply has no effect; omitting one suppresses it. The
  default lives in `DEFAULT_EVENTS` (`app/webhooks/config.py:8`).
- `WEBHOOK_TIMEOUT_SECONDS` is validated as a strictly positive integer; a `0` or
  negative value raises `ValueError` at startup so misconfiguration is loud, not
  silent.
- See [03-environment-variables.md](03-environment-variables.md) for how these sit
  alongside the rest of the worker's configuration.

```bash
# .env — enable webhooks pointing at your receiver
WEBHOOK_URL=https://hooks.example.com/meet-transcription
WEBHOOK_EVENTS=job.completed,job.failed
WEBHOOK_TIMEOUT_SECONDS=10
WEBHOOK_MAX_RETRIES=2
```

## Events

Two events, both fired from `app/worker/processor.py`:

| Event | Constant | Fires when |
| --- | --- | --- |
| `job.completed` | `JOB_COMPLETED` | A job finishes transcription successfully — fired after `mark_completed` in `JobProcessor.process`. |
| `job.failed` | `JOB_FAILED` | A job ends in failure — fired after `mark_failed` in `JobProcessor.process`. |

The constants come from `app/webhooks/notifier.py:13-14`. The event names are kept
small and stable on purpose — they are part of the contract with your receiver.

## Payload envelope

Every delivery is a `POST` with a JSON body in this exact shape, built in
`WebhookNotifier.notify` (`app/webhooks/notifier.py:73`):

```json
{
  "event": "job.completed",
  "occurred_at": "2026-06-05T14:03:21.512000+00:00",
  "data": {
    "job_id": "...",
    "user_id": "...",
    "status": "...",
    "source_file_id": "...",
    "source_file_name": "...",
    "error_code": null,
    "error_message": null
  }
}
```

- `event` — one of the event names above.
- `occurred_at` — ISO-8601 UTC timestamp of when the webhook was built.
- `data` — a stable, non-sensitive job summary assembled in
  `JobProcessor._emit_webhook` (the same shape as `job_event_data` in
  `app/webhooks/notifier.py`, plus `error_code`). Both events carry the same keys;
  `error_code`/`error_message` are `null` on success.
- `error_code` — a stable machine-readable code on failure (e.g.
  `deepgram_key_required`, `google_not_connected`, or `internal_error` for an
  unexpected error); `null` on success.

### Completed example

```json
{
  "event": "job.completed",
  "occurred_at": "2026-06-05T14:03:21.512000+00:00",
  "data": {
    "job_id": "9c1f7a2e-0b3d-4c8e-9a11-2f6e5d4c3b2a",
    "user_id": "a7d2b1c0-1234-4abc-8def-0123456789ab",
    "status": "completed",
    "source_file_id": "1AbCdEfGhIjKlMnOpQrStUvWxYz",
    "source_file_name": "Reuniao 2026-06-05.mp4",
    "error_code": null,
    "error_message": null
  }
}
```

### Failed example

`error_message` is **always secret-free**: for a mapped failure it is the curated
`AppError.user_message` (the same friendly text shown in the UI); for any other
(unexpected) error it is a generic message — **never** `str(exc)`, a stack trace,
a token, or an API key. The machine-readable `error_code` tells the receiver what
happened. Tracebacks stay in the worker logs only.

```json
{
  "event": "job.failed",
  "occurred_at": "2026-06-05T14:05:02.880000+00:00",
  "data": {
    "job_id": "3e4d5c6b-7a89-40ef-9b12-cdef01234567",
    "user_id": "a7d2b1c0-1234-4abc-8def-0123456789ab",
    "status": "failed",
    "source_file_id": "1AbCdEfGhIjKlMnOpQrStUvWxYz",
    "source_file_name": "Reuniao 2026-06-05.mp4",
    "error_code": "google_not_connected",
    "error_message": "Conecte sua conta Google antes de transcrever."
  }
}
```

## Delivery semantics

Delivery is **best-effort** and deliberately decoupled from the job. The guarantees
are:

- **Fired after the terminal state.** The job is written to Postgres (the single
  source of truth) as `completed` / `failed` **before** the webhook is attempted.
  A lost or rejected webhook never changes a job's recorded outcome.
- **Never blocks or fails a job.** `_emit_webhook` (`app/worker/processor.py:126`)
  wraps the call in a `try/except` that swallows everything, and
  `WebhookNotifier` is documented to never raise to the worker
  (`app/webhooks/notifier.py:46`). A misbehaving receiver cannot stall the queue
  or mark a transcription as failed.
- **No-op when disabled or filtered.** `notify` returns `False` immediately if
  webhooks are disabled or the event is not in `WEBHOOK_EVENTS`
  (`app/webhooks/notifier.py:79`), so callers never need to guard.
- **Retries on transient failures.** Network/transport errors and the retryable
  HTTP statuses in `RETRYABLE_STATUS` (`app/webhooks/notifier.py:17`) — `429`,
  `500`, `502`, `503`, `504` — trigger a retry up to `WEBHOOK_MAX_RETRIES` extra
  attempts. Any other non-2xx (e.g. `400`, `401`, `404`) is treated as permanent
  and is **not** retried.
- **Backoff between attempts.** A gentle exponential backoff,
  `min(0.5 * 2**(attempt-1), 5.0)` seconds, capped at 5s
  (`WebhookNotifier._backoff`, `app/webhooks/notifier.py:134`).
- **Gives up after max retries.** Once attempts are exhausted the failure is
  logged and delivery stops; the job is unaffected.
- **Delivered means 2xx.** Only an HTTP `2xx` response counts as delivered.

Every outcome (`webhook.delivered`, `webhook.retry`, `webhook.failed`) is recorded
as a structured event via `log_event`, with secret-free fields only. See
[34-observability.md](34-observability.md) for how to read those events.

## Security

Webhooks are designed to leak nothing sensitive — see
[37-security.md](37-security.md):

- **Secret-free payload by construction.** `job_event_data` only ever copies ids,
  status, the source filename, and the friendly `error_message`. There is no field
  for a Google token, Deepgram key, or raw provider response.
- **Defence in depth via redaction.** Before sending, `notify` passes `data`
  through `redact` (`app/observability/__init__.py:54`), which masks any
  sensitive-looking key. So even if a future field were named like a secret, its
  value would be replaced with `***` rather than sent.
- **No secrets in logs.** Delivery logging uses the same structured,
  redaction-aware path; tokens and keys never appear in worker logs or in the
  payload.
- **You should still secure the channel.** Send to an HTTPS endpoint. If you need
  authentication, terminate it at your receiver (e.g. a hard-to-guess path or a
  reverse proxy that checks a header) — the worker itself does not currently sign
  requests.

## Example receiver

A minimal receiver that records terminal job events. Validate `event` and read
`data` defensively; do not assume fields are non-null (`error_message` is `null`
on success).

```python
from fastapi import FastAPI, Request

app = FastAPI()


@app.post("/meet-transcription")
async def receive(request: Request):
    body = await request.json()
    event = body.get("event")
    data = body.get("data", {})
    if event == "job.completed":
        print(f"OK  {data['job_id']}: {data['source_file_name']}")
    elif event == "job.failed":
        print(f"ERR {data['job_id']}: {data.get('error_message')}")
    # Return 2xx promptly; the worker counts only 2xx as delivered.
    return {"received": True}
```

Return a `2xx` quickly. Returning `429` or any `5xx` asks the worker to retry
(within `WEBHOOK_MAX_RETRIES`); returning `4xx` (other than `429`) tells it to give
up immediately.

## See also

- [11-worker-flow.md](11-worker-flow.md) — where `JOB_COMPLETED` / `JOB_FAILED`
  fire in the job lifecycle.
- [34-observability.md](34-observability.md) — the `webhook.*` structured log
  events.
- [37-security.md](37-security.md) — secret handling and redaction.
- [03-environment-variables.md](03-environment-variables.md) — the full env var
  reference.
