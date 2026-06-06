# Security Policy

Meet Transcription handles meeting recordings, Google Drive access, and
third-party API keys, so security is a first-class concern. This document
explains how to report a vulnerability and how the project protects sensitive
data. A deeper operator-facing reference lives in
[`documentation/37-security.md`](documentation/37-security.md) and
[`documentation/16-security.md`](documentation/16-security.md).

## Reporting a vulnerability

**Please do not open a public GitHub issue for security problems.**

Report privately instead:

1. Use **GitHub Security Advisories** → *"Report a vulnerability"* on this
   repository (preferred), or
2. Email the maintainer listed on the GitHub profile with the subject
   `SECURITY: meet-transcription`.

Include: affected version/commit, a description, reproduction steps, and the
impact you observed. Please give us a reasonable window to investigate and ship a
fix before any public disclosure. We aim to acknowledge a report within a few
business days.

Do **not** include real secrets (tokens, API keys, passwords) in your report —
redact them or share a minimal synthetic reproduction.

## Supported versions

This is an actively developed project; security fixes target the `main` branch
and the latest published GHCR image. Pin to a published `:<short-sha>` tag for
reproducible deployments and update when a fix lands.

## How secrets are protected

### Google OAuth tokens
- Stored **encrypted at rest** in PostgreSQL (Fernet, key derived from
  `APP_SECRET_KEY`). The repository layer only ever sees ciphertext; the worker
  decrypts in-process when it needs Drive access.
- The OAuth scope is **Drive only**. Tokens are per-user and never shared.

### Deepgram API keys
- **Per-user** and **encrypted at rest** (same Fernet derivation). There is **no
  global `DEEPGRAM_API_KEY`** in the web/worker architecture — the env var of that
  name belongs only to the optional legacy CLI worker.
- Keys are never rendered in the UI (only a masked preview) and never logged.

### `APP_SECRET_KEY`
- Doubles as the **session cookie signer** and the **Fernet master key** for all
  encrypted credentials. Treat it like a root secret.
- Generate a strong value (`python -c "import secrets;print(secrets.token_urlsafe(48))"`),
  set the **same** value on the web and worker services, and store it in your
  secret manager — never in git.
- Rotating it makes all previously-encrypted tokens/keys undecryptable (users
  reconnect Google and re-enter their Deepgram key). There is no in-place re-key.

### Redis
- Used only as the transcription **queue + global lock**, never as a datastore.
- Must **not** be exposed publicly. The Compose file publishes only the web
  service (port 8000); Postgres (5432) and Redis (6379) have no `ports:` mapping
  and must stay firewalled on a private network.
- If Redis is lost, no source-of-truth data is lost: pending jobs are reconciled
  from PostgreSQL on worker startup.

### PostgreSQL (source of truth) and backups
- All durable state lives in Postgres. Back up the `postgres_data` volume
  regularly; that backup contains the **encrypted** tokens/keys, so it is only as
  safe as your `APP_SECRET_KEY` custody and your backup storage.
- Restrict database network access to the application services. Never expose 5432
  to the public internet.

### Secrets never appear in logs, UI, or errors
- Domain failures are mapped to friendly, **secret-free** messages
  (`app/errors.py`); full tracebacks stay in server logs only and never reach the
  UI.
- Structured logging (`app/observability`) **redacts** any field whose name looks
  like a token/key/password/credential before it is written, in both `text` and
  `json` log formats.
- Webhook payloads (`app/webhooks`) are secret-free and additionally redacted.
- This is enforced by tests (e.g. `tests/test_observability.py`,
  `tests/test_worker_bridge.py`).

### Companion Chrome extension (optional integration)
- If you deploy the optional browser extension that uploads audio directly, it
  should request the **minimum** permissions necessary and upload over HTTPS to
  the authenticated web service. Uploaded items are tracked with a
  `chrome-extension:<uuid>` source sentinel; the extension must never embed the
  `APP_SECRET_KEY` or any server credential. Treat any extension API token as a
  user secret (encrypted at rest, never logged).

## Hardening checklist for operators

- [ ] Set a unique, strong `APP_SECRET_KEY` (same on web + worker).
- [ ] `SESSION_COOKIE_SECURE=true` when serving over HTTPS.
- [ ] Only the web service is publicly reachable; Postgres/Redis are private.
- [ ] Regular, access-controlled backups of `postgres_data`.
- [ ] `.env`, `secrets/*.json`, `token.json` are git-ignored (they are) and never
      committed. If a secret is committed by accident, **rotate it** — git history
      keeps it.
- [ ] Review `documentation/37-security.md` before going to production.
