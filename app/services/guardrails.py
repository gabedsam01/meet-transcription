"""Cost/quota guardrails enforced at job creation.

Enforced now (data is cheap): per-file size (Drive returns ``size``) and a
per-user daily job count. ``monthly_cloud_minutes_limit`` and
``max_file_duration_minutes`` are scaffolded on the settings table + env and
documented as next steps — they need minute metering / ``videoMediaMetadata``.

Limits resolve per-user (``user_automation_settings`` overrides) falling back to
the global env default; 0/None means unlimited.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Guardrails:
    max_file_size_mb: int | None = None
    daily_jobs_limit: int | None = None

    def allow_file(self, file) -> tuple[bool, str | None]:
        """Per-file admission check. Returns ``(allowed, friendly_reason)``."""
        size = getattr(file, "size", None)
        if self.max_file_size_mb and size is not None:
            if int(size) > self.max_file_size_mb * 1024 * 1024:
                return False, "Arquivo excede o limite permitido."
        return True, None

    def daily_room(self, repositories, user_id: int, now: datetime) -> int | None:
        """How many more jobs the user may create today, or None when unlimited."""
        if not self.daily_jobs_limit:
            return None
        used = repositories.jobs.count_jobs_created_since(user_id, _start_of_day(now))
        return max(0, self.daily_jobs_limit - used)


def resolve_guardrails(
    automation,
    *,
    default_max_file_size_mb: int | None,
    default_daily_jobs_limit: int | None,
) -> Guardrails:
    """Build the effective guardrails: per-user override, else the global default."""
    user_size = getattr(automation, "max_file_size_mb", None) if automation else None
    user_daily = getattr(automation, "daily_jobs_limit", None) if automation else None
    return Guardrails(
        max_file_size_mb=user_size if user_size is not None else default_max_file_size_mb,
        daily_jobs_limit=user_daily if user_daily is not None else default_daily_jobs_limit,
    )


def _start_of_day(now: datetime) -> datetime:
    """Midnight (UTC) of ``now``'s day — the daily-limit counting window."""
    return now.replace(hour=0, minute=0, second=0, microsecond=0)
