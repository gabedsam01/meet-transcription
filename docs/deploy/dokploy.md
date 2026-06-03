# Deploying on Dokploy

This guide deploys the **web + worker + postgres** architecture on
[Dokploy](https://dokploy.com/) using the Compose file in this repository.

> **Not independently deployable yet.** This branch (`feat/ui-devops-polish`) is
> the UI/DevOps layer. A full end-to-end deploy depends on `feat/postgres-core`
> (Postgres repositories), `feat/auth-users-settings` (per-user OAuth/Deepgram),
> and `feat/postgres-worker` (`app.worker.main`) being integrated first.

## Overview

- One application image runs both **web** and **worker** (different commands).
- **postgres** stays internal — it is never exposed to the internet.
- Only the **web** service gets a public domain, on port **8000**.

## 1. Create a Compose application

In Dokploy, create a **Compose** service pointing at this repository (or paste
the `docker-compose.yml`). For production, pull the published image instead of
building — edit the `x-app` anchor:

```yaml
x-app: &app
  image: ghcr.io/gabedsam01/meet-transcription:latest
  # build: .        # comment out in production; pull the GHCR image instead
  restart: unless-stopped
```

If the GHCR package is private, add a registry credential for `ghcr.io` in
Dokploy so it can pull the image.

## 2. Expose only the web service

- Attach your domain to the **web** service only.
- Set the container port to **8000** (the web service listens on `0.0.0.0:8000`).
- Enable HTTPS (Let's Encrypt) on the domain.
- Do **not** attach a domain to `worker` or `postgres`. The worker has no HTTP
  surface, and Postgres must remain internal.

The services talk to each other over the internal Compose network, so the web
and worker containers reach Postgres at hostname `postgres:5432`.

## 3. Environment variables

Set these on the application (Dokploy lets you define env per Compose project).
Use strong, unique secrets in production.

```env
# Web
ADMIN_USERNAME=your-admin
ADMIN_PASSWORD=a-strong-password
APP_SECRET_KEY=a-long-random-string         # also the encryption key for tokens
SESSION_COOKIE_SECURE=true                   # required behind HTTPS
GOOGLE_WEB_CLIENT_ID=...
GOOGLE_WEB_CLIENT_SECRET=...
GOOGLE_REDIRECT_URI=https://SEU_DOMINIO/oauth/google/callback
DEEPGRAM_API_KEY=...

# Database
POSTGRES_DB=meet
POSTGRES_USER=meet
POSTGRES_PASSWORD=a-strong-db-password
DATABASE_URL=postgresql://meet:a-strong-db-password@postgres:5432/meet

# Worker
WORKER_POLL_INTERVAL_SECONDS=30
STALE_JOB_TIMEOUT_MINUTES=30
```

> **Database note.** PostgreSQL is the only datastore — there is no SQLite mode.
> The Postgres repository layer is owned by `feat/postgres-core`; until it merges,
> this branch still ships the legacy SQLite `app/db.py`, so a full end-to-end
> deploy waits on that branch.

## 4. Google OAuth redirect URI

In Google Cloud → APIs & Services → Credentials, the **Web application** OAuth
client must list an authorized redirect URI that exactly matches your domain:

```
https://SEU_DOMINIO/oauth/google/callback
```

`GOOGLE_REDIRECT_URI` in the environment must be identical, character for
character, or Google will reject the login.

## 5. Volumes

The Compose file declares:

- `postgres_data` — named volume for the database. Keep it persistent; losing it
  loses all users, settings, tokens, and job history.
- `./data` — SQLite database and scratch (used while on SQLite). Optional once
  on Postgres.
- `./tmp` — temporary MP4/transcript files during processing (ephemeral).
- `./secrets` (read-only on worker) — only needed by the legacy `app.main`
  worker; the DB-driven worker reads encrypted tokens from the database.

In Dokploy, ensure the `postgres_data` volume (and `./data` if you stay on
SQLite) is mapped to persistent storage.

## 6. First run

1. Deploy. Wait for `postgres` to become healthy; `web` and `worker` start after.
2. Open `https://SEU_DOMINIO`, sign in with `ADMIN_USERNAME` / `ADMIN_PASSWORD`.
3. Click **Connect Google** and complete OAuth.
4. In **Settings → Drive folders**, paste the source and destination Drive
   folder links (or ids).
5. Trigger a job from **Jobs → Run once**, then open the job to follow progress.

## Running the legacy worker instead

If you are not yet running the new DB-driven worker, run the original
env-driven worker by overriding the command:

```bash
docker compose run --rm worker python -m app.main --watch
```

See the README "Simple Worker Mode" section for its environment variables.
