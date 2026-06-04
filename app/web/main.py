from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import requests
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.logger import setup_logging
from app.web import services
from app.web.config import WebSettings
from app.web.deepgram_key import DeepgramKeyStore, verify_deepgram_key
from app.web.drive_links import extract_google_drive_folder_id
from app.web.passwords import hash_password, verify_password
from app.web.repositories import DriveSettings, RepositoryBundle, build_repositories
from app.web.security import fernet_from_secret
from app.web.token_store import TokenStore

TEMPLATE_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

RUN_ONCE_MESSAGES = {
    "missing_settings": "Configure a pasta de origem em Drive Settings primeiro.",
    "not_connected": "Conecte o Google antes de rodar uma transcrição.",
    "no_deepgram_key": "Configure sua Deepgram API Key antes de iniciar uma transcrição.",
    "already_running": "Já existe um job em execução.",
    "created": "Job enfileirado; o worker fará o processamento.",
}
DEEPGRAM_TEST_MESSAGES = {
    "valid": "Deepgram API Key válida.",
    "invalid": "Deepgram API Key inválida.",
    "unverifiable": "Não foi possível verificar agora.",
}


def create_app(settings: WebSettings | None = None,
               repositories: RepositoryBundle | None = None) -> FastAPI:
    setup_logging()
    web_settings = settings or WebSettings.from_env()
    repos = repositories or build_repositories(web_settings)
    fernet = fernet_from_secret(web_settings.app_secret_key)
    token_store = TokenStore(repos.google_tokens, fernet)
    deepgram_store = DeepgramKeyStore(repos.deepgram_credentials, fernet)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        repos.users.ensure_admin(
            email=web_settings.admin_username,
            password_hash=hash_password(web_settings.admin_password),
        )
        yield

    app = FastAPI(title="Meet Transcription", lifespan=lifespan)
    app.state.settings = web_settings
    app.state.repositories = repos
    app.state.token_store = token_store
    app.state.deepgram_store = deepgram_store
    app.add_middleware(
        SessionMiddleware,
        secret_key=web_settings.app_secret_key,
        https_only=web_settings.session_cookie_secure,
        same_site="lax",
    )
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        return templates.TemplateResponse(request, "login.html")

    @app.post("/login")
    def login(request: Request, username: str = Form(...), password: str = Form(...)):
        user = repos.users.get_by_email(username.strip())
        pw_hash = repos.users.get_password_hash(user.id) if user else None
        if user is None or not user.is_active or not verify_password(password, pw_hash):
            return templates.TemplateResponse(
                request, "login.html", {"error": "Invalid email or password"}, status_code=401
            )
        request.session["user_id"] = user.id
        request.session["user_email"] = user.email
        return RedirectResponse("/", status_code=303)

    @app.post("/logout")
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, user=Depends(require_user)):
        return templates.TemplateResponse(request, "dashboard.html", {
            "user": user,
            "settings": repos.drive_settings.get_for_user(user.id),
            "google_connected": repos.google_tokens.get_for_user(user.id) is not None,
            "deepgram_configured": deepgram_store.has_key(user.id),
            "jobs": repos.jobs.list_jobs_for_user(user.id, limit=5),
        })

    @app.get("/settings")
    def settings_redirect():
        return RedirectResponse("/settings/drive", status_code=303)

    @app.get("/settings/drive", response_class=HTMLResponse)
    def drive_settings_page(request: Request, user=Depends(require_user)):
        return templates.TemplateResponse(request, "settings_drive.html", {
            "user": user,
            "settings": repos.drive_settings.get_for_user(user.id),
            "message": _pop_flash(request),
        })

    @app.post("/settings/drive")
    def save_drive_settings(
        request: Request,
        user=Depends(require_user),
        source_drive_folder_url: str = Form(...),
        destination_drive_folder_url: str = Form(""),
        save_copy_to_drive: bool = Form(False),
    ):
        try:
            source_id = extract_google_drive_folder_id(source_drive_folder_url)
            dest_url = destination_drive_folder_url.strip() or None
            dest_id = extract_google_drive_folder_id(dest_url) if dest_url else None
        except ValueError as exc:
            return templates.TemplateResponse(request, "settings_drive.html", {
                "user": user,
                "settings": repos.drive_settings.get_for_user(user.id),
                "error": str(exc),
            }, status_code=400)
        repos.drive_settings.save_for_user(user.id, DriveSettings(
            source_drive_folder_url=source_drive_folder_url.strip(),
            source_drive_folder_id=source_id,
            destination_drive_folder_url=dest_url,
            destination_drive_folder_id=dest_id,
            save_copy_to_drive=bool(save_copy_to_drive),
        ))
        _set_flash(request, "Drive settings salvos.")
        return RedirectResponse("/settings/drive", status_code=303)

    @app.get("/settings/deepgram", response_class=HTMLResponse)
    def deepgram_page(request: Request, user=Depends(require_user)):
        return templates.TemplateResponse(request, "settings_deepgram.html", {
            "user": user,
            "configured": deepgram_store.has_key(user.id),
            "masked": deepgram_store.masked(user.id),
            "message": _pop_flash(request),
        })

    @app.post("/settings/deepgram")
    def save_deepgram(request: Request, user=Depends(require_user),
                      deepgram_api_key: str = Form(...)):
        key = deepgram_api_key.strip()
        if not key:
            _set_flash(request, "Deepgram API Key não pode ser vazia.")
        else:
            deepgram_store.save_for_user(user.id, key)
            _set_flash(request, "Deepgram API Key salva.")
        return RedirectResponse("/settings/deepgram", status_code=303)

    @app.post("/settings/deepgram/test")
    def test_deepgram(request: Request, user=Depends(require_user)):
        key = deepgram_store.get_key(user.id)
        if not key:
            _set_flash(request, "Configure sua Deepgram API Key antes de iniciar uma transcrição.")
        else:
            _set_flash(request, DEEPGRAM_TEST_MESSAGES[verify_deepgram_key(key)])
        return RedirectResponse("/settings/deepgram", status_code=303)

    @app.get("/jobs", response_class=HTMLResponse)
    def jobs_page(request: Request, user=Depends(require_user)):
        return templates.TemplateResponse(request, "jobs.html", {
            "user": user,
            "jobs": repos.jobs.list_jobs_for_user(user.id),
            "message": _pop_flash(request),
        })

    @app.post("/jobs/run-once")
    def run_once(request: Request, user=Depends(require_user)):
        result = services.enqueue_run_once_job(repos, deepgram_store, user.id)
        _set_flash(request, RUN_ONCE_MESSAGES[result.status])
        return RedirectResponse("/jobs", status_code=303)

    @app.get("/admin/users", response_class=HTMLResponse)
    def admin_users(request: Request, admin=Depends(require_admin)):
        return templates.TemplateResponse(request, "admin_users.html", {
            "user": admin,
            "users": repos.users.list_all(),
            "message": _pop_flash(request),
        })

    @app.post("/admin/users")
    def admin_create_user(request: Request, admin=Depends(require_admin),
                          email: str = Form(...), password: str = Form(...),
                          role: str = Form("user")):
        email = email.strip()
        if not email or not password:
            _set_flash(request, "Email e senha são obrigatórios.")
        elif repos.users.get_by_email(email) is not None:
            _set_flash(request, f"Usuário já existe: {email}")
        else:
            repos.users.create(
                email=email, password_hash=hash_password(password),
                role="admin" if role == "admin" else "user",
            )
            _set_flash(request, f"Usuário criado: {email}")
        return RedirectResponse("/admin/users", status_code=303)

    @app.post("/admin/users/{user_id}/disable")
    def admin_disable_user(request: Request, user_id: int, admin=Depends(require_admin)):
        repos.users.set_active(user_id, False)
        _set_flash(request, "Usuário desativado.")
        return RedirectResponse("/admin/users", status_code=303)

    @app.post("/admin/users/{user_id}/enable")
    def admin_enable_user(request: Request, user_id: int, admin=Depends(require_admin)):
        repos.users.set_active(user_id, True)
        _set_flash(request, "Usuário ativado.")
        return RedirectResponse("/admin/users", status_code=303)

    @app.post("/admin/users/{user_id}/reset-password")
    def admin_reset_password(request: Request, user_id: int, admin=Depends(require_admin),
                             new_password: str = Form(...)):
        if not new_password.strip():
            _set_flash(request, "Nova senha não pode ser vazia.")
        else:
            repos.users.set_password_hash(user_id, hash_password(new_password))
            _set_flash(request, "Senha redefinida.")
        return RedirectResponse("/admin/users", status_code=303)

    @app.get("/connect-google")
    def connect_google(request: Request, user=Depends(require_user)):
        state = secrets.token_urlsafe(32)
        request.session["oauth_state"] = state
        params = {
            "client_id": web_settings.google_web_client_id,
            "redirect_uri": web_settings.google_redirect_uri,
            "response_type": "code",
            "scope": "https://www.googleapis.com/auth/drive",
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
        return RedirectResponse(url, status_code=303)

    @app.get("/oauth/google/callback")
    def oauth_callback(request: Request, code: str, state: str, user=Depends(require_user)):
        expected_state = request.session.get("oauth_state")
        if not expected_state or state != expected_state:
            raise HTTPException(status_code=400, detail="Invalid OAuth state")
        token_data = exchange_google_code(web_settings, code)
        token_store.save_for_user(user.id, token_data)
        profile = fetch_google_userinfo(token_data["access_token"])
        if profile:
            repos.users.set_google_identity(user.id, profile.get("email"), profile.get("name"))
        request.session.pop("oauth_state", None)
        return RedirectResponse("/", status_code=303)

    return app


def _set_flash(request: Request, message: str) -> None:
    request.session["flash"] = message


def _pop_flash(request: Request) -> str | None:
    return request.session.pop("flash", None)


def require_user(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    user = request.app.state.repositories.users.get_by_id(int(user_id))
    if user is None or not user.is_active:
        request.session.clear()
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user


def require_admin(request: Request, user=Depends(require_user)):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def exchange_google_code(settings: WebSettings, code: str) -> dict:
    response = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": settings.google_web_client_id,
            "client_secret": settings.google_web_client_secret,
            "redirect_uri": settings.google_redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    expires_in = int(payload.get("expires_in", 3600))
    expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    return {
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token"),
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": settings.google_web_client_id,
        "client_secret": settings.google_web_client_secret,
        "scopes": payload.get("scope", "https://www.googleapis.com/auth/drive"),
        "expiry": expiry.replace(microsecond=0).isoformat(),
    }


def fetch_google_userinfo(access_token: str) -> dict | None:
    """Best-effort fetch of the connected Google account's email/name."""
    try:
        response = requests.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        if response.status_code == 200:
            return response.json()
    except Exception:  # noqa: BLE001 - identity is optional, never block the callback
        logging.warning("Could not fetch Google userinfo")
    return None


class LazyApp:
    def __init__(self) -> None:
        self._app: FastAPI | None = None

    async def __call__(self, scope, receive, send) -> None:
        if self._app is None:
            self._app = create_app()
        await self._app(scope, receive, send)


app = LazyApp()
