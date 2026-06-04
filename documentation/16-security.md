# Security

This document describes how Meet Transcription protects credentials and user
data: how secrets are encrypted at rest, how the network surface is kept small,
which files must never reach the repository, how to back up the database, and
the privacy/consent considerations that come with handling meeting recordings.

It is operational. Where it names an environment variable, file, table, or
function, that identifier is taken verbatim from the codebase. See also
[Architecture](01-architecture.md), [Configuration](03-environment-variables.md), and
[Deployment](13-dokploy-deploy.md) for the surrounding context.

---

## 1. The secrets this system holds

Meet Transcription is a multi-user web app. Per user, it stores two classes of
high-value secret in PostgreSQL:

| Secret | Where it lives | Table / column |
| --- | --- | --- |
| Google OAuth access token | per user | `google_tokens.encrypted_access_token` |
| Google OAuth refresh token | per user | `google_tokens.encrypted_refresh_token` |
| Google OAuth client secret | per user | `google_tokens.client_secret` |
| Deepgram API key | per user | `deepgram_credentials.encrypted_api_key` |

In addition, the process environment carries deployment-level secrets that are
**not** stored in the database:

- `APP_SECRET_KEY` — the master key (see below); also signs the session cookie.
- `ADMIN_USERNAME` / `ADMIN_PASSWORD` — the bootstrap admin login.
- `POSTGRES_PASSWORD` / `DATABASE_URL` — database credentials.
- `GOOGLE_WEB_CLIENT_ID` / `GOOGLE_WEB_CLIENT_SECRET` — the OAuth app secret.

> The web/worker deployment does **not** use a global `DEEPGRAM_API_KEY`.
> Deepgram keys are strictly per-user and encrypted. The global
> `DEEPGRAM_API_KEY` env var belongs only to the legacy CLI
> (`python -m app.main`); see [Legacy CLI](01-architecture.md).

---

## 2. `APP_SECRET_KEY` and Fernet encryption at rest

### 2.1 The key derivation

All at-rest encryption uses **Fernet** (AES-128-CBC + HMAC, from the
`cryptography` library). The Fernet key is **derived from `APP_SECRET_KEY`** —
the application never asks you to manage a separate encryption key. The exact
derivation lives in `app/web/security.py`:

```python
# app/web/security.py
def fernet_from_secret(secret: str) -> Fernet:
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)
```

`APP_SECRET_KEY` is SHA-256 hashed to 32 bytes, base64url-encoded, and handed to
`Fernet`. The web layer encrypts with `encrypt_value(fernet, value)` before
writing to Postgres; the worker layer decrypts with
`decrypt_value(fernet, value)` after reading. Both are pure helpers in the same
file:

```python
def encrypt_value(fernet, value): ...   # plaintext -> ciphertext (or None)
def decrypt_value(fernet, value): ...   # ciphertext -> plaintext (or None)
```

`None` passes through unchanged (an absent token stays absent), so "no
credential" is never confused with "empty ciphertext".

### 2.2 Who encrypts, who decrypts

- **Web (`app/web/`)** encrypts. When a user connects Google
  (`/oauth/google/callback`) or saves a Deepgram key (`/settings/deepgram`), the
  plaintext is encrypted with the Fernet derived from `APP_SECRET_KEY` and only
  the ciphertext is persisted.
- **Worker (`app/repositories/postgres.py`)** decrypts, and **only** the worker.
  The repository bundle built by `build_postgres_repositories()` wraps a
  `_Decryptor` that lazily derives the same Fernet from `APP_SECRET_KEY`:

```python
# app/repositories/postgres.py — PgGoogleTokenRepository.get
return GoogleToken(
    access_token=self._dec.decrypt(t.encrypted_access_token),
    ...
    refresh_token=self._dec.decrypt(t.encrypted_refresh_token),
    client_secret=self._dec.decrypt(t.client_secret),
    ...
)
```

```python
# PgSettingsRepository.get
deepgram_api_key=self._dec.decrypt(cred.encrypted_api_key) if cred else None,
```

The decryptor only derives the key **when there is something to decrypt**. If no
Deepgram credential row exists, `APP_SECRET_KEY` is not even needed. If a
credential is present but `APP_SECRET_KEY` is unset, the worker raises a clear
`CredentialDecryptionError` rather than silently producing garbage:

```python
# app/repositories/postgres.py
class CredentialDecryptionError(RuntimeError):
    """An encrypted credential is present but APP_SECRET_KEY is missing to decrypt it."""
```

### 2.3 Operational rules for `APP_SECRET_KEY`

1. **The same `APP_SECRET_KEY` must be set on `web` and `worker`.** If they
   differ, the worker cannot decrypt what the web app encrypted.
2. **It is the master key.** Anyone with `APP_SECRET_KEY` plus a copy of the
   database can decrypt every stored token and Deepgram key. Treat it like a
   root password.
3. **Set a real value in production.** `docker-compose.yml` ships a placeholder
   default `${APP_SECRET_KEY:-change-me-in-production}` so `docker compose
   config` works without a `.env`. **Never run production on that default.**
   Generate a strong value, for example:

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

4. **Rotation invalidates existing ciphertext.** Because the key is derived from
   `APP_SECRET_KEY`, changing it makes every stored Google token and Deepgram
   key undecryptable. There is no in-place re-keying helper. To rotate, plan to
   have users re-connect Google and re-enter their Deepgram key (or write a
   migration that decrypts with the old key and re-encrypts with the new one
   before swapping the value).
5. `APP_SECRET_KEY` also backs the session cookie. Pair it with
   `SESSION_COOKIE_SECURE=true` behind HTTPS in production so the cookie is only
   sent over TLS.

---

## 3. Secrets are never logged (enforced by a test)

Decrypted credentials are returned as in-memory domain objects and used
immediately by the worker — they are never written to logs. This is not just a
convention; it is covered by an automated test in
`tests/test_worker_bridge.py`:

```python
# tests/test_worker_bridge.py
def test_worker_adapter_does_not_log_secrets(pg, caplog):
    ...
    repos = build_postgres_repositories(engine=pg, app_secret_key=_SECRET)
    with caplog.at_level(logging.DEBUG):
        tok = repos.google_tokens.get(uid)

    assert tok.access_token == "SECRET_AT"
    for secret in ("SECRET_AT", "SECRET_RT", "SECRET_CS"):
        assert secret not in caplog.text
```

The test captures logs at `DEBUG` level while the repository decrypts a Google
token, then asserts the decrypted access token, refresh token, and client secret
**do not appear anywhere in the captured log text**. If a future change ever
logs a credential, this test fails.

The same discipline applies to error reporting. When a job fails, the worker
stores a friendly, secret-free `user_message` (from `app/errors.py`) in
`transcription_jobs.error_message`; full tracebacks stay in the process logs
only, never in the UI and never in the database. See [Errors](15-troubleshooting.md).

> **Operator note:** the no-secret guarantee covers application code. Do not
> defeat it from the outside — e.g. avoid pasting a Deepgram key on a CLI
> command line (it lands in shell history and `ps`), and keep
> `DATABASE_URL`/`POSTGRES_PASSWORD` out of build logs and CI echoes.

---

## 4. Google Drive scope and permissions

### 4.1 The requested scope

The web OAuth flow requests a single Google scope:

```
https://www.googleapis.com/auth/drive
```

This is the broad Drive scope: it grants read and write access to the user's
Drive files. The app uses it to (a) **read** Google Meet `.mp4` recordings from
the configured source folder and download them for transcription, and (b)
optionally **write** a `.txt` transcript copy to the configured destination
folder.

### 4.2 What the app actually does with it

- The MP4 is **downloaded** by the worker for transcription — input only.
- A transcript copy is **uploaded to Drive only when** the user enabled
  `save_copy_to_drive` **and** a destination folder is set
  (`user_drive_settings`). Otherwise nothing is written back to Drive; the
  transcript is served from Postgres via **Download TXT**.
- Source and destination folders are configured per user under
  `/settings/drive` and stored in `user_drive_settings` (folder URL + resolved
  id + name).

### 4.3 Per-user, revocable, scoped storage

- Tokens are **per user** (`google_tokens`), encrypted at rest (section 2), and
  scoped to that user's jobs. Job reads in the UI are user-scoped through the
  repository contract (`JobRepository.get_job` / `list_jobs_for_user`).
- A user can revoke access at any time from their Google Account
  (https://myaccount.google.com/permissions); after revocation the stored token
  stops working and the worker surfaces a friendly
  `GoogleTokenMissingError`-style message rather than crashing.
- **Principle of least privilege:** the OAuth consent screen and any review
  should request only what is used. If your deployment never writes back to
  Drive (no `save_copy_to_drive`), document that the write capability of the
  `drive` scope is unused.

---

## 5. Where data is stored

PostgreSQL is the **single source of truth**. There is **no SQLite anywhere** in
the architecture. The security-relevant tables (`app/database/models.py`) are:

| Table | Contents | Sensitivity |
| --- | --- | --- |
| `users` | accounts, roles, password hashes | passwords are hashed, never plaintext |
| `google_tokens` | encrypted access/refresh tokens, client secret, `scopes` (JSONB), `expiry` | Fernet-encrypted |
| `deepgram_credentials` | `encrypted_api_key` | Fernet-encrypted |
| `user_drive_settings` | source/destination folder url+id+name, `save_copy_to_drive` | low |
| `transcription_jobs` | status, attempts, `error_message`, drive file ids | `error_message` is secret-free |
| `transcripts` | `transcript_text`, `transcript_json` (JSONB), `drive_file_id` | **meeting content** |

The `transcripts` table holds the actual content of meetings (the human-readable
`.txt` in `transcript_text` and the normalized schema in `transcript_json`).
Treat the database as containing confidential meeting material, not just
credentials — see the privacy note in section 8.

Redis (section 6) holds only transient queue state: the job-id list
(`transcription:queue`), a dedupe set (`transcription:queued`), and the global
execution lock (`transcription:global_lock`). It carries no transcripts and no
credentials. If Redis is lost, Postgres remains authoritative and pending jobs
are re-enqueued on worker startup (`requeue_pending_jobs`).

---

## 6. Keep Redis and Postgres internal — no public exposure

Redis and PostgreSQL must **never** be reachable from the public internet. They
are infrastructure for the `web` and `worker` services and nothing else.

### 6.1 In the default Docker Compose topology

Only the `web` service publishes a host port:

```yaml
# docker-compose.yml — web
ports:
  - "8000:8000"
```

`postgres` and `redis` are **not** given a `ports:` mapping. They are reachable
by other services on the Compose network by hostname (`postgres:5432`,
`redis:6379`, as seen in `DATABASE_URL` and `REDIS_URL`) but are **not**
published to the host. Keep it that way:

- **Do not** add `ports: ["5432:5432"]` to `postgres` or `ports: ["6379:6379"]`
  to `redis` on an internet-facing host. Doing so exposes the database and an
  unauthenticated Redis to the world.
- If you must reach Postgres from your workstation for debugging, prefer an SSH
  tunnel or `docker compose exec`, not a published port.

### 6.2 At the network edge

- Put only the `web` service (port `8000`) behind your reverse proxy / load
  balancer, terminate TLS there, and set `SESSION_COOKIE_SECURE=true`.
- Firewall the host so that `5432` and `6379` are not accepted from outside.
- Redis 7 here runs without a password on a private network; that is acceptable
  **only because it is not publicly reachable**. If you move Redis off the
  private network, add authentication and TLS.

### 6.3 Migrations

The one-shot `migrate` service runs `alembic upgrade head` and exits. It needs
`DATABASE_URL` but is not a network listener. The startup order is: `postgres`
healthy → `redis` healthy → `migrate` completes → `web` + `worker` start.

---

## 7. Secrets that must never enter the repository

Several paths are git-ignored on purpose. Verify with `git status` that none of
these are ever staged. From `.gitignore`:

```gitignore
.env
.env.*
!.env.example
**/token.json
secrets/*.json
secrets/*
!secrets/.gitkeep
tmp/*
data/processed_files.json
```

| Path | Why it is ignored |
| --- | --- |
| `.env`, `.env.*` | holds `APP_SECRET_KEY`, DB password, OAuth client secret, admin password |
| `.env.example` | **the only env file that IS committed** — placeholders only, no real values |
| `**/token.json` | legacy CLI Google token (plaintext credential) |
| `secrets/*.json`, `secrets/*` | service-account JSON and other secret material (`.gitkeep` kept) |
| `data/processed_files.json` | legacy CLI state; may reference private file ids |
| `tmp/*` | per-job scratch (downloaded MP4s, extracted WAV) |

Rules:

1. Copy `cp .env.example .env` and fill real values **locally only**. Never
   commit the result.
2. Mount `secrets/` read-only into containers; do not bake secrets into the
   image.
3. If a secret is ever committed by accident, **rotate it** (it is in history) —
   generate a new `APP_SECRET_KEY`, new OAuth client secret, new Deepgram keys —
   do not rely on a follow-up "remove file" commit.

---

## 8. Backups: the `postgres_data` volume

Because Postgres is the single source of truth, **your backup is the database**.
In the Compose setup the data lives in the named volume `postgres_data`.

### 8.1 What to back up

- **`postgres_data`** — users, encrypted credentials, jobs, and **transcripts**
  (meeting content). This is the only stateful volume that must survive.
- `redis_data` is transient queue state and does **not** require backup; it can
  be rebuilt from Postgres pending jobs on restart.

### 8.2 Logical backup (recommended)

A `pg_dump` is portable across minor version changes and is the easiest to
restore:

```bash
# Dump to a compressed SQL file on the host
docker compose exec -T postgres \
  pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" | gzip > backup_$(date +%F).sql.gz

# Restore into a fresh database
gunzip -c backup_2026-06-04.sql.gz | \
  docker compose exec -T postgres psql -U "$POSTGRES_USER" "$POSTGRES_DB"
```

(`POSTGRES_USER` / `POSTGRES_DB` come from your `.env`.)

### 8.3 Volume snapshot (alternative)

You can also archive the raw volume. **Stop the stack first** for a consistent
copy:

```bash
docker compose down
docker run --rm \
  -v meet-transcricao_postgres_data:/data \
  -v "$PWD":/backup alpine \
  tar czf /backup/postgres_data_$(date +%F).tar.gz -C /data .
docker compose up -d
```

(Confirm the exact volume name with `docker volume ls`; Compose prefixes it with
the project directory name.)

### 8.4 Backup hygiene

- **Backups contain everything sensitive**: encrypted credentials **and**
  plaintext transcripts. Encrypt backups at rest and restrict who can read them.
- A backup is only usable together with the matching **`APP_SECRET_KEY`** to
  decrypt credentials. Store that key separately (a secret manager), not inside
  the backup. Losing the key means the stored tokens/Deepgram keys in a restore
  are unrecoverable — users must re-connect.
- Test restores periodically; an untested backup is not a backup.

---

## 9. Privacy and consent for meeting recordings

This system ingests and stores the **content of meetings**. That carries legal
and ethical obligations beyond ordinary application data.

- **Consent to record.** Recording a Google Meet, and then transcribing it,
  generally requires that participants are informed and have consented. Consent
  requirements vary by jurisdiction (some require all-party consent). Make sure
  recordings fed into the source Drive folder were captured lawfully and with
  appropriate notice.
- **Content leaves your network when using Deepgram.** With
  `LOCAL_TRANSCRIPTION_ENABLED=false`, audio is sent to Deepgram, a third-party
  cloud service, for transcription. Disclose this in your privacy policy and
  confirm it is acceptable for the content involved. To keep audio entirely
  on-premises, enable the **local CPU engine**
  (`LOCAL_TRANSCRIPTION_ENABLED=true` with a valid faster-whisper / whisper.cpp
  model) — then no Deepgram key is required and audio never leaves the host. See
  [Local Transcription](06-local-transcription.md).
- **Stored transcripts are personal data.** `transcripts.transcript_text` and
  `transcript_json` may contain names, opinions, and other personal information
  spoken in the meeting. Apply your retention policy: define how long
  transcripts are kept and delete jobs/transcripts that are no longer needed.
- **Access control.** UI job and transcript reads are **user-scoped** — a user
  sees only their own jobs (`list_jobs_for_user`). Admin routes
  (`/admin/users`) are gated by `require_admin`. Keep the admin account strong
  (`ADMIN_PASSWORD`) and limited to people who genuinely need it.
- **Minimize the footprint.** Per-job scratch under `TMP_DIR/jobs/<job_id>/`
  (the downloaded MP4 and any extracted WAV) is always cleaned up after
  processing, so raw audio is not left lying around on the worker host.
- **Right to erasure / revocation.** Users can disconnect Google (revoking the
  stored token) and remove their Deepgram key. Provide a process to delete a
  user's stored transcripts on request, consistent with your jurisdiction's
  data-protection rules.

---

## 10. Security checklist (production)

- [ ] `APP_SECRET_KEY` set to a strong, unique value — **not**
      `change-me-in-production` — and **identical** on `web` and `worker`.
- [ ] `SESSION_COOKIE_SECURE=true` and the app served only over HTTPS.
- [ ] Strong `ADMIN_PASSWORD`, strong `POSTGRES_PASSWORD`.
- [ ] `postgres` and `redis` have **no** published host ports; only `web:8000`
      is exposed, behind a TLS-terminating proxy.
- [ ] `5432` and `6379` firewalled off from the public internet.
- [ ] No real secrets committed; `.env`, `secrets/*`, `token.json` confirmed
      git-ignored.
- [ ] Per-user Deepgram keys in use (no global `DEEPGRAM_API_KEY` in the
      web/worker deployment).
- [ ] Regular, encrypted backups of `postgres_data`, with `APP_SECRET_KEY`
      stored separately and restores tested.
- [ ] Privacy policy covers recording consent, third-party (Deepgram) audio
      processing, and transcript retention/deletion.
- [ ] `python -m pytest -v` green (includes
      `test_worker_adapter_does_not_log_secrets`).
