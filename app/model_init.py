"""``model-init`` entrypoint: prepare the configured local transcription model.

Run as ``python -m app.model_init`` (the optional ``model-init`` compose service).
It is the ONLY place that may download a model, and it runs OUTSIDE the web
service (the web layer never downloads). Behavior:

- local transcription disabled -> exit 0 (nothing to prepare);
- model already present, or downloaded when auto-download is on -> exit 0;
- model missing and not downloadable -> exit non-zero with a clear, secret-free
  message.

The worker also validates its config at runtime, so this service is optional: if
running it in Compose is inconvenient, skip it and let the worker surface an
invalid-model message instead (see docs/architecture/local-transcription.md).
"""

from __future__ import annotations

import logging

from app.logger import setup_logging
from app.models.errors import ModelManagerError
from app.models.manager import ensure_model
from app.transcription.config import TranscriptionConfig

LOGGER = logging.getLogger("app.model_init")


def main() -> int:
    setup_logging()
    config = TranscriptionConfig.from_env()
    if not config.enabled:
        LOGGER.info("Local transcription disabled; nothing to prepare.")
        return 0

    LOGGER.info(
        "Preparing local model: engine=%s model=%s auto_download=%s model_dir=%s",
        config.engine, config.model, config.auto_download, config.model_dir,
    )
    try:
        status = ensure_model(config)
    except ModelManagerError as exc:
        # Friendly message + technical detail, never a secret/traceback to stdout.
        LOGGER.error("Model preparation failed: %s (%s)", exc.user_message, exc)
        return 1

    if status.ready:
        LOGGER.info(
            "Model ready: engine=%s model=%s path=%s",
            status.engine, status.model, status.path or "(managed by engine)",
        )
        return 0

    LOGGER.error(
        "Model not ready: %s", status.reason or status.message,
    )
    return 2


if __name__ == "__main__":  # pragma: no cover - exercised via tests calling main()
    raise SystemExit(main())
