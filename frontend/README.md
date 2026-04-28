# Frontend

The frontend is generated with [Base44](https://base44.com) and dropped into this directory once exported.

## Required pages (per spec)

- **Login / Signup**
- **Dashboard** — total PnL, ROI, win rate, recent trades, by-wallet breakdown
- **Controls**
  - Wallet management (add / remove / list)
  - Risk settings (sizing strategy, % vs fixed, per-trade cap, per-market cap, daily loss cap, slippage)
  - Mode toggle (paper / live)
  - Start / Stop bot

## API the UI calls

Backend listens on `http://localhost:8000` in dev. Configure your Base44 export's API base URL accordingly. Auth uses JWT bearer tokens — `POST /auth/login` returns `{access_token, token_type}`; include `Authorization: Bearer <token>` on every protected call.

| Endpoint | Method | Body | Notes |
|---|---|---|---|
| `/auth/signup` | POST | `{email, password}` | Creates user + default settings |
| `/auth/login` | POST | OAuth2 form: `username` (email), `password` | Returns JWT |
| `/wallets` | GET | — | List user's wallets |
| `/wallets/add` | POST | `{address, label?}` | 0x-prefixed 42-char hex |
| `/wallets/remove` | POST | `{address}` | |
| `/settings` | GET | — | Returns current settings |
| `/settings/risk` | POST | partial `RiskSettingsRequest` | Only set fields are updated |
| `/settings/mode` | POST | `{mode}` | `"paper"` or `"live"` |
| `/bot/start` | POST | — | Starts user's bot loop |
| `/bot/stop` | POST | — | Stops user's bot loop |
| `/bot/status` | GET | — | `{status, last_started_at, last_error}` |
| `/trades?limit=N` | GET | — | Most recent trades, newest first |
| `/stats` | GET | — | Aggregate PnL/ROI/win-rate/by-wallet |
| `/health` | GET | — | Liveness probe |

Interactive API docs: `http://localhost:8000/docs` while the backend is running.

## When the Base44 export lands

Drop the generated app into a subdirectory here (e.g., `frontend/web/`) and update this README with the actual build/dev/deploy commands.
