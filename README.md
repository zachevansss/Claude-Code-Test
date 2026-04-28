# Polymarket Copy-Trade SaaS

Multi-tenant copy-trading for Polymarket. Each user can register, add wallets they want to mirror, configure sizing + risk, and run an isolated 24/7 bot in **paper** or **live** mode. Stats and PnL are tracked per user and per wallet.

## Stack

- **Backend:** Python · FastAPI · SQLAlchemy 2.0 (SQLite for dev → PostgreSQL for prod)
- **Auth:** JWT (HS256) · bcrypt password hashing
- **Bot runtime:** one `asyncio` task per user, owned by `BotManager`; persists status to DB so a server restart resumes running bots
- **Frontend:** Base44-generated React/Next.js (placeholder under `frontend/`)
- **Process supervision (prod):** PM2 / Supervisor on a VPS

## Layout

```
backend/
  main.py                  FastAPI/uvicorn entrypoint
  requirements.txt
  .env.example
  src/
    config/                Pydantic settings (reads .env)
    utils/                 Structured [COMPONENT] logger
    database/              Engine + session + declarative Base
    models/                User, UserSettings, UserWallet, BotInstance, Trade, Position
    auth/                  Password hashing, JWT, FastAPI deps
    api/                   FastAPI app + routers + Pydantic schemas
    tracker/               Wallet activity poller (Polymarket data API)
    risk/                  Sizing strategies + per-trade/market/daily caps
    simulation/            Paper-trading engine
    executor/              Live execution engine (NotImplementedError until wallet model is decided)
    bot_manager/           Per-user asyncio loop, lifecycle, restart-on-boot
    analytics/             PnL / ROI / win-rate / by-wallet aggregation
frontend/                  Base44 export drop-in (see frontend/README.md)
CLAUDE.md                  Workflow + module guide for Claude Code
```

## Run the backend (dev, Windows)

```bash
cd backend
python -m venv .venv
source .venv/Scripts/activate         # Git Bash on Windows
pip install -r requirements.txt
cp .env.example .env                  # then edit JWT_SECRET etc.
python main.py
```

API on `http://localhost:8000`. Interactive docs at `/docs`.

## Smoke-test the tracker (no DB or API server needed)

```bash
.venv/Scripts/python scripts/smoke_tracker.py
# or against a different wallet:
.venv/Scripts/python scripts/smoke_tracker.py --wallet 0xabc...
```

Polls Polymarket's data API for the configured wallet, parses the response through `WalletTracker`, and prints up to 10 emitted signals. Verified on `0x2005d16a84ceefa912d4e380cd32e7ff827875ea` (48 signals returned).

## Modes

- `MODE=paper` — `SimulationEngine` writes trades + positions with `mode='paper'`. No network calls to exchanges.
- `MODE=live` — `ExecutionEngine` is currently `NotImplementedError`. Will use a **managed-wallet (custodial)** model: the platform generates and encrypts a per-user EOA, signs Polymarket CLOB orders on the user's behalf for 24/7 autonomous trading. SaaS deployment of this model triggers significant regulatory + security obligations — see CLAUDE.md.

## Status

- Phase 1 (backend skeleton) ✅
- Phase 2 (single-user paper bot end-to-end) ✅ — tracker verified live against Polymarket data API
- Next: managed-wallet executor (key generation + encryption + Polymarket CLOB signing), then frontend (Base44).

See `CLAUDE.md` for module conventions and the per-edit commit workflow used in this repo.
