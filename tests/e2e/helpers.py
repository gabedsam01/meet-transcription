"""Shared builders for the integrated end-to-end tests.

These drive the *real* FastAPI app and the *real* ``JobProcessor`` through the
project's in-memory fakes (never a browser, never Postgres) — the "E2E integrado"
approach: a logged-in admin clicks through onboarding / run-once / download while a
fake worker processes jobs out of band, exactly as the two services would.

The web app uses two repository bundles: the *auth* bundle (login, Google/Drive
status shown in the UI) and the *worker* bundle (jobs/transcripts). Login
bootstraps the admin as user id=1; seed whichever bundle a scenario needs.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.models import GoogleToken as WorkerGoogleToken, Settings as WorkerSettings
from app.transcription.provider import ProviderStatus
from app.web.config import WebSettings
from app.web.main import create_app
from app.web.repositories import DriveSettings, GoogleToken as AuthGoogleToken
from app.worker.processor import JobProcessor
from tests.fakes import build_fake_repositories
from tests.support import make_worker_container

ADMIN_ID = 1  # ensure_admin bootstraps the admin as user id=1


def now() -> datetime:
    return datetime.now(timezone.utc)


def web_settings(tmp_path) -> WebSettings:
    return WebSettings.from_env({
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD": "secret",
        "APP_SECRET_KEY": "a-long-secret-for-tests",
        "SESSION_COOKIE_SECURE": "false",
        "GOOGLE_WEB_CLIENT_ID": "client-id",
        "GOOGLE_WEB_CLIENT_SECRET": "client-secret",
        "GOOGLE_REDIRECT_URI": "http://localhost:8000/oauth/google/callback",
        "DATABASE_URL": "postgresql://test",
        "TMP_DIR": str(tmp_path / "tmp"),
    })


# --- provider posture fakes (mirror get_transcription_provider_status) ------


def deepgram_required_status() -> ProviderStatus:
    return ProviderStatus(
        enabled=False, local_valid=False, deepgram_required=True, summary=None,
        message="Deepgram (transcrição local desativada).", doc_url=None,
    )


def local_invalid_status(doc_url: str = "https://docs.example/local") -> ProviderStatus:
    return ProviderStatus(
        enabled=True, local_valid=False, deepgram_required=True, summary=None,
        message="Modelo local inválido. Consulte a documentação de modelos locais.",
        doc_url=doc_url, reason="faster-whisper indisponível",
    )


def local_valid_status() -> ProviderStatus:
    return ProviderStatus(
        enabled=True, local_valid=True, deepgram_required=False,
        summary="faster-whisper small", message="Modelo local ativo: faster-whisper small",
        doc_url="https://docs.example/local",
    )


# --- app + auth helpers -----------------------------------------------------


def build_app(tmp_path, *, auth=None, worker=None, queue=None,
              transcription_status=None, drive=None):
    auth = auth if auth is not None else build_fake_repositories()
    app = create_app(
        web_settings(tmp_path), repositories=auth, worker_repositories=worker,
        queue=queue, transcription_status=transcription_status,
    )
    if drive is not None:
        app.state.build_drive_client = lambda credentials, src, dst: drive
        app.state.credentials_from_token = lambda token: object()
    return app


def login(client, username: str = "admin", password: str = "secret"):
    response = client.post(
        "/login", data={"username": username, "password": password}, follow_redirects=False
    )
    assert response.status_code in {302, 303}, response.text
    return response


def seed_worker_ready(worker, *, deepgram_key="user-dg-key", source="src-folder",
                      dest="dst-folder", save_copy=False) -> None:
    """Seed the worker bundle so run-once/the processor have everything they need."""
    worker.settings.set(WorkerSettings(ADMIN_ID, source, dest, save_copy, deepgram_key))
    worker.google_tokens.set(
        ADMIN_ID, WorkerGoogleToken(access_token="a", token_uri="u", client_id="c")
    )


def seed_auth_connected(auth, *, source="src-folder") -> None:
    """Seed the auth bundle so the dashboard/onboarding show Google + Drive ready."""
    auth.google_tokens.save_for_user(ADMIN_ID, AuthGoogleToken(
        access_token="x", refresh_token=None, token_uri="u", client_id="c",
        client_secret=None, scopes="drive", expiry=None,
    ))
    auth.drive_settings.save_for_user(ADMIN_ID, DriveSettings(
        source_drive_folder_url="https://drive.google.com/drive/folders/src",
        source_drive_folder_id=source, destination_drive_folder_url=None,
        destination_drive_folder_id=None, save_copy_to_drive=False,
    ))


def seed_deepgram_key(auth, value: str = "encrypted-key-blob") -> None:
    auth.deepgram_credentials.save_for_user(ADMIN_ID, value)
    # Also seed the new per-provider key store so provider_readiness works.
    if hasattr(auth, "provider_credentials") and auth.provider_credentials is not None:
        auth.provider_credentials.save(ADMIN_ID, "deepgram", value)


def run_worker_once(tmp_path, worker, *, drive=None, deepgram=None,
                    transcription_config=None, transcription_probes=None,
                    build_local_provider=None, webhook_notifier=None,
                    job_max_attempts=None) -> int:
    """Run a fake worker over every pending job in ``worker`` (claim → process).

    ``job_max_attempts=1`` disables retries so a single failure is terminal — used
    by terminal-failure E2E tests (dead-letter UI / job.failed webhook), since a
    retryable error would otherwise be rescheduled (pending) instead of failed.
    """
    import dataclasses

    container = make_worker_container(
        tmp_path, repositories=worker, drive=drive, deepgram=deepgram,
        transcription_config=transcription_config, transcription_probes=transcription_probes,
        build_local_provider=build_local_provider,
    )
    if job_max_attempts is not None:
        container.settings = dataclasses.replace(
            container.settings, job_max_attempts=job_max_attempts
        )
    if webhook_notifier is not None:
        container.webhook_notifier = webhook_notifier
    processor = JobProcessor(container)
    processed = 0
    while True:
        job = worker.jobs.claim_next_pending_job("e2e-worker", now())
        if job is None:
            break
        processor.process(job)
        processed += 1
    return processed
