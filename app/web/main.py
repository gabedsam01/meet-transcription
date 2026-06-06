from __future__ import annotations

import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import requests
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.drive_client import DriveClient
from app.google_auth import credentials_from_token
from app.logger import setup_logging
from app.queue import QueueSettings, build_queue
from app.repositories import RepositoryBackendError
from app.repositories import build_repositories as build_worker_repositories
from app.services.download_service import DownloadError, get_downloadable_transcript
from app.services.drive_watcher import poll_user
from app.services.guardrails import resolve_guardrails
from app.services.job_service import create_next_pending_job
from app.transcription.config import TranscriptionConfig
from app.transcription.provider import get_transcription_provider_status
from app.web import helpers
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
# Template filters keep long Drive ids and ISO timestamps from breaking layout.
templates.env.filters["mid"] = helpers.middle_truncate
templates.env.filters["dt"] = helpers.short_datetime
templates.env.filters["drive_dl"] = helpers.drive_download_url

# Run-once only creates a pending job; the worker does the processing. Keys match
# JobCreationResult.status from app.services.job_service.create_next_pending_job.
RUN_ONCE_MESSAGES = {
    "no_settings": "Configure a pasta de origem em Drive Settings primeiro.",
    "not_connected": "Conecte o Google antes de rodar uma transcrição.",
    "no_deepgram_key": "Configure sua Deepgram API Key antes de iniciar uma transcrição.",
    "no_new_videos": "Nenhum vídeo novo para transcrever.",
    "created": "Job enfileirado; o worker fará o processamento.",
}
DEEPGRAM_TEST_MESSAGES = {
    "valid": "Deepgram API Key válida.",
    "invalid": "Deepgram API Key inválida.",
    "unverifiable": "Não foi possível verificar agora.",
}


def create_app(settings: WebSettings | None = None,
               repositories: RepositoryBundle | None = None,
               worker_repositories=None,
               transcription_status=None,
               queue=None) -> FastAPI:
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
    # Auth bundle (users/settings/tokens/deepgram) — also used by require_user.
    app.state.repositories = repos
    app.state.token_store = token_store
    app.state.deepgram_store = deepgram_store
    # Worker bundle (jobs/transcripts over the same PostgreSQL) — injected in
    # tests; built lazily from the environment in production.
    app.state.worker_repositories = worker_repositories
    # Local-vs-Deepgram posture for the UI + run-once gating, and the queue the web
    # layer enqueues to. Both default from the environment; tests inject them.
    app.state.transcription_status = (
        transcription_status
        if transcription_status is not None
        else get_transcription_provider_status(TranscriptionConfig.from_env())
    )
    app.state.queue = queue if queue is not None else build_queue(QueueSettings.from_env())
    # The worker owns download/transcribe/upload. The web layer only lists Drive
    # videos to enqueue a pending job; these hooks isolate that Drive boundary.
    app.state.build_drive_client = (
        lambda credentials, src, dst: DriveClient.from_credentials(credentials, src, dst)
    )
    app.state.credentials_from_token = credentials_from_token
    app.add_middleware(
        SessionMiddleware,
        secret_key=web_settings.app_secret_key,
        https_only=web_settings.session_cookie_secure,
        same_site="lax",
    )
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    def _resolve_worker_repositories():
        """Return ``(worker Repositories, None)`` or ``(None, error_message)``.

        PostgreSQL is the single source of truth: in production the worker bundle
        is built once from the environment and cached on app.state. An unknown
        WORKER_REPOSITORY_BACKEND degrades gracefully (RepositoryBackendError);
        genuine misconfiguration (e.g. a missing DATABASE_URL) surfaces loudly.
        """
        if app.state.worker_repositories is not None:
            return app.state.worker_repositories, None
        try:
            app.state.worker_repositories = build_worker_repositories(
                os.environ.get("WORKER_REPOSITORY_BACKEND")
            )
            return app.state.worker_repositories, None
        except RepositoryBackendError as exc:
            return None, str(exc)

    def _queue_status() -> dict:
        """UI-facing queue health: poll mode (no Redis), online, or unavailable."""
        queue = app.state.queue
        if queue is None:
            return {"mode": "poll", "available": None}
        try:
            available = queue.health()
        except Exception:  # noqa: BLE001 - a status probe must never 500 the page
            available = False
        return {"mode": "queue", "available": available}

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
        worker_repos, _ = _resolve_worker_repositories()
        jobs = worker_repos.jobs.list_jobs_for_user(user.id) if worker_repos else []
        return templates.TemplateResponse(request, "dashboard.html", {
            "user": user,
            "settings": repos.drive_settings.get_for_user(user.id),
            "google_connected": repos.google_tokens.get_for_user(user.id) is not None,
            "deepgram_configured": deepgram_store.has_key(user.id),
            "transcription_status": app.state.transcription_status,
            "queue_status": _queue_status(),
            "total_jobs": len(jobs),
            "last_job": jobs[0] if jobs else None,
            "jobs": jobs[:5],
        })

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request, user=Depends(require_user)):
        # Landing page that links out to the focused settings sections.
        return templates.TemplateResponse(request, "settings.html", {"user": user})

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

    @app.get("/settings/automation", response_class=HTMLResponse)
    def automation_page(request: Request, user=Depends(require_user)):
        worker_repos, error = _resolve_worker_repositories()
        automation = (
            worker_repos.automation.get_for_user(user.id)
            if worker_repos and worker_repos.automation else None
        )
        return templates.TemplateResponse(request, "automation_settings.html", {
            "user": user,
            "automation": automation,
            "message": _pop_flash(request),
            "backend_error": error,
        })

    @app.post("/settings/automation")
    def save_automation(
        request: Request,
        user=Depends(require_user),
        auto_poll_enabled: bool = Form(False),
        poll_interval_seconds: int = Form(300),
        max_files_per_poll: int = Form(5),
    ):
        worker_repos, error = _resolve_worker_repositories()
        if worker_repos is None or worker_repos.automation is None:
            _set_flash(request, error or "Automação indisponível no momento.")
            return RedirectResponse("/settings/automation", status_code=303)
        # Clamp to sane bounds so a typo can't hammer Drive or starve the loop.
        interval = max(60, min(86400, int(poll_interval_seconds)))
        max_files = max(1, min(100, int(max_files_per_poll)))
        worker_repos.automation.upsert_for_user(
            user.id,
            auto_poll_enabled=bool(auto_poll_enabled),
            poll_interval_seconds=interval,
            max_files_per_poll=max_files,
        )
        _set_flash(request, "Configurações de automação salvas.")
        return RedirectResponse("/settings/automation", status_code=303)

    @app.post("/automation/check-now")
    def check_now(request: Request, user=Depends(require_user)):
        # Lightweight, in-request Drive *listing* + pending-job creation — the same
        # class of work as Run once. It never downloads or transcribes.
        worker_repos, error = _resolve_worker_repositories()
        if worker_repos is None:
            _set_flash(request, error)
            return RedirectResponse("/jobs", status_code=303)
        automation = (
            worker_repos.automation.get_for_user(user.id)
            if worker_repos.automation else None
        )
        guardrails = resolve_guardrails(
            automation, default_max_file_size_mb=None, default_daily_jobs_limit=None
        )
        max_files = (automation.max_files_per_poll if automation else None) or 5
        try:
            result = poll_user(
                worker_repos,
                build_drive_client=app.state.build_drive_client,
                credentials_from_token=app.state.credentials_from_token,
                user_id=user.id,
                now=_utc_now(),
                max_files=max_files,
                deepgram_required=app.state.transcription_status.deepgram_required,
                guardrails=guardrails,
            )
        except Exception:  # noqa: BLE001 - surface as a flash, never a 500.
            logging.exception("check-now failed for user_id=%s", user.id)
            _set_flash(request, "Não foi possível verificar agora. Tente novamente.")
            return RedirectResponse("/jobs", status_code=303)

        for job_id in result.job_ids:
            if app.state.queue is not None:
                try:
                    app.state.queue.enqueue(job_id)
                except Exception:  # noqa: BLE001 - Postgres has it pending; reconciler heals.
                    logging.exception("Could not enqueue job_id=%s after check-now", job_id)
        if worker_repos.automation is not None:
            worker_repos.automation.mark_poll_result(
                user.id, _utc_now(), success=not result.error_code,
                error_code=result.error_code, error_message=result.error_message,
            )
        if result.error_code:
            _set_flash(request, result.error_message or "Verificação falhou.")
        elif result.created:
            _set_flash(request, f"{result.created} novo(s) job(s) enfileirado(s).")
        else:
            _set_flash(request, result.error_message or "Nenhum vídeo novo para transcrever.")
        return RedirectResponse("/jobs", status_code=303)

    @app.post("/jobs/{job_id}/retry")
    def retry_job(request: Request, job_id: int, user=Depends(require_user)):
        worker_repos, error = _resolve_worker_repositories()
        if worker_repos is None:
            _set_flash(request, error)
            return RedirectResponse("/jobs", status_code=303)
        job = worker_repos.jobs.get_job(job_id)
        # Owner-scoped: another user's (or unknown) job 404s so it never leaks.
        if job is None or job.user_id != user.id:
            return templates.TemplateResponse(
                request, "error.html", {"user": user, "message": "Job not found."},
                status_code=404,
            )
        if job.status != "failed":
            _set_flash(request, "Só é possível repetir jobs com falha.")
            return RedirectResponse("/jobs", status_code=303)
        worker_repos.jobs.reset_job_for_retry(job_id, _utc_now())
        if app.state.queue is not None:
            try:
                app.state.queue.remove_dead(job_id)
                app.state.queue.enqueue(job_id)
            except Exception:  # noqa: BLE001 - reconciler re-enqueues if Redis is down.
                logging.exception("Could not re-enqueue retried job_id=%s", job_id)
        _set_flash(request, "Job re-enfileirado para nova tentativa.")
        return RedirectResponse("/jobs", status_code=303)

    @app.get("/admin/queue", response_class=HTMLResponse)
    def admin_queue(request: Request, admin=Depends(require_admin)):
        queue = app.state.queue
        stats = None
        dead_ids: list[int] = []
        if queue is not None:
            try:
                stats = queue.queue_stats()
                dead_ids = sorted(queue.dead_job_ids())
            except Exception:  # noqa: BLE001 - a status page must never 500.
                logging.exception("Could not read queue stats")
        worker_repos, _ = _resolve_worker_repositories()
        status_counts = (
            worker_repos.jobs.count_jobs_by_status() if worker_repos else {}
        )
        return templates.TemplateResponse(request, "queue_status.html", {
            "user": admin,
            "queue_status": _queue_status(),
            "stats": stats,
            "dead_ids": dead_ids,
            "status_counts": status_counts,
            "message": _pop_flash(request),
        })

    @app.get("/jobs", response_class=HTMLResponse)
    def jobs_page(request: Request, user=Depends(require_user)):
        worker_repos, error = _resolve_worker_repositories()
        jobs = worker_repos.jobs.list_jobs_for_user(user.id) if worker_repos else []
        return templates.TemplateResponse(request, "jobs.html", {
            "user": user,
            "jobs": jobs,
            "message": _pop_flash(request),
            "backend_error": error,
            "transcription_status": app.state.transcription_status,
            "queue_status": _queue_status(),
        })

    @app.post("/jobs/run-once")
    def run_once(request: Request, user=Depends(require_user)):
        worker_repos, error = _resolve_worker_repositories()
        if worker_repos is None:
            _set_flash(request, error)
            return RedirectResponse("/jobs", status_code=303)
        try:
            result = create_next_pending_job(
                worker_repos,
                build_drive_client=app.state.build_drive_client,
                credentials_from_token=app.state.credentials_from_token,
                user_id=user.id,
                # A valid local engine drops the Deepgram-key requirement.
                deepgram_required=app.state.transcription_status.deepgram_required,
            )
        except Exception:  # noqa: BLE001 - surface Drive/credential errors as a flash, not a 500.
            logging.exception("run-once failed to create a job for user_id=%s", user.id)
            _set_flash(request, "Não foi possível iniciar a transcrição agora. Tente novamente.")
            return RedirectResponse("/jobs", status_code=303)
        flash_message = RUN_ONCE_MESSAGES.get(result.status, "Run-once finalizado.")
        # Enqueue the pending job for the worker. Best-effort: if Redis is down the
        # job stays pending in Postgres and the worker reconciles it on startup/idle.
        if result.status == "created" and result.job is not None and app.state.queue is not None:
            try:
                app.state.queue.enqueue(result.job.id)
                logging.getLogger(__name__).info(
                    "Enqueued job_id=%s user_id=%s", result.job.id, user.id
                )
            except Exception:  # noqa: BLE001 - Postgres is the source of truth; never 500 here.
                logging.getLogger(__name__).exception(
                    "Could not enqueue job_id=%s; it stays pending for the worker to reconcile",
                    result.job.id,
                )
                flash_message = (
                    "Fila indisponível no momento: a transcrição foi registrada e será "
                    "processada assim que a fila voltar."
                )
        _set_flash(request, flash_message)
        return RedirectResponse("/jobs", status_code=303)

    @app.get("/jobs/{job_id}/download")
    def download_transcript(job_id: int, request: Request, user=Depends(require_user)):
        worker_repos, error = _resolve_worker_repositories()
        if worker_repos is None:
            raise HTTPException(status_code=503, detail=error)
        # Strict per-user ownership: even an admin gets 404 for another user's job,
        # so existence of other users' jobs never leaks (requirement 8d).
        try:
            result = get_downloadable_transcript(worker_repos, job_id, user.id, is_admin=False)
        except DownloadError as exc:
            status = {"not_found": 404, "not_completed": 409, "no_transcript": 404}.get(
                exc.code, 400
            )
            raise HTTPException(status_code=status, detail=str(exc))
        return PlainTextResponse(
            result.text,
            headers={
                "Content-Disposition": f'attachment; filename="{result.filename}"'
            },
        )

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_detail_page(request: Request, job_id: int, user=Depends(require_user)):
        worker_repos, _ = _resolve_worker_repositories()
        job = worker_repos.jobs.get_job(job_id) if worker_repos else None
        # Owner-scoped: unknown ids and other users' jobs both render a 404,
        # so the existence of another user's job never leaks.
        if job is None or job.user_id != user.id:
            return templates.TemplateResponse(
                request, "error.html", {"user": user, "message": "Job not found."},
                status_code=404,
            )
        return templates.TemplateResponse(request, "job_detail.html", {"user": user, "job": job})

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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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
