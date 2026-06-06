"""Meeting summaries — pluggable provider scaffold (no LLM call this release).

This package defines the *contract* for turning a stored transcript into a short
summary, plus configuration and status, mirroring how transcription providers are
structured (``app/transcription/``). It deliberately ships **no real LLM
integration**: the default provider is :class:`NullSummaryProvider`, summaries are
**off by default**, and nothing here calls an external API or downloads a model.

A future branch will add a concrete provider (e.g. an Anthropic Claude summary
provider) behind this same interface, gated by ``SUMMARY_ENABLED`` and a per-user
key, exactly like Deepgram. See ``documentation/19-roadmap.md`` and the summaries
section of the docs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Protocol, runtime_checkable

from app.errors import AppError


class SummaryUnavailableError(AppError):
    """Raised when a summary is requested but no provider is configured."""

    default_user_message = (
        "Resumos ainda não estão disponíveis nesta instalação."
    )
    code = "summary_unavailable"
    retryable = False


@dataclass(frozen=True)
class Summary:
    """A generated meeting summary. ``provider``/``model`` describe its origin."""

    text: str
    provider: str
    model: str | None = None


@runtime_checkable
class SummaryProvider(Protocol):
    def summarize(self, transcript_text: str, *, language: str | None = None) -> Summary: ...


@dataclass(frozen=True)
class SummarySettings:
    enabled: bool
    provider: str
    model: str | None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "SummarySettings":
        values = env if env is not None else os.environ
        raw = (values.get("SUMMARY_ENABLED", "false") or "false").strip().lower()
        return cls(
            enabled=raw in ("1", "true", "yes", "y", "on"),
            provider=(values.get("SUMMARY_PROVIDER", "") or "").strip() or "none",
            model=(values.get("SUMMARY_MODEL", "") or "").strip() or None,
        )


@dataclass(frozen=True)
class SummaryStatus:
    enabled: bool
    provider: str
    message: str


class NullSummaryProvider:
    """The only provider this release ships: it never produces a summary.

    Calling :meth:`summarize` raises :class:`SummaryUnavailableError` so the UI can
    show a friendly "coming soon" message instead of a stack trace.
    """

    name = "none"

    def summarize(self, transcript_text: str, *, language: str | None = None) -> Summary:
        raise SummaryUnavailableError("No summary provider is configured")


def get_summary_status(settings: SummarySettings | None = None) -> SummaryStatus:
    settings = settings or SummarySettings.from_env()
    if not settings.enabled or settings.provider == "none":
        return SummaryStatus(
            enabled=False,
            provider="none",
            message="Resumos automáticos: planejados (roadmap), ainda não ativados.",
        )
    # A concrete provider will be wired here in a future branch.
    return SummaryStatus(
        enabled=False,
        provider=settings.provider,
        message=(
            f"Provider de resumo '{settings.provider}' configurado, mas a integração "
            "ainda não está disponível nesta versão."
        ),
    )


def build_summary_provider(settings: SummarySettings | None = None) -> SummaryProvider:
    """Return the configured summary provider. Always the null provider for now."""
    return NullSummaryProvider()


__all__ = [
    "Summary",
    "SummaryProvider",
    "SummarySettings",
    "SummaryStatus",
    "NullSummaryProvider",
    "SummaryUnavailableError",
    "get_summary_status",
    "build_summary_provider",
]
