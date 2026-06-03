from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import db
from app.web.config import WebSettings
from app.web.helpers import (
    drive_download_url,
    extract_drive_folder_id,
    middle_truncate,
    short_datetime,
)
from app.web.main import create_app
from app.web.security import fernet_from_secret
from app.web.token_store import TokenStore


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


# --- job detail page --------------------------------------------------------


def test_job_detail_shows_full_fields_and_download(tmp_path):
    settings = _settings(tmp_path)
    user = _seed(settings)
    job = db.create_job(
        settings.database_path,
        user["id"],
        status="pending",
        source_file_id="src-xyz",
        source_file_name="call.mp4",
    )
    db.update_job(
        settings.database_path,
        job["id"],
        status="completed",
        transcript_drive_file_id="txt-789",
        attempts=2,
    )

    with TestClient(create_app(settings)) as client:
        _login(client)
        page = client.get(f"/jobs/{job['id']}")

    assert page.status_code == 200
    text = page.text
    assert "call.mp4" in text
    assert "src-xyz" in text
    assert "txt-789" in text
    assert "Download TXT" in text
    for label in ["Source file id", "Attempts", "Error", "Created", "Updated", "Processed"]:
        assert label in text


def test_job_detail_404_for_unknown_job(tmp_path):
    settings = _settings(tmp_path)
    _seed(settings)
    with TestClient(create_app(settings)) as client:
        _login(client)
        resp = client.get("/jobs/999999")
    assert resp.status_code == 404
    assert "Job not found" in resp.text


def test_job_detail_is_scoped_to_owner(tmp_path):
    settings = _settings(tmp_path)
    _seed(settings)  # admin logs in below
    other = db.get_or_create_user(settings.database_path, "other@example.com")
    other_job = db.create_job(settings.database_path, other["id"], status="completed")

    with TestClient(create_app(settings)) as client:
        _login(client)
        resp = client.get(f"/jobs/{other_job['id']}")

    assert resp.status_code == 404  # never expose another user's job


def test_long_source_id_is_truncated_but_full_value_in_title(tmp_path):
    settings = _settings(tmp_path)
    user = _seed(settings)
    long_id = "1zv32QWERTYUIOPASDFGHtBD5"
    db.create_job(
        settings.database_path,
        user["id"],
        status="completed",
        source_file_id=long_id,
        source_file_name="meeting.mp4",
    )

    with TestClient(create_app(settings)) as client:
        _login(client)
        text = client.get("/jobs").text

    assert "1zv32Q…tBD5" in text  # truncated for display
    assert f'title="{long_id}"' in text  # full value still available on hover


# --- dashboard --------------------------------------------------------------


def test_dashboard_shows_status_counts_and_ctas(tmp_path):
    settings = _settings(tmp_path)
    user = _seed(settings)
    db.create_job(
        settings.database_path, user["id"], status="completed", source_file_name="a.mp4"
    )

    with TestClient(create_app(settings)) as client:
        _login(client)
        text = client.get("/").text

    assert "Connected" in text  # Google
    assert "Configured" in text  # Drive source + Deepgram
    assert "Total jobs" in text
    assert "Last job" in text
    assert "badge-completed" in text  # last job status badge
    assert "/settings/deepgram" in text  # Deepgram CTA
    assert "/jobs" in text  # Jobs CTA


# --- settings ---------------------------------------------------------------


def test_settings_landing_links_to_sections(tmp_path):
    settings = _settings(tmp_path)
    _seed(settings)
    with TestClient(create_app(settings)) as client:
        _login(client)
        text = client.get("/settings").text
    assert "/settings/drive" in text
    assert "/settings/deepgram" in text
    assert "Drive folders" in text
    assert "Deepgram API key" in text


def test_settings_drive_saves_and_extracts_id_from_url(tmp_path):
    settings = _settings(tmp_path)
    user = _seed(settings)

    with TestClient(create_app(settings)) as client:
        _login(client)
        assert client.get("/settings/drive").status_code == 200
        resp = client.post(
            "/settings/drive",
            data={
                "source_drive_folder": "https://drive.google.com/drive/folders/1AAAsource111?usp=sharing",
                "destination_drive_folder": "1BBBdest222",
                "poll_interval_seconds": "120",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/settings/drive"

    row = db.get_settings(settings.database_path, user["id"])
    assert row["source_drive_folder_id"] == "1AAAsource111"  # extracted from the URL
    assert row["destination_drive_folder_id"] == "1BBBdest222"  # bare id kept as-is
    assert row["poll_interval_seconds"] == 120


def test_settings_deepgram_explains_per_user_key(tmp_path):
    settings = _settings(tmp_path)
    _seed(settings)
    with TestClient(create_app(settings)) as client:
        _login(client)
        page = client.get("/settings/deepgram")
    assert page.status_code == 200
    assert "Deepgram API key" in page.text
    assert "encrypted" in page.text  # per-user, no env fallback messaging
    assert "feat/auth-users-settings" in page.text  # owned by the auth branch


# --- helpers ----------------------------------------------------------------


def _seed(settings: WebSettings):
    db.init_db(settings.database_path)
    user = db.get_or_create_user(settings.database_path, "admin", "admin")
    db.save_settings(settings.database_path, user["id"], "source", "dest", 60)
    TokenStore(
        settings.database_path, fernet_from_secret(settings.app_secret_key)
    ).save_for_user(
        user["id"],
        {
            "access_token": "access",
            "refresh_token": "refresh",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "client-id",
            "client_secret": "client-secret",
            "scopes": "https://www.googleapis.com/auth/drive",
            "expiry": "2026-06-03T00:00:00+00:00",
        },
    )
    return user


def _login(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"username": "admin", "password": "secret"},
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}


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
            "DATABASE_URL": str(tmp_path / "app.db"),
            "DEEPGRAM_API_KEY": "dg-key",
            "TMP_DIR": str(tmp_path / "tmp"),
        }
    )
