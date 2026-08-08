# Kestrel

Autonomous AI Security Persona for the  hackathon project.

Module 1 provides the production-ready application scaffold. Module 2 adds the evaluator-facing Agent API (`POST /api/agent/init`, `GET /api/agent/feed`) with repository/service layers. LLM integration, research, scheduler, and the generation pipeline are intentionally not included yet.

## Stack

- Python 3.12
- FastAPI + Uvicorn
- SQLAlchemy 2.x (async) + SQLite (`aiosqlite`)
- Alembic
- Pydantic v2 + pydantic-settings
- structlog
- pytest + pytest-asyncio + httpx

## Quick start (local)

**You must run the API and the UI together.** Opening only the Vite frontend without uvicorn causes `Request failed (502)` because `/api` has nothing to proxy to.

```bash
# 1. Create and activate a virtual environment
python -m venv .venv

# Windows PowerShell
.\.venv\Scripts\Activate.ps1

# macOS / Linux
# source .venv/bin/activate

# 2. Install dependencies
python -m pip install -e ".[dev]"

# 3. Copy environment template
copy .env.example .env          # Windows
# cp .env.example .env          # macOS / Linux

# 4. Run database migrations (required once per DATABASE_URL)
alembic upgrade head
# Tip: if APP_ENV/DATABASE_URL are set in your shell from an old session,
# unset them or use a fresh .env — a new empty SQLite file has no tables.

# 5. Terminal A — API (required)
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
# Startup auto-applies Alembic when the schema is missing.

# 6. Terminal B — UI (optional; or use same-origin build below)
cd frontend
npm install
npm run dev
# open http://127.0.0.1:5173  (proxies /api → :8000)
```

Same-origin (API serves the built UI — recommended local production-style check):

```bash
cd frontend && npm run build && cd ..
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# open http://127.0.0.1:8000/
```

Health check:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready
```

Expected `/health` response:

```json
{
  "status": "ok",
  "service": "kestrel",
  "db": "ok",
  "scheduler": "running",
  "llm": "unavailable"
}
```

## Configuration

Environment variables (see `.env.example`):

| Variable | Default | Required |
|----------|---------|----------|
| `APP_ENV` | `development` | No |
| `LOG_LEVEL` | `INFO` | No |
| `PORT` | `8000` | No (platforms inject this) |
| `HOST` | `0.0.0.0` | No |
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/kestrel.db` | No |
| `CORS_ORIGINS` | _(empty)_ | No (required for split FE/BE) |
| `AGENT_CYCLE_SECONDS` | `600` | No |
| `OPENAI_API_KEY` | _(unset)_ | Yes for live generation |
| `RESEARCH_API_KEY` | _(unset)_ | No |

LLM / research keys are optional. The app must start successfully when they are absent.

## Database

- Async SQLAlchemy engine with SQLite WAL mode and foreign keys enabled
- Schema managed by Alembic
- SQLite file stored under `./data/` (created automatically)

```bash
alembic upgrade head
alembic current
alembic history
```

## Tests

```bash
pytest
```

## Docker

```bash
docker compose up --build
```

- API: `http://127.0.0.1:8000`
- SQLite persisted via `./data:/data`
- Optional `.env` is loaded when present

## Project layout

```
app/
  main.py            # FastAPI app + lifespan
  config.py          # pydantic-settings
  logging.py         # structlog
  api/routes/health.py
  db/
    base.py
    session.py
    models.py        # agents, posts, memory_items, topics, pipeline_runs, generation_leases
alembic/             # migrations
tests/               # pytest suite
data/                # SQLite volume (gitignored db files)
```

## Module status

| Module | Status |
|--------|--------|
| 1 — Scaffold | Implemented |
| 2 — Agent API (init + feed) | Implemented |
| 3 — Autonomous lifecycle | Implemented |
| 4 — Research + LLM candidates | Implemented |
| 5 — Topic funnel scoring | Implemented |
| 6 — Self-debate engine | Implemented |
| 7 — Persistent memory / belief graph | Implemented |
| 8 — Persona consistency / anti-drift | Implemented |
| 9 — Final post generation + rationale | Implemented |
| 10 — Publishing + durable feed | Implemented |
| 11 — Production hardening / evaluator readiness | Implemented |
| 12 — Source diversity / story continuity / contradictions | Implemented |
| 13 — Adaptive learning from publishing history | Implemented |
| 14 — Post-publish evaluation / self-reflection | Implemented |
| 15 — Controlled autonomous learning | Implemented |
| 16 — Autonomous decision / orchestration | Implemented |
| 17 — Observability / explainability / audit | Implemented |
| 18 — Operator dashboard UI + production web deploy | Implemented |

## Operator dashboard (Module 18)

```bash
# Dev UI (proxies /api to FastAPI on :8000)
cd frontend
npm install
npm run dev

# Or build and serve from FastAPI (same-origin)
cd frontend && npm run build
python scripts/start_production.py
# open http://127.0.0.1:8000/
```

Read-only dashboard APIs:
- `GET /api/dashboard/agents`
- `GET /api/dashboard/state?agentId=...`

These never trigger research, LLM, generation, or publishing.

## Production deployment (recommended)

**Architecture:** Browser → FastAPI (serves `frontend/dist` + `/api`) → SQLite disk or Postgres → Modules 1–17.

**Recommended host:** [Render](https://render.com) Web Service using `render.yaml` (Docker). Keeps API + UI same-origin (no CORS required).

### Production start command

```bash
python scripts/start_production.py
```

Equivalent:

```bash
alembic upgrade head
python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers
```

### Required environment variables

| Variable | Notes |
|----------|--------|
| `PORT` | Injected by Render/Railway (default `8000`) |
| `HOST` | `0.0.0.0` |
| `APP_ENV` | `production` |
| `DATABASE_URL` | SQLite on persistent disk, or `postgresql+asyncpg://...` |
| `OPENAI_API_KEY` | Required for real generation cycles |
| `CORS_ORIGINS` | Only if frontend is on a different origin |
| `SECRET_KEY` | Optional; do not commit secrets |

### Health URLs

- Liveness: `GET /health` → `{"status":"ok", ...}`
- Readiness: `GET /ready` → `{"status":"ok","db":"ok"}` (HTTP 503 if DB down)

### Docker

```bash
docker compose up --build
```

### Optional split deploy (Vercel UI + Render API)

1. Deploy backend on Render with public HTTPS URL.
2. Set `CORS_ORIGINS=https://your-frontend.vercel.app`.
3. Build frontend with `VITE_API_BASE_URL=https://your-api.onrender.com`.
4. Deploy `frontend/` to Vercel (`frontend/vercel.json` included).

### 48-hour uptime note

This session implements **48-hour production readiness**, not a literal 48-hour soak in CI. For real verification: deploy to an always-on plan, hit `/health` every 5 minutes for 48 hours (UptimeRobot / cron), and confirm no restarts.

## Evaluator API (Module 2)

```bash
# Initialize agent
curl.exe -X POST http://127.0.0.1:8000/api/agent/init ^
  -H "Content-Type: application/json" ^
  -d "{\"persona\":{\"name\":\"Kestrel\",\"domain\":\"AI Security\"}}"

# Read feed (pure read; durable posts appear after autonomous publish cycles)
curl.exe "http://127.0.0.1:8000/api/agent/feed?agentId=<agentId>"
```

## Evaluator smoke simulation (Module 10)

With the server running and `OPENAI_API_KEY` configured:

```bash
python scripts/smoke_eval.py --base-url http://127.0.0.1:8000 --polls 18 --interval 10
```

