# Deploying on Dokploy

This guide deploys the **web + worker + postgres** architecture on
[Dokploy](https://dokploy.com/) using the Compose file in this repository.

## Overview

- One application image runs both **web** and **worker** (different commands).
- **postgres** stays internal — it is never exposed to the internet, and it is
  the single source of truth (no SQLite).
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
APP_SECRET_KEY=a-long-random-string         # also the encryption key for tokens/keys
SESSION_COOKIE_SECURE=true                   # required behind HTTPS
GOOGLE_WEB_CLIENT_ID=...
GOOGLE_WEB_CLIENT_SECRET=...
GOOGLE_REDIRECT_URI=https://YOUR_DOMAIN/oauth/google/callback

# Database (PostgreSQL — single source of truth, no SQLite)
POSTGRES_DB=meet_transcription
POSTGRES_USER=meet_user
POSTGRES_PASSWORD=a-strong-db-password
DATABASE_URL=postgresql+psycopg://meet_user:a-strong-db-password@postgres:5432/meet_transcription

# Worker
WORKER_REPOSITORY_BACKEND=postgres
WORKER_POLL_INTERVAL_SECONDS=30
WORKER_CONCURRENCY=1
STALE_JOB_TIMEOUT_MINUTES=30
```

> **Deepgram keys are per user.** The Web UI stores each user's Deepgram API key
> **encrypted** in PostgreSQL, so there is **no global `DEEPGRAM_API_KEY`** in the
> web/worker deployment. (The global env var exists only for the legacy CLI
> worker, `python -m app.main`.)

## 4. Google OAuth redirect URI

In Google Cloud → APIs & Services → Credentials, the **Web application** OAuth
client must list an authorized redirect URI that exactly matches your domain:

```
https://YOUR_DOMAIN/oauth/google/callback
```

`GOOGLE_REDIRECT_URI` in the environment must be identical, character for
character, or Google will reject the login.

## 5. Volumes

The Compose file declares:

- `postgres_data` — named volume for the database (the single source of truth).
  Keep it persistent; losing it loses all users, settings, tokens, transcripts,
  and job history.
- `./tmp` — temporary MP4/transcript files during processing (ephemeral).
- `./data` — scratch only; the database lives in Postgres, not here.
- `./secrets` (read-only) — only needed by the legacy `app.main` CLI worker; the
  DB-driven worker reads encrypted tokens/keys from the database.

In Dokploy, ensure the `postgres_data` volume is mapped to persistent storage.

## 6. First run

1. Deploy. Wait for `postgres` to become healthy; `web` and `worker` start after.
2. Apply the schema once: run `alembic upgrade head` in the web container
   (e.g. `docker compose run --rm web alembic upgrade head`).
3. Open `https://YOUR_DOMAIN`, sign in with `ADMIN_USERNAME` / `ADMIN_PASSWORD`.
4. Click **Connect Google** and complete OAuth.
5. In **Settings → Deepgram**, save your personal Deepgram API key (required).
6. In **Settings → Drive folders**, paste the source (and optional destination)
   Drive folder links (or ids).
7. Trigger a job from **Jobs → Run once**, then open the job to follow progress
   and download the transcript when it completes.

## Running the legacy CLI worker (compatibility only)

The original env-driven worker is kept for compatibility and is **not** the
Compose `worker` service. Run it on demand by overriding the command:

```bash
docker compose run --rm worker python -m app.main --watch
```

See the README "Legacy Simple Worker Mode" section for its environment variables.
