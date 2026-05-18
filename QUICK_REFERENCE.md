# My Bot — Quick Reference

A condensed cheat-sheet for **what this project is, how it fits together, and the tools / shortcuts I use to run it.**

---

## What the project does (one paragraph)

A Polymarket copy-trading bot. I point it at a source wallet on Polymarket, and the bot mirrors that wallet's trades into my own (paper or live) account, with risk caps so it can't blow up. **It runs 24/7 on a Hetzner VPS in Helsinki** (`204.168.246.106`) as a systemd service called `copytrade`. The local Windows machine is no longer the bot's home — it's just where I look at the dashboard from. The bot fills as the source fills, auto-closes positions when markets resolve, and produces a live dashboard I can watch over Tailscale at `http://100.107.39.71:8000/dashboard`. **In live mode**, the bot trades from a self-custodied EOA on Polygon (`0xe5d87349…498b8`) via Polymarket's V2 CLOB — funds are pUSD; the proxy / Magic Link wallet is bypassed entirely.

---

## Folder layout

```
Claude Code Test/                 ← project root
├── backend/                      ← Python server + bot logic + DB
│   ├── src/                      ← all the code (tracker, risk, executor, etc.)
│   ├── stats.py                  ← CLI dashboard script
│   ├── main.py                   ← server entrypoint (uvicorn)
│   ├── copytrade.db              ← SQLite database (trades, positions, settings)
│   └── .env                      ← my secrets (JWT, master key, RPC URL)
├── frontend/                     ← (placeholder — Base44 UI lives here later)
├── CLAUDE.md                     ← instructions for Claude Code itself
├── README.md                     ← public-facing project description
├── QUICK_REFERENCE.md            ← this file
└── .gitignore                    ← what NOT to push to GitHub
```

### What each file is for

| File | Purpose |
|---|---|
| **CLAUDE.md** | Tells Claude Code how to behave on this repo (commit rules, project conventions, architecture overview). Claude reads it automatically. **I edit it when project rules change.** |
| **README.md** | Public-facing description of the project. Anyone landing on the GitHub page sees this. |
| **QUICK_REFERENCE.md** | This doc — for me. |
| **.gitignore** | Lists files git should never push. Includes `.env`, `copytrade.db`, `.venv/`. Without this, secrets and personal data would leak to GitHub. |
| **.env** | My local secrets. Never pushed (it's in .gitignore). |
| **copytrade.db** | All trade/position history. Never pushed (also gitignored). **The local file is a frozen snapshot from migration day (2026-05-14).** The live DB lives on the VPS at `/root/copytrade/backend/copytrade.db`. |

---

## Tools I use

| Tool | What it is | What I use it for |
|---|---|---|
| **Claude Code** | AI coding assistant in a terminal. | The main way I make changes — I type what I want, it writes code, edits files, runs commands. |
| **Cursor** | An AI-powered code editor (think VS Code with Claude built in). | Looking at code, manual edits when I want to peek before letting Claude touch it. |
| **PowerShell** | Windows terminal. | Running my dashboard (`stats.py --watch`), running the server, git commands when I'm not using Claude. |
| **git** | Version control. Tracks every change to my code. | Lets me see history, undo bad changes, sync code between machines. |
| **GitHub** | Cloud storage for git repos. Mine is at `github.com/zachevansss/Claude-Code-Test`. | Backup of my code. If my PC dies, my code lives there. Claude Code auto-pushes after every commit per CLAUDE.md rules. |

---

## Infrastructure & external services

These are the four outside services the bot leans on (in order of how essential they are right now).

| Service | What it is | How it's integrated |
|---|---|---|
| **Polygon** | The Layer-2 blockchain Polymarket runs on. Everything on-chain — my proxy wallet, pUSD balance, the CTF Exchange v2 contract, conditional outcome tokens — lives here. Polygon mainnet is chain ID 137. | This is the substrate, not a thing I sign up for. The bot reads my pUSD balance from a Polygon address, sends approve/transfer txs to Polygon contracts, and submits orders that ultimately settle on Polygon. `POLYGON_CHAIN_ID=137` in `.env` pins it. |
| **Alchemy** | A blockchain RPC provider — basically a "phone line" to Polygon. I have a free-tier account; their free quota (≈300M compute units/month) is far more than one bot needs. | My private RPC URL lives in `.env` as `POLYGON_RPC_URL=https://polygon-mainnet.g.alchemy.com/v2/<key>`. Every module that touches Polygon — `wallet/balances.py`, `wallet/approvals.py`, `executor/engine.py` — reads it via `settings.polygon_rpc_url`. The free public endpoint (`polygon-rpc.com`) started returning 401s, so a paid-tier provider is required to talk to Polygon at all. |
| **NordVPN** | Commercial VPN service. I use the Finland endpoint when I open Polymarket in my browser. | **Not integrated in code.** Polymarket geoblocks the US *website*, but `clob.polymarket.com` and `data-api.polymarket.com` (the APIs the bot calls) aren't geoblocked, so the bot itself doesn't need a VPN. The VPN matters when *I* log in to deposit, withdraw, or check the UI. Will become relevant for the VPS only if Hetzner's IP block ends up Polymarket-restricted. |
| **Hetzner VPS** | Cheap European VPS provider (typically €4–8/month for a CX11 / CPX22 box). | **ACTIVE.** Provisioned 2026-05-09, bot migrated 2026-05-14. CX22 box in Helsinki, Ubuntu 24.04. Public IPv4 `204.168.246.106`. Bot runs as the `copytrade` systemd service from `/root/copytrade/backend/`. Logs at `/root/copytrade/backend/logs/bot.log` (rotating). Migration was forced by my PC's sleep cycles pausing the bot when I was away. |
| **Tailscale** | Zero-config mesh VPN that gives my PC and VPS private `100.x.x.x` addresses they can reach each other on, no matter what network I'm on. Free for personal use. | Installed on PC and VPS 2026-05-15. The VPS's Tailscale IP is `100.107.39.71` — that's the address I use for the dashboard now, *not* the public `204.168.246.106`. `ufw` on the VPS only allows port 8000 from Tailscale's `100.64.0.0/10` range, so the dashboard isn't reachable from the open internet. Works from any device I install Tailscale on (PC, phone, anywhere). |

Mental-model notes:

- **Polygon ≠ Polymarket.** Polymarket is the platform (UI, order book, CLOB API). Polygon is the chain underneath where everything actually settles. Alchemy lets me *read and write* Polygon; it has nothing to do with Polymarket directly.
- **Alchemy is mission-critical even in paper mode.** `GET /wallet` reads pUSD over Alchemy. If the key dies or my quota runs out, the wallet endpoint returns `balance_error` and the UI shows $0.
- **The VPN is for me, not the bot.** The bot's HTTPS calls to Polymarket's APIs work from any IP. So the bot can run on a US VPS, a Hetzner box, or my laptop without a VPN.
- **The bot lives on the VPS now, not my PC.** Closing PowerShell, putting my PC to sleep, even shutting it down — none of that affects the bot. It keeps polling, sizing, filling.
- **Tailscale ≠ NordVPN.** They both run at the same time without conflict. Tailscale handles only `100.x.x.x` traffic (PC ↔ VPS); NordVPN handles everything else (Polymarket browsing). Windows routes by specificity, so the more-specific Tailscale route wins for dashboard traffic.
- **NordVPN's *browser extension* will still try to intercept the dashboard URL** (the network bypass is at a different layer). Either whitelist the dashboard IP in the extension, use a different browser for it, or use an Incognito/InPrivate window (extensions are off there by default).

---

## How everything is wired together

```
                    [ Source Polymarket Wallet ]
                              │
                              │ (poll every 5s)
                              ▼
[ backend tracker ]  →  [ risk manager ]  →  [ paper/live engine ]  →  [ DB ]
                              │                                          │
                              │                                          │
                              ▼                                          ▼
                       [ resolution checker ]              [ stats.py / HTML dashboard ]
                       (every 60s, closes                       (what I see)
                        resolved markets)
```

- **Tracker** hits Polymarket's data API to see what the source wallet did
- **Risk manager** decides if a trade should fire and how big — applies all my caps (per-trade, per-market, leverage, daily-loss, mirror curve)
- **Engine** writes a paper fill (or sends a real CLOB order in live mode) and persists to the DB
- **Resolution checker** every minute looks at open positions, finds resolved markets, closes them at the settle price → realized PnL
- **stats.py** reads the DB and prints the dashboard. It doesn't *control* anything; it's a viewer.

---

## Running the bot

**I don't have to run anything to keep the bot alive.** It runs on the VPS as a systemd service that auto-starts on boot and auto-restarts on crash. My PC's role is now just *viewing*, not *hosting*. Closing every terminal on my Windows machine has zero effect on whether trades fire.

### Viewing the dashboard

Just open a browser to:

```
http://100.107.39.71:8000/dashboard
```

That's the VPS reached via its Tailscale IP. Works from my PC, my phone (with Tailscale installed), anywhere I'm on Tailscale. Bookmark it.

**Caveat — NordVPN's browser extension:** if I get a "Squid error" page when loading the dashboard, that's NordVPN's Threat Protection intercepting at the browser level. Either disable the extension on that site, use Incognito/InPrivate (Ctrl+Shift+N), or use a different browser without the NordVPN extension. The network connection itself is fine (PowerShell `Invoke-WebRequest` to the URL works); it's purely a browser-extension issue.

### Controlling the bot on the VPS

SSH in first (from PowerShell):

```powershell
ssh root@204.168.246.106
```

Then on the VPS:

| Command | What it does |
|---|---|
| `systemctl status copytrade` | Is it running? Memory/CPU/uptime |
| `systemctl restart copytrade` | Restart the bot (rarely needed) |
| `systemctl stop copytrade` | Stop it. Only do this if I really mean it. |
| `journalctl -u copytrade --no-pager --since "5 minutes ago" \| tail -50` | Recent log lines from systemd |
| `tail -f /root/copytrade/backend/logs/bot.log` | Live log tail — better for watching trades flow in real time |
| `curl -s http://localhost:8000/health` | Bot's own health JSON — same as the dashboard URL but compact |
| `systemctl list-timers copytrade-*` | See next-fire time for both the nightly redemption timer and the 5-min monitor timer |
| `journalctl -u copytrade-redeem --since yesterday` | What last night's redemption cron did (or didn't do) |
| `journalctl -u copytrade-monitor --since "1 hour ago" \| tail` | What the alert monitor has been seeing |

### Automated jobs (set up 2026-05-18, running on the VPS)

Two systemd timers handle the "set and forget" parts. Both are at `/etc/systemd/system/copytrade-{redeem,monitor}.{service,timer}`.

| Timer | Fires | What it does |
|---|---|---|
| `copytrade-redeem.timer` | `00:00 America/Chicago` daily (handles DST) | Calls `CTF.redeemPositions` for every resolved standard market the EOA still holds outcome tokens for. **Neg-risk markets are skipped** (phase 2 work). pUSD lands back at the EOA, bot's next tick picks up the new balance for sizing. |
| `copytrade-monitor.timer` | every 5 min | Checks `systemctl is-active copytrade`, `/health`, tick freshness, on-chain MATIC; pushes alerts to ntfy.sh on issues. |

**Push alerts via ntfy.sh.** Topic suffix: `copytrade-zach-bXhgqJszo0K_bUU9YVDb0NdB`. I subscribe in the free ntfy iOS/Android app (paste topic name, no account needed). Conditions that alert: bot service down, /health unreachable, tick stale >30 min, bot status≠running, bot last_error non-null, MATIC < 1.0 (warn) / < 0.1 (critical). 6-hour cooldown per condition.

Manual override commands (from VPS):
- `systemctl start copytrade-redeem.service` — fire redemption now
- `systemctl start copytrade-monitor.service` — re-check alerts now
- `curl -X POST "https://ntfy.sh/copytrade-zach-bXhgqJszo0K_bUU9YVDb0NdB" -H "Title: Test" -d "hi"` — send a test push

### What still works locally for legacy / offline viewing

The local `backend/copytrade.db` is a **frozen snapshot from migration day**. I can still run `stats.py --watch` against it to see history *as of that snapshot* — but it doesn't update because no bot is writing to it. For the live picture, always use the browser dashboard URL above.

```powershell
# Optional — offline view of frozen local DB
cd "C:\Users\e4gra\OneDrive\Desktop\Claude Code Test\backend"
.\.venv\Scripts\python.exe stats.py --watch
```

---

## Git / GitHub flow

CLAUDE.md tells Claude to commit + push after every successful edit. So:

- I make a change with Claude → Claude commits + pushes → backed up to GitHub instantly
- If I break something I can `git log` to see history, `git revert` to undo
- I never have to remember to save — the rule does it for me

---

## Claude Code keyboard shortcuts

| Shortcut | What it does |
|---|---|
| **Ctrl + O** | Show details of what Claude is doing right now (current tool call, output) |
| **Esc Esc** | Press Esc twice — clears the current input box (good for "delete all words I just typed") |
| **Ctrl + Shift + Tab** | Cycle Claude's mode (Auto / Plan / Ask) |
| **↑ / ↓ Arrows** | Scroll the conversation up and down |
| **Alt + Enter** | New line in the input box without submitting (lets me type multi-line prompts) |
| **/model** | Change which Claude model is being used (Opus / Sonnet / Haiku, etc.) |

---

## Common slash commands

| Command | What it does |
|---|---|
| `/model` | Switch Claude model |
| `/help` | Claude Code help menu |
| `/clear` | Clear the conversation (Claude forgets prior context) |

---

## Quick mental model when something looks broken

1. **Dashboard URL won't load in browser?** → First: does PowerShell `Invoke-WebRequest -Uri "http://100.107.39.71:8000/health"` succeed? If yes, the bot is fine and the browser is being blocked by NordVPN's extension — try Incognito (Ctrl+Shift+N). If PowerShell also fails, Tailscale or the VPS itself has a problem.
2. **No new fills for a while?** → SSH into the VPS and run `curl -s http://localhost:8000/health` (the `/health` endpoint reports `last_tick_at`, `last_signal_emitted_at`, `last_poll_status`). If `status:ok` and recent ticks but no fills, source is quiet or trades are getting risk-rejected. Check `tail -50 /root/copytrade/backend/logs/bot.log` for `[BOT_MANAGER] risk rejected ...` lines — those tell me *why* (usually mirror size below `min_trade_usd` floor).
3. **Bot crashed?** → `systemctl status copytrade` shows current state. `journalctl -u copytrade --since "10 minutes ago"` shows recent output. systemd auto-restarts crashed services, so a single crash usually self-heals. Also check `bot_instances.last_error` in the DB (visible on the dashboard's health banner). If the failure is `sqlite3.OperationalError: database is locked`, verify the DB is still in WAL mode: `sqlite3 /root/copytrade/backend/copytrade.db "PRAGMA journal_mode"` should return `wal`. The bot's connect-time pragmas reset this on every start, but worth checking after a DB restore from backup.
4. **Phone alerts stop arriving?** → `systemctl list-timers copytrade-monitor.timer` should show a recent fire time. Test manually: `curl -X POST "https://ntfy.sh/copytrade-zach-bXhgqJszo0K_bUU9YVDb0NdB" -H "Title: Test" -d "hi"`. If that lands on phone, the channel works; the monitor cron may have a bug. If it doesn't land, check the ntfy app's subscription is still there and Notification permissions are on.
5. **Resolved positions not redeeming?** → `journalctl -u copytrade-redeem --since yesterday` shows last night's run. Most common skip reasons: "neg-risk market — SKIP" (phase 2 work — manually redeem via Polygonscan Write Contract on the NegRiskAdapter) or "no on-chain balance" (already redeemed or never owned). Manual fire: `systemctl start copytrade-redeem.service`.
6. **Tailscale dashboard unreachable from a new device?** → Install Tailscale on the new device and sign into the same account. Confirm both devices appear at https://login.tailscale.com/admin/machines. No VPS-side change needed.
7. **Lost my home IP whitelist (NordVPN extension blocking)?** → Already documented above. The ufw rule for the home IP `107.202.220.218` is leftover from before Tailscale — can be deleted now, since Tailscale is the only path I use.
8. **Lost my .env / master key?** → All managed wallets are unrecoverable. Why I back the master key up to a password manager. **The live `.env` lives on the VPS at `/root/copytrade/backend/.env`** — `scp` it down for backup. **When backing up `copytrade.db` also grab `copytrade.db-wal` and `copytrade.db-shm`** in the same instant; backing up only the `.db` while WAL has uncommitted writes loses recent activity.
9. **Pushed something I shouldn't have?** → `.gitignore` should prevent it. If a file made it through, `git rm --cached <file>` and add it to `.gitignore`.

---

## Going-live reminders (the short version)

Detailed plan lives at `~/.claude/plans/i-have-a-couple-resilient-wirth.md`. Highlights:

- Don't go live until ≥7 paper days, ≥1500 fills, ≥1 down day observed
- ~~Use a VPS~~ ✅ **Done.** Bot runs on Hetzner Helsinki via systemd.
- Use a **VPN with kill-switch** if VPS is in a Polymarket-blocked region — Helsinki passes the geoblock test (`ipinfo.io` returns `FI`).
- Start live params *more conservative* than paper — friction (gas, slippage, latency) drags returns
- ~~Redemption is manual~~ ✅ **Now automated.** The nightly `copytrade-redeem.timer` calls `CTF.redeemPositions` on resolved standard markets and lands pUSD back at the EOA. Neg-risk markets are still skipped (phase 2 work) — if I hold neg-risk winners they sit unredeemed in CTF tokens until I either build phase 2 or manually redeem via Polygonscan's Write Contract on the NegRiskAdapter.
- Before flipping `LIVE_TRADING_ENABLED=True` in the VPS `.env`, verify `/health` reports `status:ok` and the dashboard banner shows the bot has been ticking healthily for >24h.

## Live launch state (as of 2026-05-18)

The migration from proxy to self-custody EOA is **done** — held only at the user's request before flipping the final live env flag. Everything below is on-chain reality:

| State | Value |
|---|---|
| EOA address | `0xe5d87349a102c2b8b1f84f34e6c8ac6310c498b8` |
| pUSD balance | 998.91 |
| MATIC (gas) | 20.77 |
| `managed_wallets.proxy_address` for user_id=1 | NULL (executor routes via SIG_EOA) |
| pUSD → V2 CTF Exchange `0xE111...996B` | max allowance |
| pUSD → V2 NegRisk Exchange `0xe222...0F59` | max allowance |
| CTF → V2 CTF Exchange | approved |
| CTF → V2 NegRisk Exchange | approved |
| DB journal mode | `wal` (with 30s busy_timeout) |
| `MODE` in `.env` | `paper` (held) |
| `LIVE_TRADING_ENABLED` in `.env` | `False` (held) |

To flip live: edit `/root/copytrade/backend/.env` → `MODE=live`, `LIVE_TRADING_ENABLED=True`. Apply live-mode risk overrides via `POST /settings` per `project_live_risk_overrides.md`. Then `systemctl restart copytrade`. Watch `journalctl -u copytrade -f` for the first `[EXECUTION]` lines.

### How the live flow works (one-paragraph mental model)

Source wallet trades → tracker emits signal → risk manager sizes via mirror_scale × sqrt(source_notional), floored at min_trade_usd → executor signs SIG_EOA order with V2 EIP-712 domain → submits to Polymarket CLOB → reconciler polls fill status every tick and writes fill_price/filled_size → on market resolution, `resolution/checker.py` closes the position in the DB at the settle price → at midnight Central the redemption timer calls `CTF.redeemPositions` and lands pUSD back at the EOA → next tick's on-chain pUSD lookup picks up the new balance → that funds the next round of orders. Failure modes alert via ntfy push.

---

That's it. Keep this file handy when I forget how something works.
