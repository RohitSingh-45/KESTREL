# Multi-stage production image: build UI, then serve API + static assets.

FROM node:22-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# Same-origin production: empty API base (browser calls /api on this host).
ENV VITE_API_BASE_URL=
RUN npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    LOG_LEVEL=INFO \
    PORT=8000 \
    HOST=0.0.0.0 \
    DATABASE_URL=sqlite+aiosqlite:////data/kestrel.db \
    AGENT_CYCLE_SECONDS=600 \
    GENERATION_LEASE_SECONDS=300 \
    CORS_ORIGINS= \
    RUN_MIGRATIONS_ON_STARTUP=false

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
COPY scripts ./scripts
COPY --from=frontend /frontend/dist ./frontend/dist

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir . \
    && mkdir -p /data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["python", "scripts/start_production.py"]
