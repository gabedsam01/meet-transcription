"""Map a resolved transcription provider's identity to a concurrency *kind*.

Concurrency control is provider-aware: local CPU engines must serialize
(``kind == LOCAL``) because they saturate the CPU-only VPS, while network
providers (Deepgram and other cloud SaaS) are I/O-bound and may run in parallel
(``kind == CLOUD``). The decision is by the *resolved* provider — a user who has
a valid local engine but chose Deepgram is a CLOUD job, not a LOCAL one.
"""

from __future__ import annotations

CLOUD = "cloud"
LOCAL = "local"

# Network/SaaS providers — parallelism is cheap on CPU (mostly upload + wait).
CLOUD_PROVIDERS = frozenset({"deepgram", "gemini", "openrouter"})
# CPU-bound local engines — must run one at a time.
LOCAL_PROVIDERS = frozenset({"faster-whisper", "whisper-cpp"})


def classify_provider_kind(name: str | None) -> str:
    """Return ``LOCAL`` for known CPU engines, else ``CLOUD``.

    Unknown/blank names default to ``CLOUD``: the safe side to overcommit (a
    genuinely broken job still fails terminally downstream), and it never
    accidentally serializes a cloud provider behind the single local lock.
    """
    key = (name or "").strip().lower()
    if key in LOCAL_PROVIDERS:
        return LOCAL
    return CLOUD
