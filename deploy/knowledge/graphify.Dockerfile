FROM ghcr.io/astral-sh/uv@sha256:28df4bbd896cf66a224f2e0cb22240a9a2b9803a3a13519bcadf2e9fdd68c632

RUN uv pip install --system "graphifyy[mcp]==0.9.67"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 7778
ENTRYPOINT ["graphify-mcp"]
