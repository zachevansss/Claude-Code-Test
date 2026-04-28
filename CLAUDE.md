# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Workflow: commit and push after every edit (mandatory)

**As you work in this repo you MUST commit and push regularly so no work is ever lost.** This is a hard requirement, not a suggestion. Every successful file edit (Edit / Write / NotebookEdit) inside this working tree must be followed — in the same response, before yielding back to the user — by:

1. `git add <edited files>`
2. `git commit -m "<clean imperative message>"`
3. `git push` (to `origin/main`)

The goal: at any point the user can stop the session, walk away, and find every change preserved on GitHub. If you have edited a file and have not yet pushed, your turn is not finished.

**Commit message rules:**
- Imperative mood, specific about *what* changed: "Add user auth helper", "Fix typo in README intro", "Refactor parser to handle empty input".
- Never generic placeholders like "auto commit", "update file", "changes", "wip".
- Describe the change, not the task ("Fix off-by-one in pagination" — not "Fix bug user reported").

**Granularity:**
- One logical change per commit. Don't batch unrelated edits.
- Multiple files edited as part of a single logical change can share one commit.
- Many small commits is the desired outcome — granular history is the whole point.

**When NOT to commit:**
- The edit failed (don't commit broken state — fix first, then commit).
- The user explicitly said "don't commit yet" / "hold off pushing" / similar — honor that until they release the hold.
- The path is outside this working tree (e.g., `~/.claude/...` user config).
- The file is gitignored — `.claude/` is per-user Claude Code state and is not committed.

**If a push fails** (network, auth, conflict): surface the error to the user immediately. Do not silently move on; an unpushed commit means work is at risk.

## Git identity is repo-local

`user.name` and `user.email` are set in `.git/config` for this repo only — the user's **global** git config is intentionally empty. If a commit ever fails with a missing-identity error, restore with:

```
git config user.name "zachevansss"
git config user.email "zevans4548@gmail.com"
```

Don't promote these to `--global`.

## Remote

`origin` → `https://github.com/zachevansss/Claude-Code-Test` (private). Default branch is `main`. `gh` CLI is authenticated as `zachevansss` and can be used for repo-level operations (issues, PRs, releases).

## Project: Polymarket Copy-Trade SaaS

Multi-tenant copy-trading platform. Each user signs up, registers wallet addresses to mirror, configures sizing + risk, and runs an isolated bot in paper or live mode.

### Stack
- Backend: FastAPI · SQLAlchemy 2.0 · SQLite (PostgreSQL-ready) · JWT/bcrypt auth
- Bot runtime: one `asyncio` task per user, owned by `BotManager`. State persisted to DB so a server restart resumes any bot whose row says `running`.
- Frontend: Base44-generated React/Next.js (placeholder under `frontend/`)

### Run dev backend
```bash
cd backend
python -m venv .venv && source .venv/Scripts/activate
pip install -r requirements.txt
cp .env.example .env       # edit JWT_SECRET
python main.py             # uvicorn on :8000, /docs for interactive
```

### Module map (`backend/src/`)
- `config/settings.py` — Pydantic settings, reads `.env`
- `utils/logging.py` — `get_logger("COMPONENT")` returns adapter that prefixes every record with `[COMPONENT]`. Use exactly: `TRACKER`, `RISK`, `SIMULATION`, `EXECUTION`, `DATABASE`, `API`, `BOT_MANAGER`, `AUTH`, `ANALYTICS`.
- `database/{base,session}.py` — declarative `Base`, `engine`, `SessionLocal`, `get_db()` FastAPI dep
- `models/` — `User`, `UserSettings`, `UserWallet`, `BotInstance`, `Trade`, `Position`. Every business row carries `user_id`. `Trade` and `Position` carry `mode` ("paper"/"live") so paper and live histories live in one table cleanly separated.
- `auth/{security,jwt,deps}.py` — bcrypt hashing, JWT encode/decode, `get_current_user` dep
- `api/app.py` — app factory; lifespan hook creates tables and calls `bot_manager.restart_all()` on boot
- `api/routers/` — `auth`, `bot`, `wallets`, `settings`, `data` (trades + stats)
- `tracker/poller.py` — `WalletTracker.poll() -> list[TradeSignal]`. Hits Polymarket data API (`/activity?user=<addr>`) in parallel per wallet via `httpx.AsyncClient`. Per-wallet errors are isolated. First poll seeds `_seen` and emits nothing (avoids historical flood); the BotManager pre-seeds `_seen` from `Trade.external_tx` so restarts don't re-emit. Tracker instances are owned by BotManager and persist across ticks.
- `risk/manager.py` — `RiskManager.size(signal) -> SizedOrder`. Applies sizing strategy then per-trade / per-market / daily caps. Raises `RiskRejection` on reject.
- `simulation/engine.py` — `SimulationEngine.execute(order)` updates trades + positions with `mode='paper'`. No network.
- `executor/engine.py` — `ExecutionEngine.execute(order)` raises `NotImplementedError`. Wiring it up depends on the wallet/custody decision (non-custodial signing vs. managed wallet).
- `bot_manager/manager.py` — singleton `bot_manager`. `start/stop/restart_all/stop_all`. Owns one persistent `WalletTracker` per user. Each `_tick` polls, dedupes signals against `Trade.external_tx` in DB (final safety net), runs risk, dispatches to engine. Daily loss is computed from today's `Position.realized_pnl_usd` deltas.
- `analytics/engine.py` — `AnalyticsEngine.compute(user_id) -> StatsOut`

### Architecture rules
- **Modular** — each module has one job; cross-module deps via narrow interfaces (`TradeSignal`, `SizedOrder`).
- **Multi-user from day one** — every query filters by `user_id`; bot loops are per-user; never store global state that bleeds between users.
- **Paper and live use the same shape** — `SimulationEngine` and `ExecutionEngine` expose the same `.execute(order, source_wallet)`; the bot manager picks one based on `UserSettings.mode`. Don't fork code paths beyond the engine boundary.
- **Live execution is gated** — `ExecutionEngine` raises `NotImplementedError`. Don't replace it with placeholder logic; wait for the wallet/custody decision.
- **Log every meaningful action** with the appropriate `[COMPONENT]` tag.
- **Don't break existing functionality** — run the API and exercise the affected endpoint(s) before declaring an edit done.
- **Don't hardcode** — anything tunable goes in `Settings` (env) or `UserSettings` (per-user DB row).

### Phases (build order — don't skip)
1. Backend structure ✅
2. Single-user bot engine end-to-end (paper) ✅ — tracker live; needs live-fire verification against Polymarket API
3. Multi-user architecture (structurally in place; needs load testing)
4. Database models ✅ (refine as needed)
5. API layer ✅
6. Bot manager ✅ (real tracker wired)
7. Frontend UI (Base44)
8. Deployment (VPS + Supervisor/PM2 + auto-restart)

### Known gaps to close before going live
- `ExecutionEngine` — blocked on wallet/custody decision (see open question below).
- Tracker activity-row schema — fields parsed defensively (`conditionId`/`marketId`, `outcome`/`outcomeName`, etc.); validate against a real Polymarket response and tighten once known.
- Live-mode balance — `_tick` currently uses `paper_balance_usd` for risk sizing in both modes; live mode needs real on-chain USDC balance lookup.
- Slippage handling, retry logic — to be added inside `ExecutionEngine` once it's real.
- Alembic migrations — schema lives only in `Base.metadata.create_all`; add migrations before swapping to PostgreSQL.

### Open architectural decision
**Wallet/custody model for live execution** — see executor stub. Three options: non-custodial WalletConnect signing, custodial managed wallet, or imported private keys (do not implement option 3).
