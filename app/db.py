from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BUSY_TIMEOUT_MS = 5000


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect_db(path: str | Path) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(path: str | Path) -> None:
    with connect_db(path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                name TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS google_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE,
                access_token TEXT NOT NULL,
                refresh_token TEXT,
                token_uri TEXT NOT NULL,
                client_id TEXT NOT NULL,
                client_secret TEXT,
                scopes TEXT NOT NULL,
                expiry TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE,
                source_drive_folder_id TEXT NOT NULL,
                destination_drive_folder_id TEXT NOT NULL,
                poll_interval_seconds INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS transcription_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                source_file_id TEXT,
                source_file_name TEXT,
                transcript_drive_file_id TEXT,
                status TEXT NOT NULL,
                error_message TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                processed_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            """
        )


def get_or_create_user(path: str | Path, email: str, name: str | None = None) -> sqlite3.Row:
    now = utc_now()
    with connect_db(path) as conn:
        conn.execute(
            """
            INSERT INTO users (email, name, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET name = excluded.name
            """,
            (email, name, now),
        )
        return conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()


def get_user_by_id(path: str | Path, user_id: int) -> sqlite3.Row | None:
    with connect_db(path) as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def save_settings(
    path: str | Path,
    user_id: int,
    source_drive_folder_id: str,
    destination_drive_folder_id: str,
    poll_interval_seconds: int,
) -> sqlite3.Row:
    now = utc_now()
    with connect_db(path) as conn:
        conn.execute(
            """
            INSERT INTO settings (
                user_id, source_drive_folder_id, destination_drive_folder_id,
                poll_interval_seconds, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                source_drive_folder_id = excluded.source_drive_folder_id,
                destination_drive_folder_id = excluded.destination_drive_folder_id,
                poll_interval_seconds = excluded.poll_interval_seconds,
                updated_at = excluded.updated_at
            """,
            (
                user_id,
                source_drive_folder_id,
                destination_drive_folder_id,
                poll_interval_seconds,
                now,
                now,
            ),
        )
        return get_settings(path, user_id)


def get_settings(path: str | Path, user_id: int) -> sqlite3.Row | None:
    with connect_db(path) as conn:
        return conn.execute("SELECT * FROM settings WHERE user_id = ?", (user_id,)).fetchone()


def create_job(
    path: str | Path,
    user_id: int,
    status: str,
    source_file_id: str | None = None,
    source_file_name: str | None = None,
) -> sqlite3.Row:
    now = utc_now()
    with connect_db(path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO transcription_jobs (
                user_id, source_file_id, source_file_name, status,
                attempts, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, source_file_id, source_file_name, status, 0, now, now),
        )
        return conn.execute(
            "SELECT * FROM transcription_jobs WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()


def update_job(path: str | Path, job_id: int, **fields: Any) -> sqlite3.Row:
    allowed = {
        "source_file_id",
        "source_file_name",
        "transcript_drive_file_id",
        "status",
        "error_message",
        "attempts",
        "processed_at",
    }
    updates = {key: value for key, value in fields.items() if key in allowed}
    updates["updated_at"] = utc_now()
    if updates.get("status") == "completed" and not updates.get("processed_at"):
        updates["processed_at"] = utc_now()

    assignments = ", ".join(f"{key} = ?" for key in updates)
    values = list(updates.values()) + [job_id]
    with connect_db(path) as conn:
        conn.execute(f"UPDATE transcription_jobs SET {assignments} WHERE id = ?", values)
        return conn.execute(
            "SELECT * FROM transcription_jobs WHERE id = ?", (job_id,)
        ).fetchone()


def list_jobs(path: str | Path, user_id: int) -> list[sqlite3.Row]:
    with connect_db(path) as conn:
        return conn.execute(
            """
            SELECT * FROM transcription_jobs
            WHERE user_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (user_id,),
        ).fetchall()


def get_latest_jobs(path: str | Path, user_id: int, limit: int = 5) -> list[sqlite3.Row]:
    with connect_db(path) as conn:
        return conn.execute(
            """
            SELECT * FROM transcription_jobs
            WHERE user_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()


def save_google_token(path: str | Path, user_id: int, token_data: dict[str, Any]) -> sqlite3.Row:
    now = utc_now()
    with connect_db(path) as conn:
        conn.execute(
            """
            INSERT INTO google_tokens (
                user_id, access_token, refresh_token, token_uri, client_id,
                client_secret, scopes, expiry, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                access_token = excluded.access_token,
                refresh_token = excluded.refresh_token,
                token_uri = excluded.token_uri,
                client_id = excluded.client_id,
                client_secret = excluded.client_secret,
                scopes = excluded.scopes,
                expiry = excluded.expiry,
                updated_at = excluded.updated_at
            """,
            (
                user_id,
                token_data["access_token"],
                token_data.get("refresh_token"),
                token_data["token_uri"],
                token_data["client_id"],
                token_data.get("client_secret"),
                token_data["scopes"],
                token_data.get("expiry"),
                now,
                now,
            ),
        )
        return get_google_token(path, user_id)


def get_google_token(path: str | Path, user_id: int) -> sqlite3.Row | None:
    with connect_db(path) as conn:
        return conn.execute(
            "SELECT * FROM google_tokens WHERE user_id = ?", (user_id,)
        ).fetchone()
