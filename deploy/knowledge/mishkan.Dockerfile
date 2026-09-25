FROM ghcr.io/astral-sh/uv@sha256:28df4bbd896cf66a224f2e0cb22240a9a2b9803a3a13519bcadf2e9fdd68c632

RUN apt-get update \
    && apt-get install --yes --no-install-recommends curl socat \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/mishkan
COPY . .
RUN uv sync --frozen --no-dev

ENV PATH="/opt/mishkan/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 18888
CMD ["sh", "-c", "socat TCP-LISTEN:18888,reuseaddr,fork TCP:127.0.0.1:8888 & exec mishkand --config /workspace/.mishkan/config.yaml"]
