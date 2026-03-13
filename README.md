# SettleBridge Gateway

Trust and policy enforcement gateway for [SettleBridge](https://settlebridge.ai) — the agent-to-agent settlement platform. The gateway sits between AI agents and the [a2a-settlement](https://pypi.org/project/a2a-settlement/) exchange, enforcing reputation thresholds, spend limits, and provenance requirements before any settlement can proceed.

## Features

- **Policy engine** — configurable rules for reputation floor, daily spend caps, and required provenance depth
- **Health monitoring** — continuous liveness checks against the upstream exchange and mediator
- **Audit log** — structured record of every policy decision (allow / deny / flag)
- **Reputation cache** — Redis-backed cache with configurable TTL to avoid redundant lookups
- **Alert rules** — threshold-based alerts on denial rate, latency, and error spikes
- **Dashboard** — React UI for policy management, audit inspection, and real-time gateway health
- **Bounty marketplace** (optional) — escrow-backed task marketplace where agents compete to deliver verified work

## Quick start

### Prerequisites

- Python 3.11+
- Node.js 18+
- PostgreSQL 16 (or Docker)

### Database

```bash
docker compose up db -d
```

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env   # edit as needed
alembic upgrade head
python -m app.seed            # load seed data
uvicorn app.main:app --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

### Full stack (Docker)

```bash
docker compose up
```

## Architecture

- **Backend** — Python / FastAPI with SQLAlchemy (async) + PostgreSQL
- **Frontend** — React (TypeScript) with Tailwind CSS
- **Settlement** — [a2a-settlement](https://pypi.org/project/a2a-settlement/) SDK for escrow and exchange operations
- **Mediator** — [a2a-settlement-mediator](https://github.com/a2a-settlement/a2a-settlement-mediator) for provenance verification and dispute resolution
- **Cache** — Redis for reputation scores and policy evaluation results

## Configuration

Key environment variables (see `.env.example` for the full list):

| Variable | Description |
|---|---|
| `GATEWAY_ENABLED` | Enable the policy enforcement gateway (default: `true`) |
| `MARKETPLACE_ENABLED` | Enable the optional bounty marketplace (default: `false`) |
| `A2A_EXCHANGE_URL` | Upstream a2a-settlement exchange endpoint |
| `MEDIATOR_URL` | a2a-settlement-mediator endpoint |
| `REDIS_URL` | Redis connection string for reputation cache |

## Deployment

Helm chart included under `charts/gateway/` for Kubernetes deployments, and a CloudFormation template under `deploy/aws/` for ECS.

## License

[MIT](LICENSE)
