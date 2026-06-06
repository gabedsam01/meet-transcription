from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.repositories.memory import build_memory_repositories
from app.web.config import WebSettings
from app.web.helpers import (
    drive_download_url,
    extract_drive_folder_id,
    middle_truncate,
    short_datetime,
)
from app.web.main import create_app
from app.web.repositories import DriveSettings as AuthDriveSettings, GoogleToken as AuthGoogleToken
from tests.fakes import build_fake_repositories


# --- pure helpers -----------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("https://drive.google.com/drive/folders/1zv32QtBD5?usp=sharing", "1zv32QtBD5"),
        ("https://drive.google.com/drive/folders/1zv32QtBD5", "1zv32QtBD5"),
        ("https://drive.google.com/file/d/1abcDEF_ghi/view", "1abcDEF_ghi"),
        ("https://drive.google.com/open?id=1xyz789", "1xyz789"),
        ("1barEID-only_123", "1barEID-only_123"),
        ("  1trimmed  ", "1trimmed"),
        ("", ""),
        (None, ""),
    ],
)
def test_extract_drive_folder_id(value, expected):
    assert extract_drive_folder_id(value) == expected


def test_middle_truncate_long_short_and_none():
    assert middle_truncate("1zv32QWERTYUIOPASDFGHtBD5") == "1zv32Q…tBD5"
    assert middle_truncate("short") == "short"
    assert middle_truncate(None) == "—"


def test_short_datetime_formats_and_falls_back():
    assert short_datetime("2026-06-03T10:05:09+00:00") == "2026-06-03 10:05"
    assert short_datetime("2026-06-03T10:05:09Z") == "2026-06-03 10:05"
    assert short_datetime("not-a-date") == "not-a-date"
    assert short_datetime("") == "—"
    assert short_datetime(None) == "—"


def test_drive_download_url():
    assert (
        drive_download_url("abc123")
        == "https://drive.google.com/uc?export=download&id=abc123"
    )


# --- integrated UI (auth bundle for login, worker bundle for jobs) ----------


def _now():
    return datetime.now(timezone.utc)


def _settings(tmp_path) -> WebSettings:
    return WebSettings.from_env(
        {
            "ADMIN_USERNAME": "admin",
            "ADMIN_PASSWORD": "secret",
            "APP_SECRET_KEY": "a-long-secret-for-tests",
            "SESSION_COOKIE_SECURE": "false",
            "GOOGLE_WEB_CLIENT_ID": "client-id",
            "GOOGLE_WEB_CLIENT_SECRET": "client-secret",
            "GOOGLE_REDIRECT_URI": "http://localhost:8000/oauth/google/callback",
            "DATABASE_URL": "postgresql://test",
            "TMP_DIR": str(tmp_path / "tmp"),
        }
    )


def _app(tmp_path, worker, auth=None):
    # Admin bootstrap creates user id=1 in the auth bundle; the worker bundle owns jobs.
    return create_app(
        _settings(tmp_path),
        repositories=auth or build_fake_repositories(),
        worker_repositories=worker,
    )


def _login(client: TestClient) -> None:
    response = client.post(
        "/login", data={"username": "admin", "password": "secret"}, follow_redirects=False
    )
    assert response.status_code in {302, 303}


# --- job detail page --------------------------------------------------------


def test_job_detail_shows_full_fields_and_download(tmp_path):
    worker = build_memory_repositories()
    job = worker.jobs.create_job(1, "src-xyz", "call.mp4", _now())
    worker.transcripts.create(job.id, 1, "body", None, None, _now())
    worker.jobs.mark_completed(job.id, _now(), transcript_drive_file_id="txt-789")

    with TestClient(_app(tmp_path, worker)) as client:
        _login(client)
        page = client.get(f"/jobs/{job.id}")

    assert page.status_code == 200
    text = page.text
    assert "call.mp4" in text
    assert "src-xyz" in text
    assert "txt-789" in text
    assert "Download TXT" in text
    assert f"/jobs/{job.id}/download" in text  # local Postgres-backed transcript
    for label in ["Source file id", "Attempts", "Error", "Created", "Updated", "Processed"]:
        assert label in text


def test_job_detail_404_for_unknown_job(tmp_path):
    with TestClient(_app(tmp_path, build_memory_repositories())) as client:
        _login(client)
        resp = client.get("/jobs/999999")
    assert resp.status_code == 404
    assert "Job not found" in resp.text


def test_job_detail_is_scoped_to_owner(tmp_path):
    worker = build_memory_repositories()
    other_job = worker.jobs.create_job(2, "f", "other.mp4", _now())  # belongs to user 2
    worker.jobs.mark_completed(other_job.id, _now())

    with TestClient(_app(tmp_path, worker)) as client:
        _login(client)  # logs in as admin = user id 1
        resp = client.get(f"/jobs/{other_job.id}")

    assert resp.status_code == 404  # never expose another user's job


def test_long_source_id_is_truncated_but_full_value_in_title(tmp_path):
    worker = build_memory_repositories()
    long_id = "1zv32QWERTYUIOPASDFGHtBD5"
    worker.jobs.create_job(1, long_id, "meeting.mp4", _now())

    with TestClient(_app(tmp_path, worker)) as client:
        _login(client)
        text = client.get("/jobs").text

    assert "1zv32Q…tBD5" in text  # truncated for display
    assert f'title="{long_id}"' in text  # full value still available on hover


def test_failed_job_shows_badge_on_list_and_error_on_detail(tmp_path):
    worker = build_memory_repositories()
    job = worker.jobs.create_job(1, "f", "meet.mp4", _now())
    worker.jobs.mark_failed(job.id, "Deepgram exploded mid-transcription", _now())

    with TestClient(_app(tmp_path, worker)) as client:
        _login(client)
        list_page = client.get("/jobs").text
        detail_page = client.get(f"/jobs/{job.id}").text

    # Status is a badge on the list; the long error text lives on the detail page
    # so it never blows out the table.
    assert "badge-failed" in list_page
    assert "Deepgram exploded mid-transcription" not in list_page
    assert "Deepgram exploded mid-transcription" in detail_page


# --- dashboard --------------------------------------------------------------


def test_dashboard_shows_status_counts_and_ctas(tmp_path):
    auth = build_fake_repositories()
    auth.drive_settings.save_for_user(1, AuthDriveSettings("url", "src-folder", None, None, False))
    auth.google_tokens.save_for_user(1, AuthGoogleToken("a", "r", "u", "c", "s", "sc", None))
    auth.provider_credentials.save(1, "deepgram", "encrypted-key")
    worker = build_memory_repositories()
    job = worker.jobs.create_job(1, "file-1", "a.mp4", _now())
    worker.jobs.mark_completed(job.id, _now())

    with TestClient(_app(tmp_path, worker, auth=auth)) as client:
        _login(client)
        text = client.get("/").text

    assert "Conectado" in text  # Google
    assert "Configurado" in text  # Drive source + Models provider
    assert "Total jobs" in text
    assert "Último job" in text
    assert "badge-completed" in text  # last job status badge
    assert "/models" in text  # Models CTA (replaces the old Deepgram tab)
    assert "/jobs" in text  # Jobs CTA


# --- settings ---------------------------------------------------------------


def test_settings_landing_links_to_sections(tmp_path):
    with TestClient(_app(tmp_path, build_memory_repositories())) as client:
        _login(client)
        text = client.get("/settings").text
    assert "/settings/drive" in text
    assert "/models" in text
    assert "Drive folders" in text
    assert "Models" in text


def test_models_page_shows_status_without_full_key(tmp_path):
    with TestClient(_app(tmp_path, build_memory_repositories())) as client:
        _login(client)
        page = client.get("/models")
    assert page.status_code == 200
    assert "Models" in page.text
    assert "Não configurado" in page.text  # no per-user key saved yet
    assert "criptografad" in page.text  # encrypted-at-rest messaging
    # The model selectors list each provider's catalogue.
    assert "nova-3" in page.text
    assert "gemini-2.5-flash" in page.text


def test_deepgram_settings_redirects_to_models(tmp_path):
    with TestClient(_app(tmp_path, build_memory_repositories())) as client:
        _login(client)
        page = client.get("/settings/deepgram", follow_redirects=False)
    assert page.status_code == 303
    assert page.headers["location"] == "/models?provider=deepgram"
