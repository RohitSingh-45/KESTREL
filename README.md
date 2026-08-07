# Kestrel

Autonomous AI Security Persona for the HANUMAN hackathon project.

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

```bash
# 1. Create and activate a virtual environment
python -m venv .venv

# Windows PowerShell
.\.venv\Scripts\Activate.ps1

# macOS / Linux
# source .venv/bin/activate

# 2. Install dependencies
pip install -e ".[dev]"

# 3. Copy environment template
copy .env.example .env          # Windows
# cp .env.example .env          # macOS / Linux

# 4. Run database migrations
alembic upgrade head

# 5. Start the API server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

Expected response:

```json
{
  "status": "ok",
  "db": "ok",
  "scheduler": "not_started"
}
```

## Configuration

Environment variables (see `.env.example`):

| Variable | Default | Required |
|----------|---------|----------|
| `APP_ENV` | `development` | No |
| `LOG_LEVEL` | `INFO` | No |
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/kestrel.db` | No |
| `AGENT_CYCLE_SECONDS` | `600` | No |
| `OPENAI_API_KEY` | _(unset)_ | No (Module 1) |
| `RESEARCH_API_KEY` | _(unset)_ | No (Module 1) |

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

