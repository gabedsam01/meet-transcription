from __future__ import annotations

import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import requests
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app.database.connection import DatabaseConfigError
from app.drive_client import DriveClient
from app.exports import available_formats
from app.google_auth import credentials_from_token
from app.logger import setup_logging
from app.queue import QueueSettings, build_queue
from app.recordings import (
    RecordingMetadata,
    new_recording_id,
    recording_path,
    source_file_id_for,
    write_metadata,
)
from app.repositories import RepositoryBackendError
from app.repositories import build_repositories as build_worker_repositories
from app.services.download_service import (
    DownloadError,
    get_downloadable_transcript,
    get_transcript_export,
)
from app.services.drive_watcher import poll_user
from app.services.guardrails import resolve_guardrails
from app.services.job_service import create_next_pending_job
from app.summaries import get_summary_status
from app.transcription.config import TranscriptionConfig
from app.transcription.provider import get_transcription_provider_status
from app.transcription.provider_config import (
    default_model_settings,
    with_fallback,
    with_primary,
)
from app.transcription.provider_models import (
    SELECTABLE_PROVIDERS,
    get_provider_spec,
    is_cloud_provider,
)
from app.version import get_version_info
from app.web import helpers
from app.web.config import WebSettings
from app.web.deepgram_key import DeepgramKeyStore
from app.web.drive_links import extract_google_drive_folder_id
from app.web.passwords import hash_password, verify_password
from app.web.provider_keys import ProviderKeyStore, verify_provider_key
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
PROVIDER_TEST_MESSAGES = {
    "valid": "API key válida.",
    "invalid": "API key inválida.",
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
    # Models tab: per-provider encrypted keys + per-user provider/model selection.
    provider_key_store = (
        ProviderKeyStore(repos.provider_credentials, fernet)
        if repos.provider_credentials is not None
        else None
    )
    model_settings_repo = repos.model_settings

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
    app.state.provider_key_store = provider_key_store
    app.state.model_settings_repo = model_settings_repo
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
    # Reject an oversized upload by Content-Length BEFORE the multipart body is
    # buffered to a temp file, so a (authenticated) client cannot force the server
    # to spool gigabytes to disk. The in-handler streaming check stays the
    # authoritative cap on the stored recording (and covers chunked uploads with no
    # Content-Length). A reverse proxy body limit is still recommended in prod.
    app.add_middleware(
        _UploadSizeLimitMiddleware,
        path="/api/recordings/upload",
        max_bytes=lambda: web_settings.extension_upload_max_mb * 1024 * 1024,
    )
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    def _session_user(request: Request):
        """Best-effort current user for error pages; never raises."""
        try:
            user_id = request.session.get("user_id")
            if user_id:
                return repos.users.get_by_id(int(user_id))
        except Exception:  # noqa: BLE001 - the error page must render regardless.
            return None
        return None

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        """Render a friendly HTML error page for browser navigation.

        Preserves the auth gate's redirect (303 + Location to /login) and keeps the
        JSON ``{"detail": ...}`` shape for non-HTML clients (API calls, the download
        endpoint, the test client). No tracebacks ever reach the response.
        """
        headers = dict(exc.headers or {})
        location = headers.get("location") or headers.get("Location")
        if location:
            return RedirectResponse(location, status_code=exc.status_code)
        wants_html = "text/html" in request.headers.get("accept", "")
        if wants_html and exc.status_code != 405:
            context = {"user": _session_user(request), **_http_error_context(exc.status_code, exc.detail)}
            return templates.TemplateResponse(
                request, "error.html", context, status_code=exc.status_code
            )
        return JSONResponse(
            {"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers or None
        )

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
        except (RepositoryBackendError, DatabaseConfigError) as exc:
            # Degrade gracefully (e.g. unset/invalid DATABASE_URL) so /ready answers
            # 503 and the pages flash an error instead of returning a 500. The
            # messages are operator-facing config text, never secrets.
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

    def _model_settings_for(user_id: int):
        saved = model_settings_repo.get_for_user(user_id) if model_settings_repo else None
        return saved or default_model_settings()

    def _providers_view(user_id: int) -> list[dict]:
        view = []
        for pid in SELECTABLE_PROVIDERS:
            spec = get_provider_spec(pid)
            view.append({
                "id": pid,
                "label": spec.label,
                "models": spec.models,
                "requires_api_key": spec.requires_api_key,
                "diarization": spec.diarization,
                "size_note": _size_note(spec),
                "docs_url": spec.docs_url,
                "configured": bool(provider_key_store and provider_key_store.has(user_id, pid)),
                "masked": provider_key_store.masked(user_id, pid) if provider_key_store else None,
            })
        return view

    def _primary_ready(user_id: int, model_settings) -> bool:
        spec = get_provider_spec(model_settings.primary_provider)
        if spec is None or not spec.requires_api_key:
            return True
        return bool(provider_key_store and provider_key_store.has(user_id, model_settings.primary_provider))

    def _queue_backend_name() -> str:
        queue = app.state.queue
        if queue is None:
            return "none"
        name = type(queue).__name__.lower()
        if "redis" in name:
            return "redis"
        if "memory" in name:
            return "memory"
        return "queue"

    @app.get("/health")
    def health():
        # Liveness only: the process is up. No Google OAuth, DB, or Redis required,
        # so a load balancer can probe it cheaply. Shape is asserted by tests.
        return {"status": "ok"}

    @app.get("/ready")
    def ready():
        """Readiness: Postgres + schema (migrations) + queue backend. Never 500s.

        Returns 200 with ``{"status": "ready", ...}`` only when the database is
        reachable AND the queue is usable (or running in poll mode); otherwise 503
        with ``{"status": "degraded", "checks": {...}}`` so orchestrators can wait.
        """
        checks: dict = {}
        worker_repos, db_error = _resolve_worker_repositories()
        if worker_repos is None:
            checks["database"] = {"ok": False, "detail": db_error or "worker repositories unavailable"}
        else:
            try:
                worker_repos.jobs.list_pending_jobs()
                checks["database"] = {"ok": True}
            except Exception:  # noqa: BLE001 - readiness must never raise.
                checks["database"] = {"ok": False, "detail": "database unreachable"}
        # A successful jobs query touches a migrated table, so it doubles as a
        # migrations check: schema present ⇒ `alembic upgrade head` ran.
        checks["migrations"] = {
            "ok": checks["database"]["ok"],
            "detail": "schema present" if checks["database"]["ok"] else "schema unverified",
        }
        qs = _queue_status()
        backend = _queue_backend_name()
        if qs["mode"] == "poll":
            checks["queue"] = {"ok": True, "mode": "poll", "backend": backend}
        else:
            checks["queue"] = {"ok": bool(qs["available"]), "mode": "queue", "backend": backend}
        is_ready = checks["database"]["ok"] and checks["queue"]["ok"]
        return JSONResponse(
            {"status": "ready" if is_ready else "degraded", "checks": checks},
            status_code=200 if is_ready else 503,
        )

    @app.get("/version")
    def version():
        """Build + provider posture. Public, secret-free (commit/version/providers)."""
        status = app.state.transcription_status
        info = get_version_info()
        info["providers"] = {
            "local_enabled": bool(getattr(status, "enabled", False)),
            "local_valid": bool(getattr(status, "local_valid", False)),
            "deepgram_required": bool(getattr(status, "deepgram_required", True)),
            "queue_backend": _queue_backend_name(),
            "summaries_enabled": get_summary_status().enabled,
        }
        return info

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
        model_settings = _model_settings_for(user.id)
        primary_spec = get_provider_spec(model_settings.primary_provider)
        return templates.TemplateResponse(request, "dashboard.html", {
            "user": user,
            "settings": repos.drive_settings.get_for_user(user.id),
            "google_connected": repos.google_tokens.get_for_user(user.id) is not None,
            "model_settings": model_settings,
            "provider_label": primary_spec.label if primary_spec else model_settings.primary_provider,
            "provider_ready": _primary_ready(user.id, model_settings),
            "transcription_status": app.state.transcription_status,
            "queue_status": _queue_status(),
            "total_jobs": len(jobs),
            "last_job": jobs[0] if jobs else None,
            "jobs": jobs[:5],
        })

    @app.get("/onboarding", response_class=HTMLResponse)
    def onboarding(request: Request, user=Depends(require_user)):
        """Guided setup checklist computed from the user's real configuration state."""
        drive = repos.drive_settings.get_for_user(user.id)
        status = app.state.transcription_status
        google_connected = repos.google_tokens.get_for_user(user.id) is not None
        folder_valid = bool(drive and drive.source_drive_folder_id)
        deepgram_configured = deepgram_store.has_key(user.id)
        # A valid local engine needs no key; otherwise a per-user Deepgram key is it.
        provider_ready = (not status.deepgram_required) or deepgram_configured
        qs = _queue_status()
        queue_online = qs["mode"] == "poll" or bool(qs["available"])
        worker_repos, _ = _resolve_worker_repositories()
        worker_online = worker_repos is not None
        automation_active = all(
            [google_connected, folder_valid, provider_ready, queue_online, worker_online]
        )
        checklist = [
            {"label": "Google conectado", "done": google_connected},
            {"label": "Pasta do Drive válida", "done": folder_valid},
            {"label": "Provider válido", "done": provider_ready},
            {"label": "Fila online", "done": queue_online},
            {"label": "Worker online", "done": worker_online},
            {"label": "Automação ativa", "done": automation_active},
        ]
        provider_label = (
            "Modelo local ativo" if status.local_valid
            else ("Deepgram configurado" if deepgram_configured else "Provider pendente")
        )
        test_cta = (
            ("Testar Deepgram", "/settings/deepgram") if status.deepgram_required
            else (("Ver documentação", status.doc_url) if status.doc_url else None)
        )
        steps = [
            {"n": 1, "title": "Login / admin", "done": True,
             "desc": f"Autenticado como {user.email} ({user.role}).", "cta": None},
            {"n": 2, "title": "Conectar Google", "done": google_connected,
             "desc": "Autorize o acesso ao Google Drive para ler as gravações do Meet.",
             "cta": None if google_connected else ("Conectar Google", "/connect-google")},
            {"n": 3, "title": "Escolher pasta do Drive", "done": folder_valid,
             "desc": "Defina a pasta de origem onde o Meet salva as gravações.",
             "cta": None if folder_valid else ("Configurar pasta", "/settings/drive")},
            {"n": 4, "title": "Escolher provider / modelo", "done": provider_ready,
             "desc": status.message,
             "cta": None if provider_ready else ("Configurar Deepgram", "/settings/deepgram")},
            {"n": 5, "title": "Testar provider", "done": provider_ready,
             "desc": f"{provider_label}. Valide a chave Deepgram ou o modelo local.",
             "cta": test_cta},
            {"n": 6, "title": "Ativar automação", "done": automation_active,
             "desc": "Com tudo acima verde, jobs enfileirados são processados automaticamente pelo worker.",
             "cta": None},
            {"n": 7, "title": "Rodar teste final", "done": False,
             "desc": "Rode uma transcrição de teste para confirmar o fluxo ponta a ponta.",
             "cta": ("Ir para Jobs", "/jobs")},
        ]
        return templates.TemplateResponse(request, "onboarding.html", {
            "user": user,
            "steps": steps,
            "checklist": checklist,
            "all_ready": automation_active,
            "transcription_status": status,
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

    @app.get("/models", response_class=HTMLResponse)
    def models_page(request: Request, user=Depends(require_user), provider: str | None = None):
        return templates.TemplateResponse(request, "models.html", {
            "user": user,
            "model_settings": _model_settings_for(user.id),
            "providers": _providers_view(user.id),
            "preselect": provider,
            "message": _pop_flash(request),
        })

    @app.post("/models/provider")
    def save_provider(request: Request, user=Depends(require_user),
                      provider: str = Form(...), model: str = Form("")):
        updated = with_primary(_model_settings_for(user.id), provider, model or None)
        if model_settings_repo is not None:
            model_settings_repo.save_for_user(user.id, updated)
        _set_flash(request, f"Provedor salvo: {updated.primary_provider} / {updated.primary_model}")
        return RedirectResponse("/models", status_code=303)

    @app.post("/models/credentials")
    def save_credentials(request: Request, user=Depends(require_user),
                         provider: str = Form(...), api_key: str = Form(...)):
        key = api_key.strip()
        if not is_cloud_provider(provider):
            _set_flash(request, "Provedor inválido.")
        elif not key:
            _set_flash(request, "API key não pode ser vazia.")
        elif provider_key_store is None:
            _set_flash(request, "Armazenamento de credenciais indisponível.")
        else:
            provider_key_store.save(user.id, provider, key)
            _set_flash(request, f"API key salva para {provider}.")
        return RedirectResponse(f"/models?provider={provider}", status_code=303)

    @app.post("/models/test")
    def test_provider(request: Request, user=Depends(require_user), provider: str = Form(...)):
        key = provider_key_store.get(user.id, provider) if provider_key_store else None
        if not key:
            _set_flash(request, "Configure a API key deste provedor primeiro.")
        else:
            result = verify_provider_key(provider, key)
            _set_flash(request, PROVIDER_TEST_MESSAGES.get(result, "Não foi possível verificar agora."))
        return RedirectResponse(f"/models?provider={provider}", status_code=303)

    @app.post("/models/fallback")
    def save_fallback(request: Request, user=Depends(require_user),
                      fallback_enabled: bool = Form(False),
                      fallback_provider: str = Form(""),
                      fallback_model: str = Form("")):
        updated = with_fallback(
            _model_settings_for(user.id),
            enabled=bool(fallback_enabled),
            provider=fallback_provider or None,
            model=fallback_model or None,
        )
        if model_settings_repo is not None:
            model_settings_repo.save_for_user(user.id, updated)
        if updated.fallback_enabled:
            _set_flash(request, f"Fallback ativo: {updated.fallback_provider} / {updated.fallback_model}")
        else:
            _set_flash(request, "Fallback desativado.")
        return RedirectResponse("/models", status_code=303)

    # Backward-compatible aliases: the old Deepgram tab now lives under Models.
    @app.get("/settings/deepgram")
    def deepgram_alias(request: Request, user=Depends(require_user)):
        return RedirectResponse("/models?provider=deepgram", status_code=303)

    @app.post("/settings/deepgram")
    def save_deepgram_alias(request: Request, user=Depends(require_user),
                            deepgram_api_key: str = Form(...)):
        key = deepgram_api_key.strip()
        if not key:
            _set_flash(request, "Deepgram API Key não pode ser vazia.")
        elif provider_key_store is None:
            _set_flash(request, "Armazenamento de credenciais indisponível.")
        else:
            provider_key_store.save(user.id, "deepgram", key)
            _set_flash(request, "Deepgram API Key salva.")
        return RedirectResponse("/models?provider=deepgram", status_code=303)

    @app.post("/settings/deepgram/test")
    def test_deepgram_alias(request: Request, user=Depends(require_user)):
        key = provider_key_store.get(user.id, "deepgram") if provider_key_store else None
        if not key:
            _set_flash(request, "Configure sua Deepgram API Key antes de iniciar uma transcrição.")
        else:
            result = verify_provider_key("deepgram", key)
            _set_flash(request, PROVIDER_TEST_MESSAGES.get(result, "Não foi possível verificar agora."))
        return RedirectResponse("/models?provider=deepgram", status_code=303)

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
            try:
                worker_repos.automation.mark_poll_result(
                    user.id, _utc_now(), success=not result.error_code,
                    error_code=result.error_code, error_message=result.error_message,
                )
            except Exception:  # noqa: BLE001 - bookkeeping must never 500 the page.
                logging.exception("Could not record poll result for user_id=%s", user.id)
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

    @app.post("/api/recordings/upload")
    async def upload_recording(
        request: Request,
        file: UploadFile = File(...),
        meeting_url: str | None = Form(None),
        meeting_title: str | None = Form(None),
        started_at: str | None = Form(None),
        ended_at: str | None = Form(None),
        duration_seconds: float | None = Form(None),
        source: str = Form("chrome-extension"),
    ):
        """Accept a Chrome-extension recording, persist it, and enqueue a job.

        Token-authenticated (NOT a logged-in session). This request only validates,
        streams the media to the shared recordings dir, and creates a pending job —
        it NEVER downloads, transcribes, or uploads. The worker owns all of that.
        """
        configured = web_settings.extension_upload_token
        if not configured:
            raise HTTPException(status_code=503, detail="Upload da extensão desativado.")
        provided = _extract_upload_token(request)
        # constant-time compare; the token is never logged or echoed in any error.
        if not provided or not secrets.compare_digest(provided, configured):
            raise HTTPException(status_code=401, detail="Token de upload inválido.")

        target_email = (
            web_settings.extension_upload_user_email or web_settings.admin_username
        )
        owner = repos.users.get_by_email(target_email)
        if owner is None or not owner.is_active:
            raise HTTPException(status_code=503, detail="Conta de upload indisponível.")

        worker_repos, error = _resolve_worker_repositories()
        if worker_repos is None:
            raise HTTPException(status_code=503, detail=error)

        recording_id = new_recording_id()
        dest = recording_path(
            web_settings.recordings_dir, recording_id, _recording_suffix(file)
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        max_bytes = web_settings.extension_upload_max_mb * 1024 * 1024
        written = 0
        try:
            with dest.open("wb") as out:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        raise HTTPException(
                            status_code=413,
                            detail=(
                                "Gravação excede o limite de "
                                f"{web_settings.extension_upload_max_mb} MB."
                            ),
                        )
                    out.write(chunk)
        except HTTPException:
            dest.unlink(missing_ok=True)
            raise
        finally:
            await file.close()
        if written == 0:
            dest.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="Gravação vazia.")

        write_metadata(web_settings.recordings_dir, RecordingMetadata(
            recording_id=recording_id, filename=dest.name,
            source=(source or "chrome-extension").strip() or "chrome-extension",
            meeting_url=_clean(meeting_url), meeting_title=_clean(meeting_title),
            started_at=_clean(started_at), ended_at=_clean(ended_at),
            duration_seconds=duration_seconds, content_type=file.content_type,
        ))

        job = worker_repos.jobs.create_job(
            user_id=owner.id,
            source_file_id=source_file_id_for(recording_id),
            source_file_name=_clean(meeting_title) or "Gravação do Meet",
            now=datetime.now(timezone.utc),
        )
        logging.getLogger(__name__).info(
            "Recording upload accepted: job_id=%s user_id=%s bytes=%s",
            job.id, owner.id, written,
        )
        if app.state.queue is not None:
            try:
                app.state.queue.enqueue(job.id)
            except Exception:  # noqa: BLE001 - stays pending; the worker reconciles.
                logging.getLogger(__name__).exception(
                    "Could not enqueue uploaded job_id=%s; it stays pending", job.id
                )
        return JSONResponse(
            {"job_id": job.id, "status": "pending", "recording_id": recording_id},
            status_code=201,
        )

    @app.get("/jobs/{job_id}/download")
    def download_transcript(
        job_id: int, request: Request, user=Depends(require_user), format: str = "txt"
    ):
        worker_repos, error = _resolve_worker_repositories()
        if worker_repos is None:
            raise HTTPException(status_code=503, detail=error)
        fmt = (format or "txt").strip().lower()
        # Strict per-user ownership: even an admin gets 404 for another user's job,
        # so existence of other users' jobs never leaks (requirement 8d).
        try:
            export = get_transcript_export(worker_repos, job_id, user.id, fmt, is_admin=False)
        except DownloadError as exc:
            status = {
                "not_found": 404,
                "not_completed": 409,
                "no_transcript": 404,
                "bad_format": 400,
            }.get(exc.code, 400)
            raise HTTPException(status_code=status, detail=str(exc))
        return Response(
            export.content,
            media_type=export.media_type,
            headers={"Content-Disposition": f'attachment; filename="{export.filename}"'},
        )

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_detail_page(request: Request, job_id: int, user=Depends(require_user)):
        worker_repos, _ = _resolve_worker_repositories()
        job = worker_repos.jobs.get_job(job_id) if worker_repos else None
        # Owner-scoped: unknown ids and other users' jobs both render a 404,
        # so the existence of another user's job never leaks.
        if job is None or job.user_id != user.id:
            return templates.TemplateResponse(
                request, "error.html",
                {
                    "user": user,
                    "code": "job_not_found",
                    "message": "Job not found.",
                    "action": "Verifique o link ou volte à lista de jobs.",
                    "retry_url": "/jobs",
                },
                status_code=404,
            )
        return templates.TemplateResponse(request, "job_detail.html", {
            "user": user, "job": job, "export_formats": available_formats(),
        })

    @app.get("/search", response_class=HTMLResponse)
    def search(request: Request, user=Depends(require_user), q: str = ""):
        query = (q or "").strip()
        worker_repos, error = _resolve_worker_repositories()
        results = []
        if query and worker_repos is not None:
            try:
                found = worker_repos.transcripts.search_transcripts(user.id, query, limit=25)
            except Exception:  # noqa: BLE001 - search must never 500 the page.
                found = []
                error = error or "Busca indisponível no momento."
            results = [
                {
                    "job_id": t.job_id,
                    "snippet": helpers.search_snippet(t.text, query),
                    "created_at": t.created_at,
                }
                for t in found
            ]
        return templates.TemplateResponse(request, "search.html", {
            "user": user, "query": query, "results": results, "backend_error": error,
        })

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


def _size_note(spec) -> str:
    """Human size hint for the Models tab, e.g. 'Limite: inline ~70 MB, até ~99 MB'."""
    parts = []
    if spec.max_inline_bytes:
        parts.append(f"inline ~{spec.max_inline_bytes // (1024 * 1024)} MB")
    if spec.max_file_bytes:
        parts.append(f"até ~{spec.max_file_bytes // (1024 * 1024)} MB")
    return "Limite: " + ", ".join(parts) if parts else ""


class _UploadSizeLimitMiddleware:
    """Pure-ASGI guard: 413 an oversized upload by Content-Length before the body
    is read. Scoped to one POST path; everything else passes straight through.

    A small margin above the limit absorbs multipart boundaries + metadata fields
    so a recording exactly at the limit is never falsely rejected; the handler's
    streaming check remains the authoritative cap on the stored bytes.
    """

    _MARGIN = 1024 * 1024  # 1 MiB for multipart overhead.

    def __init__(self, app, *, path: str, max_bytes) -> None:
        self.app = app
        self._path = path
        self._max_bytes = max_bytes

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") == "http" and scope.get("method") == "POST" and scope.get("path") == self._path:
            limit = self._max_bytes()
            content_length = _header_value(scope, b"content-length")
            if limit and content_length and content_length.isdigit():
                if int(content_length) > limit + self._MARGIN:
                    mb = limit // (1024 * 1024)
                    response = JSONResponse(
                        {"detail": f"Gravação excede o limite de {mb} MB."},
                        status_code=413,
                    )
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)


def _header_value(scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return value.decode("latin-1")
    return None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _set_flash(request: Request, message: str) -> None:
    request.session["flash"] = message


def _pop_flash(request: Request) -> str | None:
    return request.session.pop("flash", None)


def _extract_upload_token(request: Request) -> str | None:
    """Read the extension upload token from ``Authorization: Bearer`` or
    ``X-Upload-Token``. The token is never logged."""
    auth = request.headers.get("Authorization", "")
    if auth[:7].lower() == "bearer ":
        token = auth[7:].strip()
        if token:
            return token
    header = request.headers.get("X-Upload-Token", "").strip()
    return header or None


# Container/audio formats the extension may send, mapped to a stored suffix.
_RECORDING_EXTS = (".webm", ".ogg", ".opus", ".m4a", ".mp4", ".wav", ".mp3")


def _recording_suffix(file: UploadFile) -> str:
    name = (file.filename or "").lower()
    for ext in _RECORDING_EXTS:
        if name.endswith(ext):
            return ext
    ctype = (file.content_type or "").lower()
    if "ogg" in ctype:
        return ".ogg"
    if "m4a" in ctype or "mp4" in ctype:
        return ".m4a"
    if "wav" in ctype:
        return ".wav"
    if "mpeg" in ctype:
        return ".mp3"
    return ".webm"  # the extension records WebM/Opus by default


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


# Status code -> (code, friendly message, suggested action, retry url). Used by the
# friendly-error UI. Deliberately ignores the raw `detail` so no internal string can
# leak into the page.
_HTTP_ERROR_CATALOG = {
    400: ("bad_request", "Requisição inválida.", "Revise os dados e tente novamente.", None),
    401: ("unauthorized", "Você precisa entrar para acessar isto.", "Faça login e tente novamente.", "/login"),
    403: ("forbidden", "Você não tem permissão para acessar isto.", "Entre com uma conta autorizada.", None),
    404: ("not_found", "Página ou recurso não encontrado.", "Verifique o endereço ou volte ao início.", None),
    409: ("conflict", "Este recurso ainda não está pronto.", "Aguarde o processamento e tente novamente.", None),
    503: ("unavailable", "Serviço temporariamente indisponível.", "Tente novamente em instantes.", None),
}


def _http_error_context(status_code: int, detail) -> dict:
    """Friendly, secret-free error-panel context for an HTTP status."""
    code, message, action, retry_url = _HTTP_ERROR_CATALOG.get(
        status_code, ("error", "Ocorreu um erro inesperado.", "Tente novamente.", None)
    )
    return {"code": code, "message": message, "action": action, "doc_url": None, "retry_url": retry_url}


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
