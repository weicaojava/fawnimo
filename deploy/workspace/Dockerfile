# Build one same-origin frontend and API gateway; no upstream image is published.
FROM node:22-bookworm-slim AS frontend
WORKDIR /build/front
RUN corepack enable && corepack prepare pnpm@9.15.9 --activate
COPY front/package.json front/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY front/ ./
ENV VITE_BACKEND_URL=""
RUN pnpm run build \
    && printf 'window.__ENV = { BACKEND_URL: "" };\n' > dist/env.js

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/backend/.venv/bin:$PATH"
WORKDIR /app/backend
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
# langchain-openai currently lives in the locked optional dev extra and is
# needed by the text runtime. Keep that extra until runtime dependencies move.
RUN uv sync --frozen --no-dev --extra dev --no-install-project
COPY backend/ ./
RUN uv sync --locked --no-dev --extra dev \
    && groupadd --gid 10001 studio \
    && useradd --uid 10001 --gid studio --no-create-home studio \
    && mkdir -p /data \
    && chown studio:studio /data
COPY --from=frontend /build/front/dist /app/front/dist
COPY LICENSE /app/LICENSE
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).close()"]
CMD ["python", "run_controlplane.py", "--root", "/data", "--host", "0.0.0.0", "--port", "8000", "--frontend", "/app/front/dist", "--probe", "/usr/bin/ffmpeg"]
