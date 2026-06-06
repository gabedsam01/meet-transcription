# Security (Operator Guide)

This is the **operator-facing** security guide: a checklist with the *why* behind
each control, written for whoever deploys and runs this stack in production. It
complements two companion documents and deliberately does **not** repeat them:

- The top-level [../SECURITY.md](../SECURITY.md) — the vulnerability **reporting
  policy** and the public summary of how secrets are protected.
- [16-security.md](16-security.md) — the deep reference: table-by-table secret
  inventory, the exact Fernet derivation, backups, privacy/consent, and the
  no-secret-logging test.

Read both first. This page assumes them and focuses on the *operational
decisions* you make at deploy time. Where it names an env var, file, table,
route, or function, that identifier is taken verbatim from the codebase.

Cross-references used throughout: [16-security.md](16-security.md),
[34-observability.md](34-observability.md), [35-webhooks.md](35-webhooks.md),
[10-postgres-and-migrations.md](10-postgres-and-migrations.md).

---

## 1. `APP_SECRET_KEY` is your one root secret

The whole credential model hangs off a single value. Understand what it controls
before you generate it:

| Role | Why it matters |
| --- | --- |
| **Session cookie signer** | A weak/leaked key lets an attacker forge a logged-in session. |
| **Fernet master key** for all at-rest secrets | Derives the key that encrypts every Google token and Deepgram key. |

The derivation is in `app/web/security.py::fernet_from_secret` — the value is
SHA-256 hashed to 32 bytes, base64url-encoded, and handed to `Fernet`. The web
layer encrypts with `encrypt_value` before writing to Postgres; the worker
decrypts with `decrypt_value` after reading. The repository layer only ever
stores ciphertext.

**Operational consequences (the *why*):**

1. **Same value on `web` and `worker`.** The web service encrypts; the worker
   decrypts. If they differ, the worker's `_Decryptor` in
   `app/repositories/postgres.py` cannot turn ciphertext back into a usable
   Google token / Deepgram key, and jobs fail. The decryptor is lazy — it only
   derives the key when there is actually something to decrypt, and raises a
   clear `CredentialDecryptionError` (not garbage) if a credential exists but the
   key is unset.
2. **It is a root secret.** `APP_SECRET_KEY` + a copy of the database = every
   stored token and key in plaintext. Store it in a secret manager, never in git,
   never in the image.
3. **Rotation is destructive.** Because the key is *derived* from
   `APP_SECRET_KEY`, changing it makes all existing ciphertext **undecryptable** —
   there is no in-place re-key. Users must reconnect Google and re-enter their
   Deepgram key. Plan rotation as a migration window (or write a one-off script
   that decrypts with the old key and re-encrypts with the new one before the
   swap). See [16-security.md](16-security.md) §2.3.
4. **Never run on the placeholder.** `docker-compose.yml` ships
   `${APP_SECRET_KEY:-change-me-in-production}` only so `docker compose config`
   works without a `.env`. Generate a real value:

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

---

## 2. Per-user Deepgram keys — no global fallback

In the web/worker architecture there is **no global `DEEPGRAM_API_KEY`**. Each
user's key is stored encrypted in `deepgram_credentials.encrypted_api_key` and
decrypted only inside the worker (`PgSettingsRepository.get` in
`app/repositories/postgres.py` calls `self._dec.decrypt(cred.encrypted_api_key)`).

Why this matters operationally:

- There is **no silent fallback** to an environment key. If a user has neither a
  Deepgram key nor a valid local engine, the worker fails the job with the
  friendly `DeepgramKeyRequiredError` / `LocalTranscriptionUnavailableError`
  (`app/errors.py`) — no accidental billing to a shared key, no surprise.
- The env var `DEEPGRAM_API_KEY` belongs **only** to the legacy CLI
  (`python -m app.main`). Do not set it expecting the web/worker to use it.
- To keep audio fully on-premises and avoid Deepgram entirely, enable the local
  CPU engine (`LOCAL_TRANSCRIPTION_ENABLED=true`). See [16-security.md](16-security.md)
  §9 and `06-local-transcription.md`.

---

## 3. Google OAuth — encrypted tokens, Drive scope

- Tokens are **per user**, stored encrypted in `google_tokens`
  (`encrypted_access_token`, `encrypted_refresh_token`, `client_secret`), and
  decrypted only by the worker via `PgGoogleTokenRepository.get`.
- The OAuth flow requests a single scope, the broad Drive scope
  (`https://www.googleapis.com/auth/drive`): read the source-folder MP4s, and
  optionally write a `.txt` copy back when `save_copy_to_drive` is on. If your
  deployment never writes back, document that the write capability is unused
  (principle of least privilege — see [16-security.md](16-security.md) §4).
- Job and transcript reads in the UI are **user-scoped** through the repository
  contract (`JobRepository.get_job` / `list_jobs_for_user`); a user never sees
  another user's jobs. Admin routes (`/admin/users`) are gated by `require_admin`.
- Users can revoke at https://myaccount.google.com/permissions; after revocation
  the worker surfaces a friendly message rather than crashing.

---

## 4. Session cookie and the HTTPS edge

`APP_SECRET_KEY` signs the session cookie, but signing is not confidentiality on
the wire. In production:

- Set `SESSION_COOKIE_SECURE=true` so the cookie is only sent over TLS. Without
  it, a cookie can leak over plain HTTP if the proxy ever serves the app without
  TLS.
- Terminate TLS at your reverse proxy / load balancer in front of `web:8000`.

---

## 5. Keep Redis and Postgres off the public internet

Redis is **only** the transcription queue + global execution lock; Postgres is
the **single source of truth** (there is no SQLite anywhere). Neither should ever
be reachable from outside.

In the default Compose topology only `web` publishes a host port:

```yaml
# docker-compose.yml — web
ports:
  - "8000:8000"
```

`postgres` and `redis` have **no** `ports:` mapping. They are reachable by
service hostname on the private Compose network (`postgres:5432`, `redis:6379`)
but never published. Why this is load-bearing:

- **Do not** add `ports: ["5432:5432"]` or `ports: ["6379:6379"]` on an
  internet-facing host — that exposes the database and an unauthenticated Redis
  to the world. Redis 7 here runs without a password *because* it is private; if
  you ever move it off the private network, add auth + TLS.
- Firewall `5432` and `6379` off from the public internet. For ad-hoc DB access
  use an SSH tunnel or `docker compose exec`, not a published port.
- If Redis is lost, **nothing authoritative is lost**: pending jobs are
  reconciled from Postgres on worker startup (`requeue_pending_jobs`). That is
  why Redis needs no backup and can stay disposable. See
  [10-postgres-and-migrations.md](10-postgres-and-migrations.md) for the DB and
  one-shot `migrate` (`alembic upgrade head`) service.

---

## 6. Backups carry encrypted secrets — protect both halves

Because Postgres is the source of truth, your backup **is** the system: it
contains the encrypted Google tokens and Deepgram keys **and** the plaintext
meeting transcripts (`transcripts.transcript_text` / `transcript_json`). Two
operational rules follow:

- **Encrypt backups at rest and restrict who can read them.** A backup is as
  sensitive as live data.
- **Store `APP_SECRET_KEY` separately from the backup.** A dump is only useful
  *together with* the matching key to decrypt credentials. Keep the key in a
  secret manager, not inside the archive. Lose the key and the restored
  credentials are unrecoverable (users reconnect). Full `pg_dump` / volume
  procedures live in [10-postgres-and-migrations.md](10-postgres-and-migrations.md)
  and [16-security.md](16-security.md) §8 — test your restores.

---

## 7. Secrets never reach logs, the UI, errors, or webhooks

This is enforced in code, not just by convention. Four layers protect it:

- **Errors → friendly, secret-free messages.** Every domain failure is an
  `AppError` (`app/errors.py`) carrying a Portuguese `user_message` (e.g.
  `"Configure sua Deepgram API Key antes de iniciar uma transcrição."`). The
  worker stores that `user_message` as the job's `error_message`; full
  tracebacks stay in process logs only, never in the UI or database.
- **Structured logging redacts.** `app/observability/redact` masks any field
  whose name contains `token`, `secret`, `password`, `key`, `authorization`,
  `auth`, `credential`, `cookie`, `session`, or `fernet` with `***`, in both
  `text` and `json` formats — so even a careless `log_event(..., api_key=key)`
  cannot leak. Decrypted credentials are used in-memory and never logged. See
  [34-observability.md](34-observability.md).
- **Webhook payloads are secret-free.** `app/webhooks/notifier.py::job_event_data`
  emits only ids, status, source filename, and the friendly `error_message`; the
  payload is additionally passed through `redact` before delivery. See
  [35-webhooks.md](35-webhooks.md).
- **No transcription in an HTTP request.** The web layer only validates, creates
  a `pending` job, and enqueues its id; download/transcribe/upload happen in the
  worker. This keeps request handlers off the credential-using path.

Operator note: the guarantee covers application code — do not defeat it from the
outside. Avoid pasting a Deepgram key on a CLI command line (it lands in shell
history and `ps`), and keep `DATABASE_URL` / `POSTGRES_PASSWORD` out of build
logs and CI echoes.

---

## 8. Reporting a vulnerability

**Do not open a public GitHub issue for a security problem.** Report it
privately — via GitHub Security Advisories ("Report a vulnerability") or the
maintainer email — and do not include real secrets in the report. The full
policy, supported versions, and disclosure window are in
[../SECURITY.md](../SECURITY.md).

---

## 9. Companion Chrome extension (optional)

If you deploy the optional browser extension that uploads audio directly:

- Request the **minimum** permissions necessary and upload over HTTPS to the
  authenticated web service.
- The extension must **never** embed `APP_SECRET_KEY` or any server credential —
  it is client-side code and anything baked in is effectively public.
- Uploaded items are tracked with a `chrome-extension:<uuid>` source sentinel.
  Treat any extension API token as a user secret: encrypted at rest, never
  logged.

---

## 10. Hardening checklist

- [ ] `APP_SECRET_KEY` is a strong, unique value (not `change-me-in-production`)
      and **identical** on `web` and `worker`; stored in a secret manager.
- [ ] `SESSION_COOKIE_SECURE=true` and the app served only over HTTPS.
- [ ] Strong `ADMIN_PASSWORD` and `POSTGRES_PASSWORD`.
- [ ] Only `web:8000` is published; `postgres:5432` and `redis:6379` have no host
      port and are firewalled off the public internet.
- [ ] Per-user Deepgram keys in use; no global `DEEPGRAM_API_KEY` in web/worker.
- [ ] Google OAuth limited to the Drive scope actually used; admin accounts kept
      minimal.
- [ ] Regular, **encrypted** `postgres_data` backups with `APP_SECRET_KEY` stored
      separately, and restores tested.
- [ ] `.env`, `secrets/*`, `token.json` confirmed git-ignored; any accidentally
      committed secret is **rotated**, not just deleted.
- [ ] `LOG_FORMAT` set as desired; verify logs show `***` for sensitive fields.
- [ ] Privacy policy covers recording consent, third-party (Deepgram) audio
      processing, and transcript retention/deletion (see [16-security.md](16-security.md) §9).
- [ ] `python -m pytest -v` green (includes `test_worker_adapter_does_not_log_secrets`).
