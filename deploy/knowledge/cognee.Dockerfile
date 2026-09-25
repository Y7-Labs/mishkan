FROM ghcr.io/astral-sh/uv@sha256:28df4bbd896cf66a224f2e0cb22240a9a2b9803a3a13519bcadf2e9fdd68c632

RUN apt-get update \
    && apt-get install --yes --no-install-recommends curl libmagic1 \
    && rm -rf /var/lib/apt/lists/* \
    && uv pip install --system "cognee[api,ollama]==1.6.1" "gunicorn==23.0.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TELEMETRY_DISABLED=1 \
    COGNEE_LOG_SEARCH_HISTORY=false \
    COGNEE_LOG_FILE=false

EXPOSE 8000
CMD ["gunicorn", "-w", "1", "-k", "uvicorn.workers.UvicornWorker", "-t", "30000", "--bind=0.0.0.0:8000", "--access-logfile=-", "--error-logfile=-", "cognee.api.client:app"]
