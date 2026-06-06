"""Single source of truth for per-user provider readiness.

Used by dashboard, onboarding, jobs, and run-once to answer "is this user
ready to transcribe?" without hardcoding Deepgram.

The Models tab is the authority: a user's primary provider + model + encrypted
key.  Local engines are driven by the environment, not per-user keys.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.transcription.provider_config import ModelSettings, default_model_settings
from app.transcription.provider_models import (
    ProviderSpec,
    get_provider_spec,
    is_cloud_provider,
)


@dataclass(frozen=True)
class ProviderReadiness:
    """Per-user provider posture for UI rendering."""

    ok: bool
    provider_id: str | None
    provider_label: str | None
    model: str | None
    kind: str  # "cloud" | "local" | "none"
    status_label: str
    reason: str | None
    action_label: str | None
    action_href: str | None
    has_credential: bool
    credential_required: bool


def compute_provider_readiness(
    model_settings: ModelSettings | None,
    *,
    has_key: callable | None = None,
) -> ProviderReadiness:
    """Determine whether the user's primary provider is ready to transcribe.

    ``has_key(provider_id) -> bool`` checks whether an encrypted API key exists
    for the given provider. Pass ``None`` when no credential store is available
    (e.g. worker context) — keys always appear absent.
    """
    ms = model_settings or default_model_settings()
    provider_id = ms.primary_provider or ""
    spec: ProviderSpec | None = get_provider_spec(provider_id)
    credential_required = bool(spec and spec.requires_api_key)
    has_credential = _check_credential(provider_id, has_key) if spec else False

    if not spec:
        return ProviderReadiness(
            ok=False,
            provider_id=provider_id or None,
            provider_label=None,
            model=None,
            kind="none",
            status_label="Provedor inválido",
            reason=f"Provedor '{provider_id}' não encontrado no registro.",
            action_label="Configurar Modelos",
            action_href="/models",
            has_credential=False,
            credential_required=False,
        )

    model = ms.primary_model or spec.default_model
    if model and not spec.has_model(model):
        return ProviderReadiness(
            ok=False,
            provider_id=provider_id,
            provider_label=spec.label,
            model=model,
            kind="cloud" if is_cloud_provider(provider_id) else "local",
            status_label="Modelo inválido",
            reason=f"Modelo '{model}' não pertence ao provedor {spec.label}.",
            action_label="Ajustar em Modelos",
            action_href="/models",
            has_credential=False,
            credential_required=credential_required,
        )

    if credential_required and not has_credential:
        return ProviderReadiness(
            ok=False,
            provider_id=provider_id,
            provider_label=spec.label,
            model=model,
            kind="cloud",
            status_label="Chave ausente",
            reason=f"A API key de {spec.label} não foi configurada.",
            action_label=f"Configurar chave",
            action_href=f"/models?provider={provider_id}",
            has_credential=False,
            credential_required=True,
        )

    kind = "cloud" if is_cloud_provider(provider_id) else "local"
    return ProviderReadiness(
        ok=True,
        provider_id=provider_id,
        provider_label=spec.label,
        model=model,
        kind=kind,
        status_label="Configurado",
        reason=None,
        action_label=None,
        action_href=None,
        has_credential=has_credential,
        credential_required=credential_required,
    )


def provider_status_text(readiness: ProviderReadiness) -> str:
    """One-line summary for dashboard / jobs banner."""
    if not readiness.ok:
        return readiness.reason or readiness.status_label
    if readiness.provider_label and readiness.model:
        return f"{readiness.provider_label}: {readiness.model}"
    return readiness.status_label


def _check_credential(provider_id: str, has_key: callable | None) -> bool:
    if has_key is None:
        return False
    try:
        return bool(has_key(provider_id))
    except Exception:
        return False
