from fastapi.testclient import TestClient

from app.web.config import WebSettings
from app.web.main import create_app
from app.web.passwords import hash_password
from tests.fakes import build_fake_repositories


def _settings(tmp_path) -> WebSettings:
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


def _client(tmp_path, repos=None):
    repos = repos or build_fake_repositories()
    return TestClient(create_app(_settings(tmp_path), repositories=repos)), repos


def _login(client, username, password):
    return client.post("/login", data={"username": username, "password": password},
                       follow_redirects=False)


def test_admin_can_create_and_list_user(tmp_path):
    client, repos = _client(tmp_path)
    with client:
        _login(client, "admin", "secret")
        client.post("/admin/users",
                    data={"email": "u@x.com", "password": "pw", "role": "user"},
                    follow_redirects=False)
        page = client.get("/admin/users").text
        assert "u@x.com" in page
        assert repos.users.get_by_email("u@x.com").role == "user"


def test_created_user_can_login(tmp_path):
    client, repos = _client(tmp_path)
    with client:
        _login(client, "admin", "secret")
        client.post("/admin/users",
                    data={"email": "u@x.com", "password": "pw", "role": "user"},
                    follow_redirects=False)
        client.post("/logout", follow_redirects=False)
        r = _login(client, "u@x.com", "pw")
        assert r.status_code in {302, 303}
        assert r.headers["location"] == "/"


def test_non_admin_blocked_from_admin_routes(tmp_path):
    client, repos = _client(tmp_path)
    # Pre-seed a normal user before lifespan (bootstrap only adds the admin).
    repos.users.create(email="u@x.com", password_hash=hash_password("pw"), role="user")
    with client:
        _login(client, "u@x.com", "pw")
        assert client.get("/admin/users", follow_redirects=False).status_code == 403
        assert client.post("/admin/users",
                           data={"email": "z@x.com", "password": "p", "role": "user"},
                           follow_redirects=False).status_code == 403


def test_admin_disable_enable_and_reset_password(tmp_path):
    client, repos = _client(tmp_path)
    with client:
        _login(client, "admin", "secret")
        client.post("/admin/users",
                    data={"email": "u@x.com", "password": "pw", "role": "user"},
                    follow_redirects=False)
        uid = repos.users.get_by_email("u@x.com").id

        client.post(f"/admin/users/{uid}/disable", follow_redirects=False)
        assert repos.users.get_by_id(uid).is_active is False
        client.post(f"/admin/users/{uid}/enable", follow_redirects=False)
        assert repos.users.get_by_id(uid).is_active is True

        old_hash = repos.users.get_password_hash(uid)
        client.post(f"/admin/users/{uid}/reset-password",
                    data={"new_password": "newpw"}, follow_redirects=False)
        assert repos.users.get_password_hash(uid) != old_hash
