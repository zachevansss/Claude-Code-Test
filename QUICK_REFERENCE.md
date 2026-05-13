# My Bot — Quick Reference

A condensed cheat-sheet for **what this project is, how it fits together, and the tools / shortcuts I use to run it.**

---

## What the project does (one paragraph)

A Polymarket copy-trading bot. I point it at a source wallet on Polymarket, and the bot mirrors that wallet's trades into my own (paper or live) account, with risk caps so it can't blow up. It runs in the background while my PC is on, fills as the source fills, auto-closes positions when markets resolve, and produces a live dashboard I can watch.

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
| **copytrade.db** | All trade/position history. Never pushed (also gitignored). |

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
| **Hetzner VPS** | Cheap European VPS provider (typically €4–8/month for a CX11 / CPX11 box). | **Not yet provisioned.** Planned home for the bot to run 24/7 instead of inside my PowerShell window. `backend/deploy/install.sh` and `backend/deploy/launch.md` are the scripts I'll run on the VPS at cutover — they set up systemd + the .env + the venv. Once migrated, the SQLite DB and `.env` live on the VPS, and my PC stops being the bot's home. |

Mental-model notes:

- **Polygon ≠ Polymarket.** Polymarket is the platform (UI, order book, CLOB API). Polygon is the chain underneath where everything actually settles. Alchemy lets me *read and write* Polygon; it has nothing to do with Polymarket directly.
- **Alchemy is mission-critical even in paper mode.** `GET /wallet` reads pUSD over Alchemy. If the key dies or my quota runs out, the wallet endpoint returns `balance_error` and the UI shows $0.
- **The VPN is for me, not the bot.** The bot's HTTPS calls to Polymarket's APIs work from any IP. So the bot can run on a US VPS, a Hetzner box, or my laptop without a VPN.
- **Hetzner is the destination, not the present.** Right now the bot runs on this Windows box while it's on. The VPS migration is a separate going-live step.

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

I need **two PowerShell terminals open at the same time**. They do different things — don't confuse them.

| Terminal | Command | What it does | If I close it |
|---|---|---|---|
| **A — the bot** | `.\.venv\Scripts\python.exe main.py` | Polls source wallets, runs risk checks, fires paper/live fills, writes to DB. **This is what takes trades.** | Bot dies. No new fills until I restart it. |
| **B — the dashboard** | `.\.venv\Scripts\python.exe stats.py --watch` | Reads the DB and shows stats on screen, refreshes every 5s. Pure viewer. | Nothing breaks. Trades keep firing. I just can't see them until I reopen it. |

```powershell
cd "C:\Users\e4gra\OneDrive\Desktop\Claude Code Test\backend"

# TERMINAL A — the bot itself (must stay open for trades to fire)
.\.venv\Scripts\python.exe main.py

# TERMINAL B (separate window) — the dashboard (optional viewer)
.\.venv\Scripts\python.exe stats.py --watch
```

**Key rule:** the dashboard ≠ the bot. If trades stop, it's because `main.py` died, not because I closed the dashboard.

Bot keeps running as long as the `main.py` terminal stays open. Closing PowerShell or shutting down the PC kills it. DB persists; on restart the bot auto-resumes.

### Two ways to view the dashboard

| Where | How | Needs |
|---|---|---|
| **In the terminal (CLI)** | `.\.venv\Scripts\python.exe stats.py --watch` | Nothing else — reads the DB directly. Works even if `main.py` is off. |
| **In a browser (HTML, phone-friendly)** | Open `http://localhost:8000/dashboard` in any browser on the same machine | `main.py` must be running (it serves the page). |

If the terminal dashboard looks like a wall of scrolling text, it's old refresh history piled up. Press **Ctrl+C**, run `cls`, then re-run `stats.py --watch` for a clean view. Make the terminal window tall so all the stats sections fit on one screen.

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

1. **Dashboard shows old data?** → Restart `stats.py --watch` (Ctrl+C, re-run)
2. **No new fills for a while?** → **First check: is `main.py` still running?** Open a terminal and run `tasklist | findstr python` — if you don't see a python process whose command line is `main.py`, the bot is dead. Restart it. (Closing the dashboard does NOT stop trades — only closing/crashing `main.py` does.) If `main.py` is alive but still no fills, it might be a quiet source wallet or a Polymarket API hiccup.
3. **Bot crashed?** → The `main.py` terminal in Cursor will show a red error icon on the tab. Scroll up in that terminal for the Python traceback. Also check `bot_instances.last_error` in the DB.
4. **Lost my .env / master key?** → All managed wallets are unrecoverable. Why I back the master key up to a password manager.
5. **Pushed something I shouldn't have?** → `.gitignore` should prevent it. If a file made it through, `git rm --cached <file>` and add it to `.gitignore`.

---

## Going-live reminders (the short version)

Detailed plan lives at `~/.claude/plans/i-have-a-couple-resilient-wirth.md`. Highlights:

- Don't go live until ≥7 paper days, ≥1500 fills, ≥1 down day observed
- Use a **VPS** (Hetzner / DigitalOcean) so the bot runs 24/7
- Use a **VPN with kill-switch** if VPS is in a Polymarket-blocked region
- Start live params *more conservative* than paper — friction (gas, slippage, latency) drags returns
- One real code gap: auto-resolver doesn't call `redeemPositions()` on-chain yet. That needs to be added before live or capital won't recycle.

---

That's it. Keep this file handy when I forget how something works.
