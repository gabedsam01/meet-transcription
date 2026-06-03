from app import db


def test_init_db_creates_schema_and_enables_wal(tmp_path):
    db_path = tmp_path / "app.db"

    db.init_db(db_path)

    with db.connect_db(db_path) as conn:
        table_names = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]

    assert {"users", "google_tokens", "settings", "transcription_jobs"} <= table_names
    assert journal_mode == "wal"
    assert busy_timeout > 0


def test_user_settings_and_jobs_roundtrip(tmp_path):
    db_path = tmp_path / "app.db"
    db.init_db(db_path)

    user = db.get_or_create_user(db_path, email="admin@example.com", name="Admin")
    db.save_settings(
        db_path,
        user_id=user["id"],
        source_drive_folder_id="source",
        destination_drive_folder_id="destination",
        poll_interval_seconds=300,
    )
    job = db.create_job(
        db_path,
        user_id=user["id"],
        source_file_id="file123",
        source_file_name="meeting.mp4",
        status="pending",
    )
    db.update_job(
        db_path,
        job["id"],
        status="completed",
        transcript_drive_file_id="txt123",
    )

    settings = db.get_settings(db_path, user["id"])
    jobs = db.list_jobs(db_path, user["id"])

    assert settings["source_drive_folder_id"] == "source"
    assert settings["destination_drive_folder_id"] == "destination"
    assert jobs[0]["status"] == "completed"
    assert jobs[0]["transcript_drive_file_id"] == "txt123"
