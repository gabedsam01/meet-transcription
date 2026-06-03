FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN mkdir -p /app/data /app/tmp /app/secrets

EXPOSE 8000

# One image serves both roles; docker-compose overrides `command` per service:
#   web    -> uvicorn app.web.main:app --host 0.0.0.0 --port 8000
#   worker -> python -m app.worker.main      (legacy: python -m app.main --watch)
# Default to the web server so a bare `docker run` is useful; override to run a
# worker. This CMD is intentionally easy to override — nothing is baked in.
CMD ["uvicorn", "app.web.main:app", "--host", "0.0.0.0", "--port", "8000"]
