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

```powershell
cd "C:\Users\e4gra\OneDrive\Desktop\Claude Code Test\backend"

# start the server (bot runs inside it)
.\.venv\Scripts\python.exe main.py

# in a SECOND PowerShell window, watch the dashboard
.\.venv\Scripts\python.exe stats.py --watch
```

Bot keeps running as long as the server window stays open. Closing PowerShell or shutting down the PC kills it. DB persists; on restart the bot auto-resumes.

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
2. **No new fills for a while?** → Could be source wallet went quiet, or Polymarket API hiccup. Check the latest fill timestamp. Most "stale" feelings are during overnight quiet hours.
3. **Bot crashed?** → Check `bot_instances.last_error` in the DB or look at the server PowerShell window for traceback.
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
