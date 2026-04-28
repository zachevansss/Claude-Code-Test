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
cp .env.example .env       # set JWT_SECRET
.venv/Scripts/python -m src.wallet.crypto generate   # paste output into .env as MASTER_ENCRYPTION_KEY
python main.py             # uvicorn on :8000, /docs for interactive
```
`MASTER_ENCRYPTION_KEY` is required: signup creates a managed wallet and that wallet's private key is encrypted with this Fernet key. Losing the key permanently locks all stored wallets.

### Module map (`backend/src/`)
- `config/settings.py` — Pydantic settings, reads `.env`. Notable: `live_trading_enabled` (kill switch), `master_encryption_key`, `polygon_rpc_url`, `execution_max_retries`.
- `utils/logging.py` — `get_logger("COMPONENT")` adapter that prefixes every record with `[COMPONENT]`. Tags: `TRACKER`, `RISK`, `SIMULATION`, `EXECUTION`, `DATABASE`, `API`, `BOT_MANAGER`, `AUTH`, `ANALYTICS`, `WALLET`.
- `database/{base,session}.py` — declarative `Base`, `engine`, `SessionLocal`, `get_db()` FastAPI dep
- `models/` — `User`, `UserSettings`, `UserWallet` (source wallets to copy), `ManagedWallet` (the user's platform-managed EOA, encrypted PK), `BotInstance`, `Trade`, `Position`. Every business row carries `user_id`. `Trade` and `Position` carry `mode` ("paper"/"live") so paper and live histories live in one table cleanly separated. `Trade.asset_id` stores the Polymarket ERC-1155 token id; `Trade.external_tx` is the source-wallet tx hash used for cross-tick dedupe.
- `wallet/crypto.py` — Fernet encrypt/decrypt for private keys. Refuses to operate without a valid `MASTER_ENCRYPTION_KEY`. CLI: `python -m src.wallet.crypto generate` mints a fresh key.
- `wallet/manager.py` — `WalletManager` (static methods): `create_for_user`, `get_or_create`, `get_signer`, `get_private_key_hex`. Generation uses `eth_account.Account.create()` (CSPRNG).
- `auth/{security,jwt,deps}.py` — bcrypt hashing, JWT encode/decode, `get_current_user` dep
- `api/app.py` — app factory; lifespan hook creates tables and calls `bot_manager.restart_all()` on boot
- `api/routers/` — `auth` (signup auto-provisions a ManagedWallet), `bot`, `wallets` (source wallets to mirror), `wallet` (managed wallet read: address + on-chain USDC/MATIC), `settings`, `data` (trades + stats).
- `tracker/poller.py` — `WalletTracker.poll() -> list[TradeSignal]`. Hits Polymarket data API (`/activity?user=<addr>`) in parallel per wallet via `httpx.AsyncClient`. Per-wallet errors are isolated. First poll seeds `_seen` and emits nothing (avoids historical flood); the BotManager pre-seeds `_seen` from `Trade.external_tx` so restarts don't re-emit. Tracker instances are owned by BotManager and persist across ticks. Emits `asset_id` (CLOB token id) on every signal.
- `risk/manager.py` — `RiskManager.size(signal) -> SizedOrder`. Applies sizing strategy then per-trade / per-market / daily caps. Raises `RiskRejection` on reject.
- `simulation/engine.py` — `SimulationEngine.execute(order)` updates trades + positions with `mode='paper'`. No network.
- `executor/engine.py` — `ExecutionEngine.execute(order)` signs + posts a Polymarket CLOB GTC limit order via `py-clob-client`. Three independent safety gates (kill switch, managed wallet present, asset_id+external_tx present); slippage tolerance widens limit price; retries with backoff. Persists `Trade(mode='live')` keyed by source-wallet tx for dedupe; CLOB order id stored in `status`. Refusals raise `ExecutionRefused`; the bot loop catches and logs without crashing.
- `bot_manager/manager.py` — singleton `bot_manager`. `start/stop/restart_all/stop_all`. Owns one persistent `WalletTracker` per user. Each `_tick` polls, dedupes signals against `Trade.external_tx` in DB (final safety net), runs risk, dispatches to engine. Calls `engine.set_slippage(...)` on the live engine. Daily loss is computed from today's `Position.realized_pnl_usd` deltas.
- `analytics/engine.py` — `AnalyticsEngine.compute(user_id) -> StatsOut`

### Architecture rules
- **Modular** — each module has one job; cross-module deps via narrow interfaces (`TradeSignal`, `SizedOrder`).
- **Multi-user from day one** — every query filters by `user_id`; bot loops are per-user; never store global state that bleeds between users.
- **Paper and live use the same shape** — `SimulationEngine` and `ExecutionEngine` expose the same `.execute(order, source_wallet)`; the bot manager picks one based on `UserSettings.mode`. Don't fork code paths beyond the engine boundary.
- **Live execution is multi-gated** — `LIVE_TRADING_ENABLED` (env kill switch) AND `UserSettings.mode='live'` AND a valid managed wallet AND asset_id+external_tx on the order. All four must pass. Don't soften any of these gates.
- **Log every meaningful action** with the appropriate `[COMPONENT]` tag.
- **Don't break existing functionality** — run the API and exercise the affected endpoint(s) before declaring an edit done.
- **Don't hardcode** — anything tunable goes in `Settings` (env) or `UserSettings` (per-user DB row).

### Phases (build order — don't skip)
1. Backend structure ✅
2. Single-user bot engine end-to-end (paper) ✅ — tracker verified live against Polymarket data API
3. Multi-user architecture (structurally in place; needs load testing)
4. Database models ✅
5. API layer ✅
6. Bot manager ✅
7. Live execution ✅ structurally — needs live-fire test with funded wallet + kill switch on
8. Frontend UI (Base44)
9. Deployment (VPS + Supervisor/PM2 + auto-restart)

### Custody model
Per-user EOA, key generated via `eth_account.Account.create()` on signup, encrypted at rest with the Fernet master key from `.env`. User deposits USDC + a small amount of MATIC for gas to that address. The bot signs CLOB orders with that key on the user's behalf. Threat model: master key + DB compromise = full access to all wallets. Mitigation for SaaS phase: move to KMS (AWS/GCP/Vault). For personal use on a private VPS, env-var key is acceptable.

### Going-live checklist (do BEFORE flipping LIVE_TRADING_ENABLED=True)
1. Generate `MASTER_ENCRYPTION_KEY` and back it up to a password manager — losing it means losing all wallets.
2. Sign up, hit `GET /wallet`, see your managed address.
3. Send a small amount of USDC (e.g., $5) and ~0.5 MATIC to that address on Polygon.
4. **One-time on-chain setup not yet automated:** the EOA must `approve()` Polymarket's Exchange contract to spend USDC and CTF tokens. Until we add a `POST /wallet/setup` endpoint, do this manually or via the Polymarket UI.
5. Use a paid Polygon RPC (`POLYGON_RPC_URL` — Alchemy / Infura / QuickNode), not the public default which 401s.
6. Set `MODE=live` and `LIVE_TRADING_ENABLED=True`. Set the user's mode to `live` via `POST /settings/mode`.
7. Start the bot. Monitor `[EXECUTION]` logs and `BotInstance.last_error`.

### Known gaps still to close
- **Live-mode balance** — risk sizing still uses `paper_balance_usd`. For live mode, sub in the actual on-chain USDC balance from `/wallet`.
- **One-time wallet setup** — Polymarket Exchange approvals + CLOB API credential creation should be wrapped in `POST /wallet/setup`. Currently you'd do it manually via the Polymarket UI or a one-off script.
- **Withdrawals** — `POST /wallet/withdraw` not yet implemented. For SaaS, this needs strong auth (2FA / email confirm / cooldown).
- **Order status reconciliation** — we persist the trade at submission with the limit price. To reflect actual fill price + partial fills, add a poller that hits the CLOB order status endpoint and updates rows.
- **Async-friendliness** — `py-clob-client` is sync; `.execute()` briefly blocks the event loop. Wrap with `asyncio.to_thread()` past ~50 concurrent users.
- **Alembic migrations** — schema is `Base.metadata.create_all` only; add migrations before swapping to PostgreSQL.
