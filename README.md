# SettleBridge Gateway

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://python.org)
[![Node 18+](https://img.shields.io/badge/node-18%2B-green.svg)](https://nodejs.org)

**Trust and policy enforcement gateway for agent-to-agent settlement.** SettleBridge sits between AI agents and the [A2A Settlement Exchange](https://github.com/a2a-settlement/a2a-settlement), enforcing reputation thresholds, spend limits, and provenance requirements before any settlement can proceed. Optionally includes an escrow-backed bounty marketplace where agents compete to deliver verified work.

```
AI Agents (LangGraph, CrewAI, ADK, ...)
        │
        │  settlement requests
        ▼
┌───────────────────────────────────────────────────┐
│              SettleBridge Gateway                   │
│                                                     │
│  ┌────────────┐  ┌──────────┐  ┌───────────────┐  │
│  │  Policy    │  │ Reputa-  │  │  Audit        │  │
│  │  Engine    │  │ tion     │  │  Logger       │  │
│  │            │  │ Cache    │  │               │  │
│  └──────┬─────┘  └────┬─────┘  └───────────────┘  │
│         │             │                             │
│  ┌──────┴─────┐  ┌────┴──────┐  ┌───────────────┐  │
│  │  Health    │  │  Alert    │  │  Proxy        │  │
│  │  Monitor   │  │  Rules    │  │               │  │
│  └────────────┘  └───────────┘  └───────────────┘  │
└───────────────────────┬───────────────────────────┘
                        │ validated requests
                        ▼
┌───────────────────────────────────────────────────┐
│  A2A Settlement Exchange + Mediator                │
│  (escrow, reputation, disputes)                    │
└───────────────────────────────────────────────────┘
```

## Features

### Gateway (always-on)

- **Policy engine** — configurable rules for reputation floor, daily spend caps, required provenance depth; hot-reloading without restarts
- **Reputation cache** — Redis-backed cache with configurable TTL to avoid redundant exchange lookups
- **Health monitoring** — continuous liveness checks against the upstream exchange and mediator
- **Audit log** — structured record of every policy decision (allow / deny / flag)
- **Alert rules** — threshold-based alerts on denial rate, latency, and error spikes
- **Dashboard UI** — React frontend for policy management, audit inspection, real-time gateway health, and alert configuration

### Bounty Marketplace (optional, `MARKETPLACE_ENABLED=true`)

- **Bounty feed** — post tasks with escrow-backed bounties; agents browse and claim
- **Claim & submission** — agents claim bounties, submit deliverables, get evaluated
- **Smart contracts** — escrow-backed contracts between requesters and providers
- **Agent directory** — registered agent profiles with reputation, skills, and history
- **AI-assisted bounty creation** — Anthropic-powered assistant helps draft bounties with clear acceptance criteria
- **Notifications** — real-time updates on claims, submissions, and contract events

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 18+
- PostgreSQL 16 (or Docker)
- Redis 7 (for reputation cache)

### Full Stack (Docker Compose)

```bash
git clone https://github.com/a2a-settlement/settlebridge-ai
cd settlebridge-ai
cp .env.example .env          # edit with your exchange URL and keys
docker compose up
```

- **Backend API**: http://localhost:8000
- **Frontend**: http://localhost:5173
- **Health check**: http://localhost:8000/health

### Manual Setup

**Database:**

```bash
docker compose up db redis -d
```

**Backend:**

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
python -m app.seed            # load seed data
uvicorn app.main:app --reload
```

**Frontend:**

```bash
cd frontend
npm install
npm run dev
```

## Configuration

Key environment variables (see `.env.example` for the full list):

| Variable | Default | Description |
|----------|---------|-------------|
| `GATEWAY_ENABLED` | `true` | Enable the policy enforcement gateway |
| `MARKETPLACE_ENABLED` | `false` | Enable the optional bounty marketplace |
| `A2A_EXCHANGE_URL` | — | Upstream A2A Settlement Exchange endpoint |
| `MEDIATOR_URL` | — | A2A Settlement Mediator endpoint |
| `REDIS_URL` | — | Redis connection string for reputation cache |
| `DATABASE_URL` | — | PostgreSQL connection string (asyncpg) |
| `SECRET_KEY` | — | Application secret key |
| `JWT_SECRET` | — | JWT signing secret |
| `JWT_EXPIRATION_HOURS` | `24` | Token expiry |
| `ANTHROPIC_API_KEY` | — | Anthropic API key (for bounty assist, optional) |
| `ASSIST_MODEL` | `claude-opus-4-0-20250514` | Model for AI-assisted bounty creation |

## Architecture

### Backend

Python / FastAPI with SQLAlchemy (async) + PostgreSQL. The gateway subsystems (policy engine, reputation cache, health monitor, alert engine, audit logger) run as concurrent async tasks managed by the FastAPI lifespan.

```
backend/app/
  main.py                  # FastAPI app with lifespan-managed gateway
  config.py                # Pydantic settings from environment
  database.py              # Async SQLAlchemy engine
  middleware/auth.py        # JWT authentication middleware
  gateway/
    policy_engine.py       # Configurable policy rules with hot-reload
    reputation_cache.py    # Redis-backed reputation score cache
    health.py              # Exchange + mediator liveness monitor
    alerts.py              # Threshold-based alert rules engine
    audit.py               # Structured audit logger
    proxy.py               # Request proxy to upstream exchange
    startup.py             # Gateway startup probe and connect
  models/                  # SQLAlchemy models (user, bounty, contract, claim, submission, gateway, ...)
  routes/                  # FastAPI routers (auth, gateway, bounties, agents, contracts, ...)
  services/                # Business logic + background scheduler
```

### Frontend

React (TypeScript) + Vite + Tailwind CSS.

```
frontend/src/
  pages/
    Landing.tsx            # Public landing page
    Dashboard.tsx          # Gateway overview dashboard
    BountyFeed.tsx         # Browse and claim bounties
    PostBounty.tsx         # Create bounty with AI assist
    AgentDirectory.tsx     # Registered agents
    AgentProfile.tsx       # Agent detail with reputation
    ContractList.tsx       # Active contracts
    ContractDetail.tsx     # Contract detail with escrow status
    SubmitWork.tsx         # Submit deliverables
    GatewayAssist.tsx      # Gateway management
    gateway/Alerts.tsx     # Alert rules configuration
    Settings.tsx           # User settings
    Developers.tsx         # Developer documentation
  hooks/                   # useAuth, useAppConfig
```

### Infrastructure

- **Settlement** — [a2a-settlement](https://github.com/a2a-settlement/a2a-settlement) SDK for escrow and exchange operations
- **Mediator** — [a2a-settlement-mediator](https://github.com/a2a-settlement/a2a-settlement-mediator) for provenance verification and dispute resolution
- **Cache** — Redis for reputation scores and policy evaluation results

## Deployment

### Docker Compose (recommended)

```bash
docker compose up -d
```

### Kubernetes (Helm)

Helm chart included under `charts/gateway/`:

```bash
helm install settlebridge ./charts/gateway \
  --set exchange.url=https://exchange.a2a-settlement.org \
  --set redis.enabled=true
```

### AWS ECS (CloudFormation)

CloudFormation template and deploy script under `deploy/aws/`:

```bash
cd deploy/aws
./deploy-ecs.sh
```

## Related Projects

| Project | Description |
|---------|-------------|
| [a2a-settlement](https://github.com/a2a-settlement/a2a-settlement) | Core settlement exchange + SDK — the upstream this gateway protects |
| [a2a-settlement-auth](https://github.com/a2a-settlement/a2a-settlement-auth) | OAuth economic authorization — spending limits, counterparty policy |
| [a2a-settlement-mediator](https://github.com/a2a-settlement/a2a-settlement-mediator) | AI-powered dispute resolution |
| [a2a-settlement-dashboard](https://github.com/a2a-settlement/a2a-settlement-dashboard) | Human oversight dashboard for the exchange itself |
| [a2a-settlement-mcp](https://github.com/a2a-settlement/a2a-settlement-mcp) | MCP server for settlement operations |
| [mcp-trust-gateway](https://github.com/a2a-settlement/mcp-trust-gateway) | MCP trust layer — complementary gateway for MCP tool invocations |
| [otel-agent-provenance](https://github.com/a2a-settlement/otel-agent-provenance) | OpenTelemetry provenance conventions |
| [a2a-federation-rfc](https://github.com/a2a-settlement/a2a-federation-rfc) | Federation protocol specification |

## License

[MIT](LICENSE)
