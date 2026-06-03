from __future__ import annotations

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

from app import db
from app.web.config import WebSettings
from app.web.security import fernet_from_secret
from app.web.token_store import TokenStore


TEMPLATE_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


def create_app(settings: WebSettings | None = None) -> FastAPI:
    web_settings = settings or WebSettings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.init_db(web_settings.database_path)
        yield

    app = FastAPI(title="Meet Transcription", lifespan=lifespan)
    app.state.settings = web_settings
    app.state.token_store = TokenStore(
        web_settings.database_path, fernet_from_secret(web_settings.app_secret_key)
    )
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
    def login(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
    ):
        if username != web_settings.admin_username or password != web_settings.admin_password:
            return templates.TemplateResponse(
                request,
                "login.html",
                {"error": "Invalid username or password"},
                status_code=401,
            )
        user = db.get_or_create_user(
            web_settings.database_path, email=username, name=username
        )
        request.session["user_id"] = user["id"]
        request.session["user_email"] = user["email"]
        return RedirectResponse("/", status_code=303)

    @app.post("/logout")
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, user=Depends(require_user)):
        settings_row = db.get_settings(web_settings.database_path, user["id"])
        token_row = db.get_google_token(web_settings.database_path, user["id"])
        jobs = db.get_latest_jobs(web_settings.database_path, user["id"], limit=5)
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "user": user,
                "settings": settings_row,
                "google_connected": token_row is not None,
                "jobs": jobs,
            },
        )

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request, user=Depends(require_user)):
        settings_row = db.get_settings(web_settings.database_path, user["id"])
        return templates.TemplateResponse(
            request,
            "settings.html",
            {"user": user, "settings": settings_row},
        )

    @app.post("/settings")
    def save_settings(
        user=Depends(require_user),
        source_drive_folder_id: str = Form(...),
        destination_drive_folder_id: str = Form(...),
        poll_interval_seconds: int = Form(...),
    ):
        db.save_settings(
            web_settings.database_path,
            user_id=user["id"],
            source_drive_folder_id=source_drive_folder_id,
            destination_drive_folder_id=destination_drive_folder_id,
            poll_interval_seconds=poll_interval_seconds,
        )
        return RedirectResponse("/settings", status_code=303)

    @app.get("/jobs", response_class=HTMLResponse)
    def jobs_page(request: Request, user=Depends(require_user)):
        jobs = db.list_jobs(web_settings.database_path, user["id"])
        return templates.TemplateResponse(
            request,
            "jobs.html",
            {"user": user, "jobs": jobs},
        )

    @app.post("/jobs/run-once")
    def run_once(user=Depends(require_user)):
        from app.web.services import run_once_for_user

        run_once_for_user(web_settings, user["id"])
        return RedirectResponse("/jobs", status_code=303)

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
        app.state.token_store.save_for_user(user["id"], token_data)
        request.session.pop("oauth_state", None)
        return RedirectResponse("/", status_code=303)

    return app


def require_user(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        raise_redirect = HTTPException(status_code=303, headers={"Location": "/login"})
        raise raise_redirect
    user = db.get_user_by_id(request.app.state.settings.database_path, int(user_id))
    if user is None:
        request.session.clear()
        raise HTTPException(status_code=303, headers={"Location": "/login"})
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


class LazyApp:
    def __init__(self) -> None:
        self._app: FastAPI | None = None

    async def __call__(self, scope, receive, send) -> None:
        if self._app is None:
            self._app = create_app()
        await self._app(scope, receive, send)


app = LazyApp()
