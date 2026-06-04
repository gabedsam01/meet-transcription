# GHCR and CI/CD

This document explains how `meet-transcription` is built, tested, and published
as a container image to the **GitHub Container Registry (GHCR)**, and how to pull
that image in production.

The single source of truth is
[`.github/workflows/docker-publish.yml`](../.github/workflows/docker-publish.yml).
The published image is consumed by [`docker-compose.yml`](../docker-compose.yml)
via the `x-app` anchor. See also [Architecture](01-architecture.md) and the
deployment guide referenced from the compose file (`docs/deploy/dokploy.md`).

```
ghcr.io/gabedsam01/meet-transcription
```

The same image runs both the `web` and `worker` services (one image, two
commands) — see the compose `x-app` anchor below.

---

## 1. Workflow overview

The workflow is named **`Publish Docker image to GHCR`** and is defined in
`.github/workflows/docker-publish.yml`. It has two jobs that run in sequence:

| Job       | Purpose                                                        | Gate                    |
|-----------|---------------------------------------------------------------|-------------------------|
| `test`    | Install deps, `compileall`, run `pytest -q`.                  | Must pass.              |
| `publish` | Build the image and push it to GHCR with two tags.            | `needs: test` — only runs if `test` is green. |

The image name is fixed at the workflow level:

```yaml
env:
  IMAGE_NAME: ghcr.io/gabedsam01/meet-transcription
```

---

## 2. Triggers

The workflow runs on the following events (`on:` block):

| Event              | Condition                                                                 |
|--------------------|---------------------------------------------------------------------------|
| `push`             | Branches `main` **and** `integration/postgres-platform`.                  |
| `pull_request`     | PRs targeting `main`.                                                      |
| `workflow_dispatch`| Manual run from the GitHub Actions UI.                                     |

```yaml
on:
  push:
    branches:
      - main
      - integration/postgres-platform
  pull_request:
    branches:
      - main
  workflow_dispatch:
```

> **Important:** the `publish` job has no event filter of its own — it runs on
> *every* trigger as long as `test` passes, including `pull_request`. Both the
> `test` and `publish` jobs execute for PRs to `main`. The image is tagged the
> same way regardless of trigger (`:latest` and `:<short-sha>`).

---

## 3. The `test` job

Runs on `ubuntu-latest`. It validates the code exactly as the local
[validation commands](01-architecture.md) do, before any image is built.

```yaml
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt
      - name: Compile sources
        run: python -m compileall app scripts
      - name: Run tests
        run: python -m pytest -q
```

Steps in order:

1. **Checkout** the repository (`actions/checkout@v4`).
2. **Set up Python 3.11** (`actions/setup-python@v5`) — the same minor version
   as the runtime base image (`python:3.11-slim`).
3. **Install dependencies** from `requirements.txt`.
4. **Compile sources** with `python -m compileall app scripts` — a fast syntax
   gate over the `app/` and `scripts/` trees.
5. **Run tests** with `python -m pytest -q`.

The test suite uses dict-backed in-memory fakes; PostgreSQL integration tests
**skip** when no reachable `TEST_DATABASE_URL`/`DATABASE_URL` is configured
(they never fall back to SQLite), and local transcription engines are mocked
(no real model downloads). The full suite passes on the integration branch.

To reproduce the `test` job locally:

```bash
pip install -r requirements.txt
python -m compileall app scripts
python -m pytest -q
```

---

## 4. The `publish` job

Runs on `ubuntu-latest`, depends on `test` (`needs: test`), and requests the
minimal permissions needed to push a package:

```yaml
  publish:
    needs: test
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
```

`packages: write` is what allows the job's `GITHUB_TOKEN` to push to GHCR. Steps:

1. **Checkout** (`actions/checkout@v4`).
2. **Log in to GHCR** (`docker/login-action@v3`) using the built-in token:

   ```yaml
   - name: Log in to GHCR
     uses: docker/login-action@v3
     with:
       registry: ghcr.io
       username: ${{ github.actor }}
       password: ${{ secrets.GITHUB_TOKEN }}
   ```

   No personal access token or extra secret is required in CI — `GITHUB_TOKEN`
   is injected automatically by Actions and scoped by the `packages: write`
   permission above.

3. **Extract image tags** (`docker/metadata-action@v5`):

   ```yaml
   - name: Extract image tags
     id: meta
     uses: docker/metadata-action@v5
     with:
       images: ${{ env.IMAGE_NAME }}
       tags: |
         type=raw,value=latest
         type=sha,format=short,prefix=
   ```

   This produces two tags on every run:

   | Tag           | Source                                  | Example                                                  |
   |---------------|-----------------------------------------|----------------------------------------------------------|
   | `:latest`     | `type=raw,value=latest`                 | `ghcr.io/gabedsam01/meet-transcription:latest`           |
   | `:<short-sha>`| `type=sha,format=short,prefix=`         | `ghcr.io/gabedsam01/meet-transcription:a1b2c3d`          |

   `prefix=` removes the default `sha-` prefix, so the short-SHA tag is just the
   7-character commit hash (e.g. `a1b2c3d`). Use `:latest` for "the newest build
   on this branch" and `:<short-sha>` to pin an exact, reproducible commit.

4. **Set up Buildx** (`docker/setup-buildx-action@v3`).
5. **Build and push** (`docker/build-push-action@v6`):

   ```yaml
   - name: Build and push
     uses: docker/build-push-action@v6
     with:
       context: .
       push: true
       tags: ${{ steps.meta.outputs.tags }}
       labels: ${{ steps.meta.outputs.labels }}
       cache-from: type=gha
       cache-to: type=gha,mode=max
   ```

   The build context is the repository root (`context: .`) and uses the root
   `Dockerfile` (base `python:3.11-slim`). `cache-from`/`cache-to` with
   `type=gha` use the GitHub Actions layer cache (`mode=max` caches all stages)
   to speed up subsequent builds.

> **Build args are not set by CI.** The Dockerfile accepts
> `INSTALL_LOCAL_TRANSCRIPTION`, `INSTALL_FASTER_WHISPER`, and
> `INSTALL_WHISPER_CPP` (all default `false`), but the workflow does not pass
> any `build-args`. The published `:latest`/`:<short-sha>` images are therefore
> built **without** the optional local-transcription engines baked in — they
> run in the default Deepgram (per-user key) mode. To ship an image with
> `faster-whisper` or `whisper.cpp` support, build locally with the relevant
> build arg (see [Local transcription](06-local-transcription.md)).

---

## 5. Pulling the image in production

Production does **not** build the image; it pulls the published one. The
`docker-compose.yml` `x-app` anchor references the GHCR image directly, and the
`image` value doubles as the local build tag so the same name resolves whether
you build or pull:

```yaml
x-app: &app
  image: ghcr.io/gabedsam01/meet-transcription:latest
  build: .
```

### Pull and run with Compose (`:latest`)

```bash
cp .env.example .env      # populate the real secrets first
docker compose pull       # pulls ghcr.io/gabedsam01/meet-transcription:latest
docker compose up -d
```

On `up -d`, Compose brings the five services up in dependency order:

```
postgres (healthy) ─┐
redis    (healthy) ─┼─▶ migrate (alembic upgrade head, exits 0) ─▶ web + worker
                    ┘
```

The `migrate` one-shot, `web`, and `worker` all reuse the **same** GHCR image
(`alembic`, `alembic.ini`, and both entrypoints are baked in); only the
`command` differs. `migrate` runs `alembic upgrade head` and exits, and `web` +
`worker` wait for `service_completed_successfully` before starting. See
[Docker Compose / services](01-architecture.md) for the full service map.

### Pinning an exact commit (`:<short-sha>`)

To deploy a specific, immutable build instead of the moving `:latest` tag,
override the image with the short-SHA tag:

```bash
# .env (or shell export) — Compose substitutes it into the x-app image.
# (If your compose is parameterized; otherwise pull/run the tag directly.)
docker pull ghcr.io/gabedsam01/meet-transcription:a1b2c3d
```

You can also pull/run a pinned tag directly without Compose, e.g. for a quick
smoke test of the web service:

```bash
docker pull ghcr.io/gabedsam01/meet-transcription:a1b2c3d
docker run --rm -p 8000:8000 \
  --env-file .env \
  ghcr.io/gabedsam01/meet-transcription:a1b2c3d \
  uvicorn app.web.main:app --host 0.0.0.0 --port 8000
```

> In a real deployment the web service needs `postgres`, `redis`, and a migrated
> schema. Use `docker compose` rather than a bare `docker run` for anything
> beyond a smoke test, so dependencies and `DATABASE_URL`/`REDIS_URL` resolve.

### Updating to a newer build

```bash
docker compose pull          # fetch the latest :latest (or your pinned tag)
docker compose up -d          # recreate web/worker with the new image
```

`migrate` runs again on `up -d` and applies any new Alembic revisions before
`web`/`worker` start, so schema changes shipped in the new image are applied
automatically.

---

## 6. Image visibility and credentials

| Aspect             | Detail                                                                                     |
|--------------------|--------------------------------------------------------------------------------------------|
| Registry           | `ghcr.io` (GitHub Container Registry).                                                      |
| Package name       | `gabedsam01/meet-transcription`.                                                            |
| CI push auth       | `GITHUB_TOKEN` + `permissions: packages: write` (no extra secret needed).                  |
| Pull auth (public) | None — `docker pull` works anonymously.                                                     |
| Pull auth (private)| A token with the `read:packages` scope (see below).                                         |

### Public vs. private packages

GHCR package visibility is configured on GitHub, **not** in the workflow. A
package's visibility is set under
**GitHub → your profile/org → Packages → `meet-transcription` → Package
settings**, and a newly published package inherits its initial visibility from
there (commonly **private** until you make it public).

- **Public image** — anyone can `docker pull` it with no login:

  ```bash
  docker pull ghcr.io/gabedsam01/meet-transcription:latest
  ```

- **Private image** — the pulling host must authenticate first with a GitHub
  token that has the `read:packages` scope (a classic Personal Access Token, or
  a fine-grained token / deploy token with package read permission):

  ```bash
  echo "$GHCR_TOKEN" | docker login ghcr.io -u <github-username> --password-stdin
  docker compose pull
  ```

  On a CI runner that needs to *pull* (rather than push) this image, the
  built-in `GITHUB_TOKEN` with `permissions: packages: read` is sufficient when
  the package belongs to the same repository/owner.

### Linking the package to the repository

To get the package to appear on the repo's **Packages** sidebar and inherit repo
permissions, ensure it is linked to `meet-transcription` in the GHCR package
settings. The workflow already labels the image via `metadata-action`
(`labels: ${{ steps.meta.outputs.labels }}`), which includes the standard OCI
`org.opencontainers.image.source` label pointing back at the repository.

### Never bake secrets into the image

The image is a pure runtime artifact. All secrets — `APP_SECRET_KEY` (the Fernet
key), `POSTGRES_PASSWORD`, `ADMIN_PASSWORD`, `GOOGLE_WEB_CLIENT_SECRET`, and the
per-user encrypted Deepgram keys — are supplied at run time via the environment
(`.env` / `env_file`) and the database, never built into a layer. See
[Security](16-security.md) for how tokens and keys are encrypted at rest.

---

## 7. Quick reference

```bash
# Reproduce CI locally
pip install -r requirements.txt
python -m compileall app scripts
python -m pytest -q

# Pull the latest published image
docker pull ghcr.io/gabedsam01/meet-transcription:latest

# Authenticate to pull a private image
echo "$GHCR_TOKEN" | docker login ghcr.io -u <github-username> --password-stdin

# Production deploy (pull + run all five services)
docker compose pull
docker compose up -d
```

| Tag pattern   | Meaning                                  | When to use                       |
|---------------|------------------------------------------|-----------------------------------|
| `:latest`     | Newest successful build on a pushed branch| Default production tag.            |
| `:<short-sha>`| Exact commit (7-char hash)               | Pin an immutable, reproducible build. |
