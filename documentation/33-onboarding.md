# Onboarding Wizard

The onboarding wizard is a single server-rendered page that turns "is this
deployment actually ready to transcribe?" into a concrete, clickable checklist.
It does not store any state of its own — every item is computed live from the
authenticated user's real configuration (Google tokens, Drive settings, the
resolved transcription provider, the queue, and the worker repository backend).
An operator can hand this page to a new user and have them self-serve through
connecting Google, choosing a folder, picking a provider, and running a first
job, without touching the database or env directly.

It is reached from the top nav link in `app/web/templates/base.html:15`
(`<a href="/onboarding">Onboarding</a>`) and rendered by
`app/web/templates/onboarding.html`.

## The route

| Method | Path | Auth | Handler |
|--------|------|------|---------|
| GET | `/onboarding` | login required (`Depends(require_user)`) | `onboarding()` — `app/web/main.py:286` |

The route is **read-only**: it reads state and renders. It never downloads,
transcribes, or enqueues anything — consistent with the hard rule that the web
layer never transcribes in-request (see [12-web-ui.md](12-web-ui.md)). The
actual "run a test" action happens later on `/jobs`.

`require_user` redirects unauthenticated visitors to the login flow, so step 1
("Login / admin") is always satisfied by the time the page renders.

## What the route reads

All inputs come from repository interfaces and app state, never from SQLite —
PostgreSQL is the single source of truth (see
[12-web-ui.md](12-web-ui.md)). The handler at `app/web/main.py:287` gathers:

- `repos.google_tokens.get_for_user(user.id)` — presence of an encrypted Google
  OAuth token for this user. Drives `google_connected`. See
  [04-google-oauth.md](04-google-oauth.md).
- `repos.drive_settings.get_for_user(user.id)` — the per-user Drive config;
  `drive.source_drive_folder_id` must be set. Drives `folder_valid`.
- `app.state.transcription_status` — a `ProviderStatus`
  (`app/transcription/provider.py:34`) resolved once at startup from the
  transcription config. Fields used: `local_valid`, `deepgram_required`,
  `message`, `doc_url`. See [05-deepgram.md](05-deepgram.md) and
  [06-local-transcription.md](06-local-transcription.md).
- `deepgram_store.has_key(user.id)` — whether this user has an **encrypted**
  per-user Deepgram key on file. Keys are never read back into the UI, only
  their presence is checked. Drives `deepgram_configured`.
- `_queue_status()` (`app/web/main.py:171`) — queue health probe. Drives
  `queue_online`. See [09-redis-queue.md](09-redis-queue.md).
- `_resolve_worker_repositories()` (`app/web/main.py:153`) — whether the worker
  repository bundle could be built from `WORKER_REPOSITORY_BACKEND`. Drives
  `worker_online`.

> Secrets are never logged, shown, or embedded in errors. The wizard only ever
> asks "is a Google token present?" and "is a Deepgram key present?" — never the
> values themselves.

## provider_ready

The single most important derived value is `provider_ready`
(`app/web/main.py:295`):

```python
provider_ready = (not status.deepgram_required) or deepgram_configured
```

In words: a provider is ready when **a local engine is valid**
(`ProviderStatus.deepgram_required` is `False`, which happens only when
`local_valid` is `True`) **OR** a per-user Deepgram key is set. There is no
silent fallback: if local transcription is enabled but misconfigured,
`get_transcription_provider_status` (`app/transcription/provider.py:55`) returns
`deepgram_required=True` with a friendly `message` and a `doc_url`, so the user
is pushed to fix Deepgram or the local model rather than failing silently at job
time.

## The 6-item checklist

Rendered by `app/web/templates/onboarding.html:18-26`. Each item is
`{"label": ..., "done": bool}` and shows a `✓` (done) or `•` (pending). The
overall "Tudo pronto" / "Em configuração" badge is driven by
`all_ready = automation_active`.

| # | Label (verbatim PT) | `done` is true when… | Source |
|---|---------------------|----------------------|--------|
| 1 | `Google conectado` | `repos.google_tokens.get_for_user(user.id) is not None` | google_tokens repo |
| 2 | `Pasta do Drive válida` | `drive and drive.source_drive_folder_id` are truthy | drive_settings repo |
| 3 | `Provider válido` | `provider_ready` (local valid **or** Deepgram key set) | `transcription_status` + deepgram_store |
| 4 | `Fila online` | `qs["mode"] == "poll"` **or** `bool(qs["available"])` | `_queue_status()` |
| 5 | `Worker online` | `_resolve_worker_repositories()` returns a bundle (not `None`) | `WORKER_REPOSITORY_BACKEND` |
| 6 | `Automação ativa` | all of items 1–5 are true | `all([...])` |

### How "Fila online" is computed

`_queue_status()` (`app/web/main.py:171`) returns one of:

- `{"mode": "poll", "available": None}` when `app.state.queue is None` — i.e.
  `QUEUE_BACKEND=none`, the legacy poll loop. **This counts as online**: the
  worker still picks up pending jobs by polling Postgres directly, so no Redis
  is required.
- `{"mode": "queue", "available": <bool>}` when a queue is configured; the
  probe calls `queue.health()` and swallows any exception into `available=False`
  (a status probe must never 500 the page). Online iff `available` is `True`.

So `queue_online` is `True` in poll mode regardless, and in queue mode only when
the backend (Redis) actually answers a health check. See
[09-redis-queue.md](09-redis-queue.md).

### How "Worker online" is computed

`_resolve_worker_repositories()` (`app/web/main.py:153`) attempts to build (and
cache on `app.state`) the worker's repository bundle from
`WORKER_REPOSITORY_BACKEND`. A `RepositoryBackendError` (unknown backend)
degrades to `(None, error)` so the page still renders; genuine misconfiguration
(e.g. a missing `DATABASE_URL`) surfaces loudly. `worker_online` is simply
`worker_repos is not None`. This proves the web service can reach the same
Postgres-backed contract the worker uses — it does **not** ping a live worker
process.

### What "Automação ativa" really means

```python
automation_active = all(
    [google_connected, folder_valid, provider_ready, queue_online, worker_online]
)
```

Be honest about the scope: **there is no separate per-user auto-poll scheduler.**
"Automação ativa" means every prerequisite is green, the queue is online, and
the worker repository backend is reachable — so that **jobs you enqueue are
processed automatically, one at a time, by the worker**. It does not mean the
system continuously scans your Drive on a timer for you. The "Run once" action
on `/jobs` scans the configured Drive folder for the **next new recording** and
enqueues it; the worker then dequeues, takes the global lock, claims the job in
Postgres, and transcribes it. "Automation active" is the guarantee that this
enqueue → process handoff works end to end.

## The 7 guided steps

Rendered by `app/web/templates/onboarding.html:30-49` from the `steps` list
(`app/web/main.py:319`). Each step has a number, a title, a `done` flag, a
Portuguese description (`desc`), and an optional `cta` tuple `(label, href)`.
A `cta` whose href starts with `http` opens in a new tab (used for the docs
link); otherwise it is an in-app link.

| Step | Title (verbatim PT) | `done` | CTA (label → href) |
|------|---------------------|--------|--------------------|
| 1 | `Login / admin` | always `True` | none (desc: `Autenticado como {email} ({role}).`) |
| 2 | `Conectar Google` | `google_connected` | `Conectar Google` → `/connect-google` (hidden when done) |
| 3 | `Escolher pasta do Drive` | `folder_valid` | `Configurar pasta` → `/settings/drive` (hidden when done) |
| 4 | `Escolher provider / modelo` | `provider_ready` | `Configurar Deepgram` → `/settings/deepgram` (hidden when done); desc is `status.message` |
| 5 | `Testar provider` | `provider_ready` | `test_cta` (see below) |
| 6 | `Ativar automação` | `automation_active` | none |
| 7 | `Rodar teste final` | always `False` | `Ir para Jobs` → `/jobs` |

Notes that matter operationally:

- **Step 4's description is the live `ProviderStatus.message`** — e.g.
  `Modelo local ativo: ...`, `Transcrição local desativada; Deepgram é
  obrigatório.`, or `Modelo local inválido. Consulte a documentação de modelos
  locais.` This tells the user exactly which posture they're in.
- **Step 5's `test_cta`** (`app/web/main.py:315`) branches on
  `status.deepgram_required`:
  - if Deepgram is required → `("Testar Deepgram", "/settings/deepgram")`
  - else if a `doc_url` exists → `("Ver documentação", status.doc_url)` (opens
    in a new tab — it's the local-engine doc link)
  - else → no CTA.
  The `provider_label` shown in step 5 is `Modelo local ativo`,
  `Deepgram configurado`, or `Provider pendente`.
- **Step 7 is intentionally never marked done.** The wizard cannot know you ran
  a successful test, so it always leaves the final end-to-end test as an open
  action linking to `/jobs`. This is also why `all_ready` is tied to
  `automation_active` (steps 1–6), not step 7.

Below the steps, the template renders two persistent buttons
(`onboarding.html:50-53`): `Rodar transcrição` → `/jobs` and
`Voltar ao dashboard` → `/`.

## Operator playbook

A typical clean onboarding, in order:

1. Sign in (step 1 auto-satisfied).
2. **Conectar Google** → `/connect-google` for OAuth ([04-google-oauth.md](04-google-oauth.md)).
3. **Configurar pasta** → `/settings/drive`; paste the Drive folder URL/id.
4. **Choose a provider**: either enable a valid local CPU engine
   ([06-local-transcription.md](06-local-transcription.md)) so
   `local_valid` is true, or set a per-user Deepgram key at
   `/settings/deepgram` ([05-deepgram.md](05-deepgram.md)).
5. **Test the provider** via step 5's CTA.
6. Confirm **Fila online** and **Worker online** are green; if not, check
   `QUEUE_BACKEND` / Redis ([09-redis-queue.md](09-redis-queue.md)) and
   `WORKER_REPOSITORY_BACKEND` / `DATABASE_URL`.
7. Once **Automação ativa** is green, go to `/jobs` and run a real transcription
   to validate the full path.

## Cross-references

- [12-web-ui.md](12-web-ui.md) — the server-rendered UI, nav, and dashboard.
- [04-google-oauth.md](04-google-oauth.md) — connecting Google Drive.
- [05-deepgram.md](05-deepgram.md) — per-user encrypted Deepgram key.
- [06-local-transcription.md](06-local-transcription.md) — local CPU engines.
- [09-redis-queue.md](09-redis-queue.md) — the queue, poll mode, and worker.
