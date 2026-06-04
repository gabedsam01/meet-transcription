FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# ---- Optional local transcription engines (OFF by default) -----------------
# These engines are heavy and CPU/arch-specific, so they are NOT in the base
# image. Enable them at build time, e.g.:
#   docker build --build-arg INSTALL_FASTER_WHISPER=true -t meet-transcription:fw .
#   docker build --build-arg INSTALL_WHISPER_CPP=true   -t meet-transcription:wc .
# Nothing heavy is ever installed at container startup. See
# docs/architecture/local-transcription.md.
ARG INSTALL_LOCAL_TRANSCRIPTION=false
ARG INSTALL_FASTER_WHISPER=false
ARG INSTALL_WHISPER_CPP=false

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# faster-whisper (CPU via CTranslate2; PyAV decodes media). Installed only when
# requested so the default image stays small.
RUN if [ "$INSTALL_FASTER_WHISPER" = "true" ] || [ "$INSTALL_LOCAL_TRANSCRIPTION" = "true" ]; then \
        pip install --no-cache-dir "faster-whisper>=1.0,<2"; \
    fi

# whisper.cpp needs ffmpeg to extract 16 kHz mono WAV. ffmpeg is a multiarch apt
# package (x86_64 + arm64). The whisper-cli binary itself is provided externally
# via WHISPER_CPP_BINARY (mounted, or baked into a derived image) — compiling
# whisper.cpp here is out of scope for this image. Installed only when requested.
RUN if [ "$INSTALL_WHISPER_CPP" = "true" ] || [ "$INSTALL_LOCAL_TRANSCRIPTION" = "true" ]; then \
        apt-get update \
        && apt-get install -y --no-install-recommends ffmpeg \
        && rm -rf /var/lib/apt/lists/*; \
    fi

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

RUN mkdir -p /app/data /app/tmp /app/secrets /models

EXPOSE 8000

# One image serves both roles; docker-compose overrides `command` per service:
#   web    -> uvicorn app.web.main:app --host 0.0.0.0 --port 8000
#   worker -> python -m app.worker.main      (legacy: python -m app.main --watch)
# Default to the web server so a bare `docker run` is useful; override to run a
# worker. This CMD is intentionally easy to override — nothing is baked in.
CMD ["uvicorn", "app.web.main:app", "--host", "0.0.0.0", "--port", "8000"]
