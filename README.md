# ChainWatch — Real-Time Crypto Fraud Attribution

A prototype for Indian law-enforcement: ingest a victim-reported wallet, trace funds across Bitcoin / EVM / TRON, identify the nearest receiving exchange (VASP) with a confidence score, score laundering risk, and emit an LEA-ready freeze / information request.

The core algorithm is a **forward BFS** from the reported address. Every counterparty is checked against the attribution store. The walk stops at the first high-confidence VASP deposit. Risk scoring, cross-chain correlation, alerts and reports decorate that result.

## Stack

| Layer | Choice |
| --- | --- |
| API | FastAPI (Python 3.11+) |
| Worker | Celery (optional) or inline jobs |
| DB | Postgres in Docker, SQLite for a laptop demo |
| Cache / queue | Redis (in-memory fallback if Redis is down) |
| UI | React 19 + Vite + Tailwind + Cytoscape.js |
| Data | Hybrid: live free-tier explorers, synthetic fallback |

## Quick start (laptop, no Docker)

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -e ".[dev]"
copy .env.example .env          # or: cp .env.example .env
python -m scripts.seed_all
uvicorn app.main:app --reload --port 8000
```

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173

| Email | Password | Role |
| --- | --- | --- |
| `admin@chainwatch.gov.in` | `ChangeMe#123` | Admin |

The seed script also creates ten scripted fraud cases (investment scam, task fraud, sextortion, ransomware, phishing, darknet, bridge, mixer, peel chain, fan-out). Every case has a known ground-truth VASP so a demo works with no API keys.

## Docker

```bash
docker compose up --build
```

- UI: http://localhost:5173
- API docs: http://localhost:8000/docs
- Postgres `5432`, Redis `6379`

Set `DATA_MODE=synthetic` if you want a guaranteed offline demo. `hybrid` (default) tries live explorers and falls back to the seed.

## Live explorer keys (optional)

Free-tier keys make hybrid mode useful on real addresses:

- [Etherscan V2](https://etherscan.io/apis) — one key covers ETH / BSC / Polygon / Arbitrum / Base
- [TronGrid](https://www.trongrid.io)
- [Blockchair](https://blockchair.com/api) (optional, Bitcoin)

Put them in `backend/.env` or the compose environment.

## Demo script

```bash
cd backend
python -m scripts.demo
python -m scripts.demo --scenario task_fraud_tron
python -m scripts.evaluate_traces
```

`scripts.demo` traces a seeded victim wallet, prints the attribution sentence, and writes a hashed investigation PDF under `backend/var/reports/`.

## What the system does

1. **Intake** — paste addresses (chain auto-detected) or import an NCRP-style CSV. A mock NCRP pusher (`POST /api/v1/integrations/ncrp/mock-push`) simulates live complaints.
2. **Trace** — Celery or inline worker; progress over SSE (`GET /api/v1/trace/{id}/stream`).
3. **Attribution** — four tiers: direct label, deposit-address consolidation, BTC common-input clustering, ML entity classifier.
4. **Risk** — typology motifs (peel chain, fan-out, rapid relay, mixer, bridge, sanctions) and a 0–100 score. Sanctions force Critical.
5. **Cross-chain** — bridge registry + time/value correlator. Inferred links are labelled as heuristic, not proven.
6. **Reports** — PDF investigation packet, pre-filled Section 94 BNSS request, SAHYOG JSON package. Every file has a SHA-256 digest and a chain-of-custody log.
7. **Ops UI** — case desk, live trace, Cytoscape fund-flow graph, wallet profile, alert inbox with SLA timers, VASP directory, analytics, admin (data-mode toggle + audit log).

## Tests

```bash
cd backend
pytest
```

Unit tests cover tracing / attribution; integration tests walk intake → trace → alerts → reports.

## Repository layout

```
backend/app/
  api/v1/routes/     cases, trace, wallets, attribution, alerts, vasps,
                     reports, analytics, integrations, auth
  chains/            evm, tron, bitcoin, synthetic, resolver
  tracing/           BFS engine, taint, pruning, cross-chain
  attribution/       labels, deposit heuristic, BTC cluster, classifier
  risk/              typologies, scorer, alerts
  reports/           PDF + SAHYOG builder
  integrations/      NCRP webhook + mock pusher
frontend/src/        pages, graph, api, auth
docker-compose.yml
```

This is a prototype. It is not a substitute for a court-ordered production system, and inferred bridge hops must be treated as leads, not evidence.
