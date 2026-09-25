FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7

ARG MEM0_COMMIT=47a69e1e72dc562b6fdd49a9ef892229afc7508a

RUN apt-get update \
    && apt-get install --yes --no-install-recommends curl git libpq5 patch \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt
RUN git clone --filter=blob:none https://github.com/mem0ai/mem0.git \
    && cd mem0 \
    && git checkout --detach "${MEM0_COMMIT}"

COPY deploy/knowledge/mem0-ollama.patch /tmp/mem0-ollama.patch
RUN cd /opt/mem0 \
    && git apply --check /tmp/mem0-ollama.patch \
    && git apply /tmp/mem0-ollama.patch \
    && pip install --no-cache-dir -r server/requirements.txt

WORKDIR /opt/mem0/server
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MEM0_TELEMETRY=false

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn main:app --host 0.0.0.0 --port 8000"]
