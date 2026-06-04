from app.web.repositories import DriveSettings, GoogleToken
from tests.fakes import build_fake_repositories


def test_users_create_get_and_ensure_admin():
    repos = build_fake_repositories()
    repos.users.ensure_admin(email="admin", password_hash="h1")
    admin = repos.users.get_by_email("admin")
    assert admin.role == "admin" and admin.is_active
    assert repos.users.get_password_hash(admin.id) == "h1"

    # idempotent: keeps a single admin, updates hash
    repos.users.ensure_admin(email="admin", password_hash="h2")
    assert len(repos.users.list_all()) == 1
    assert repos.users.get_password_hash(admin.id) == "h2"

    u = repos.users.create(email="u@x.com", password_hash="ph", role="user")
    assert u.role == "user"
    repos.users.set_active(u.id, False)
    assert repos.users.get_by_id(u.id).is_active is False
    repos.users.set_google_identity(u.id, "g@x.com", "G")
    assert repos.users.get_by_id(u.id).google_email == "g@x.com"


def test_jobs_create_list_and_active():
    repos = build_fake_repositories()
    assert repos.jobs.find_active_for_user(1) is None
    j = repos.jobs.create_job(user_id=1, status="pending")
    assert j.status == "pending"
    assert repos.jobs.find_active_for_user(1).id == j.id
    assert [x.id for x in repos.jobs.list_jobs_for_user(1)] == [j.id]
    assert repos.jobs.list_jobs_for_user(2) == []


def test_token_and_deepgram_and_drive_roundtrip():
    repos = build_fake_repositories()
    repos.google_tokens.save_for_user(1, GoogleToken("a", "r", "u", "c", "s", "sc", None))
    assert repos.google_tokens.get_for_user(1).access_token == "a"
    repos.deepgram_credentials.save_for_user(1, "cipher")
    assert repos.deepgram_credentials.get_encrypted_for_user(1) == "cipher"
    ds = DriveSettings("url", "id", None, None, True)
    repos.drive_settings.save_for_user(1, ds)
    assert repos.drive_settings.get_for_user(1).save_copy_to_drive is True
