"""Provider registry: the catalogue of transcription providers and their models.

This is the single source of truth for which providers exist, which models each
exposes, whether an API key is required, the diarization story, and the
file-size limits the worker enforces before calling out. The Models tab renders
straight from this registry, and the resolver validates user choices against it.

Nothing here performs I/O — it is pure data, safe to import anywhere (UI, worker,
tests) without pulling heavy SDKs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.errors import PROVIDER_DOCS_URL

# Provider ids — stable strings persisted in ``user_model_settings`` and
# ``provider_credentials.provider``. Never rename without a migration.
DEEPGRAM = "deepgram"
OPENROUTER = "openrouter"
GEMINI = "gemini"
LOCAL = "local"

# Diarization "kind" classifies how trustworthy speaker labels are, so the UI can
# warn honestly. The free-text ``diarization`` field is the sentence we render.
DIARIZATION_REAL = "real"
DIARIZATION_PSEUDO = "pseudo"
DIARIZATION_MODEL_DEPENDENT = "model-dependent"
DIARIZATION_NONE = "none"

# Gemini request-size ceilings. Inline base64 inflates the payload ~33%, so we
# cap raw bytes well below the hard request limit; the Files API allows more.
# Above the Files ceiling we refuse with a friendly error (chunking is a separate
# branch). Binary MiB so the boundary is unambiguous in tests.
GEMINI_INLINE_MAX_BYTES = 70 * 1024 * 1024
GEMINI_FILES_MAX_BYTES = 99 * 1024 * 1024

# OpenRouter multipart upload ceiling (defensive; the API itself returns 413 if
# exceeded — we surface a friendlier message before paying the upload).
OPENROUTER_MAX_BYTES = 100 * 1024 * 1024


@dataclass(frozen=True)
class ProviderSpec:
    """Everything the UI and resolver need to know about one provider."""

    provider_id: str
    label: str
    models: tuple[str, ...]
    default_model: str
    requires_api_key: bool
    diarization_kind: str
    diarization: str  # human sentence rendered in the UI
    notes: str = ""
    docs_url: str = PROVIDER_DOCS_URL
    max_inline_bytes: int | None = None
    max_file_bytes: int | None = None
    # Free-form, provider-agnostic capability flags for future use.
    capabilities: tuple[str, ...] = field(default_factory=tuple)

    def has_model(self, model: str) -> bool:
        return model in self.models


_DEEPGRAM_SPEC = ProviderSpec(
    provider_id=DEEPGRAM,
    label="Deepgram",
    models=("nova-3", "nova-2", "whisper"),
    default_model="nova-3",
    requires_api_key=True,
    diarization_kind=DIARIZATION_REAL,
    diarization="Diarização real (separação de locutores) disponível.",
    notes="Provedor de fala dedicado; melhor qualidade de diarização.",
    docs_url=PROVIDER_DOCS_URL,
    max_file_bytes=2 * 1024 * 1024 * 1024,
    capabilities=("diarization", "timestamps", "utterances"),
)

_OPENROUTER_SPEC = ProviderSpec(
    provider_id=OPENROUTER,
    label="OpenRouter",
    models=(
        "microsoft/mai-transcribe-1.5",
        "nvidia/parakeet-tdt-0.6b-v3",
        "mistralai/voxtral-mini-transcribe",
        "qwen/qwen3-asr-flash-2026-02-10",
        "openai/whisper-large-v3-turbo",
        "openai/whisper-large-v3",
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    ),
    default_model="openai/whisper-large-v3-turbo",
    requires_api_key=True,
    diarization_kind=DIARIZATION_MODEL_DEPENDENT,
    diarization="Diarização depende do modelo; em geral indisponível.",
    notes="Roteador de modelos: a diarização e os timestamps variam por modelo.",
    docs_url=PROVIDER_DOCS_URL,
    max_file_bytes=OPENROUTER_MAX_BYTES,
    capabilities=("timestamps",),
)

_GEMINI_SPEC = ProviderSpec(
    provider_id=GEMINI,
    label="Google Gemini",
    models=(
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-3-flash-preview",
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash",
    ),
    default_model="gemini-2.5-flash",
    requires_api_key=True,
    diarization_kind=DIARIZATION_PSEUDO,
    diarization="Pseudo-diarização via prompt; não garantida.",
    notes=(
        "Modelo multimodal: transcreve áudio e pode tentar identificar locutores "
        "por prompt, mas isso não é diarização real."
    ),
    docs_url=PROVIDER_DOCS_URL,
    max_inline_bytes=GEMINI_INLINE_MAX_BYTES,
    max_file_bytes=GEMINI_FILES_MAX_BYTES,
    capabilities=("multimodal",),
)

_LOCAL_SPEC = ProviderSpec(
    provider_id=LOCAL,
    label="Local (CPU)",
    models=("tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"),
    default_model="small",
    requires_api_key=False,
    diarization_kind=DIARIZATION_NONE,
    diarization="Sem diarização no MVP (a menos que um módulo opcional seja ativado).",
    notes="Motor local CPU (faster-whisper / whisper.cpp), configurado por ambiente.",
    capabilities=("offline",),
)

# Registry. ``LOCAL`` is included for the UI/docs but its lifecycle is driven by
# the environment (LOCAL_TRANSCRIPTION_*), not by per-user credentials.
PROVIDERS: dict[str, ProviderSpec] = {
    DEEPGRAM: _DEEPGRAM_SPEC,
    OPENROUTER: _OPENROUTER_SPEC,
    GEMINI: _GEMINI_SPEC,
    LOCAL: _LOCAL_SPEC,
}

# Cloud providers a user can select + supply a key for (excludes env-driven local).
CLOUD_PROVIDERS: tuple[str, ...] = (DEEPGRAM, OPENROUTER, GEMINI)

# Providers selectable as a primary/fallback in the Models tab.
SELECTABLE_PROVIDERS: tuple[str, ...] = CLOUD_PROVIDERS


def provider_ids() -> tuple[str, ...]:
    return tuple(PROVIDERS.keys())


def get_provider_spec(provider_id: str | None) -> ProviderSpec | None:
    if not provider_id:
        return None
    return PROVIDERS.get(provider_id)


def is_valid_provider(provider_id: str | None) -> bool:
    return bool(provider_id) and provider_id in PROVIDERS


def is_cloud_provider(provider_id: str | None) -> bool:
    return bool(provider_id) and provider_id in CLOUD_PROVIDERS


def requires_api_key(provider_id: str | None) -> bool:
    spec = get_provider_spec(provider_id)
    return bool(spec and spec.requires_api_key)


def is_valid_model(provider_id: str | None, model: str | None) -> bool:
    spec = get_provider_spec(provider_id)
    return bool(spec and model and spec.has_model(model))


def default_model(provider_id: str | None) -> str | None:
    spec = get_provider_spec(provider_id)
    return spec.default_model if spec else None


def models_for(provider_id: str | None) -> tuple[str, ...]:
    spec = get_provider_spec(provider_id)
    return spec.models if spec else ()
