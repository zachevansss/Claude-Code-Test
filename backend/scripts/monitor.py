"""Health monitor for the copytrade bot — sends ntfy.sh push alerts on issues.

Designed to run as a systemd timer every 5 minutes. Alerts on:
  - Service stopped (systemctl is-active copytrade != active)
  - /health unreachable
  - Bot tick stale (last_tick_at > 30 min ago while service is up)
  - Bot status != 'running'
  - Bot last_error is non-null
  - On-chain MATIC low (< 1.0) or critical (< 0.1)

Cooldown per condition (default 6h) avoids spam if a problem persists. When a
condition clears, its cooldown resets so the next occurrence alerts again.

Required env (set in the systemd unit):
  NTFY_TOPIC — the ntfy.sh topic to push to
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx
from web3 import Web3

sys.path.insert(0, "/root/copytrade/backend")
from src.config.settings import settings


NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
HEALTH_URL = "http://localhost:8000/health"
STATE_FILE = Path("/var/lib/copytrade-monitor/state.json")
COOLDOWN_SECONDS = 6 * 3600

LOW_MATIC = 1.0
CRITICAL_MATIC = 0.1
STALE_TICK_SECONDS = 30 * 60


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def _save_state(s: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(s, indent=2))


def _push(title: str, body: str, priority: str, tags: str) -> bool:
    """Post one ntfy message. Returns True on success."""
    headers = {
        "Title": title,
        "Priority": priority,  # min | low | default | high | urgent
        "Tags": tags,
    }
    try:
        r = httpx.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            headers=headers,
            content=body.encode("utf-8"),
            timeout=10,
        )
        r.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001
        print(f"ntfy push failed: {e}", file=sys.stderr)
        return False


def _maybe_alert(state: dict, key: str, title: str, body: str,
                 priority: str = "high", tags: str = "warning") -> None:
    now = time.time()
    last = state.get(key, 0)
    if now - last < COOLDOWN_SECONDS:
        return
    if _push(title, body, priority, tags):
        state[key] = now
        print(f"alerted: {key}")


def _clear_alert(state: dict, key: str) -> None:
    if state.pop(key, None) is not None:
        print(f"cleared: {key}")


def _eoa_address() -> str:
    path = settings.database_url.replace("sqlite:///", "", 1)
    conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True, timeout=10)
    try:
        row = conn.execute(
            "SELECT address FROM managed_wallets WHERE user_id = 1"
        ).fetchone()
        return Web3.to_checksum_address(row[0]) if row else ""
    finally:
        conn.close()


def main() -> int:
    if not NTFY_TOPIC:
        print("NTFY_TOPIC env var not set", file=sys.stderr)
        return 1

    state = _load_state()

    # --- 1. systemctl is-active ---
    try:
        r = subprocess.run(
            ["systemctl", "is-active", "copytrade"],
            capture_output=True, text=True, timeout=5,
        )
        status_text = r.stdout.strip() or "(empty)"
        if status_text != "active":
            _maybe_alert(state, "service_stopped",
                "Copytrade bot DOWN",
                f"systemctl is-active reports: {status_text}\n"
                f"SSH in and run:  systemctl status copytrade",
                priority="urgent", tags="rotating_light")
        else:
            _clear_alert(state, "service_stopped")
    except Exception as e:  # noqa: BLE001
        print(f"systemctl check failed: {e}", file=sys.stderr)

    # --- 2. /health endpoint ---
    health: dict = {}
    try:
        resp = httpx.get(HEALTH_URL, timeout=5)
        health = resp.json()
        _clear_alert(state, "health_unreachable")
    except Exception as e:  # noqa: BLE001
        _maybe_alert(state, "health_unreachable",
            "Copytrade /health unreachable",
            f"GET {HEALTH_URL}: {e}",
            priority="urgent", tags="rotating_light")

    bot = (health.get("checks") or {}).get("bot") or {}

    tick_age = bot.get("tick_age_seconds")
    if isinstance(tick_age, (int, float)) and tick_age > STALE_TICK_SECONDS:
        _maybe_alert(state, "stale_tick",
            "Bot tick stale",
            f"Last tick was {int(tick_age/60)} min ago — bot may be stuck.\n"
            f"Last poll: {bot.get('last_poll_status', 'unknown')}",
            priority="high", tags="warning")
    else:
        _clear_alert(state, "stale_tick")

    bstatus = bot.get("status")
    if bstatus and bstatus != "running":
        _maybe_alert(state, "bot_not_running",
            "Bot not running",
            f"Bot status: {bstatus}",
            priority="high", tags="warning")
    else:
        _clear_alert(state, "bot_not_running")

    err = bot.get("last_error")
    if err:
        _maybe_alert(state, "last_error",
            "Bot has recent error",
            f"{err}",
            priority="high", tags="warning")
    else:
        _clear_alert(state, "last_error")

    # --- 3. On-chain MATIC ---
    try:
        eoa = _eoa_address()
        if eoa:
            w3 = Web3(Web3.HTTPProvider(
                settings.polygon_rpc_url, request_kwargs={"timeout": 10}
            ))
            matic = w3.eth.get_balance(eoa) / 1e18
            if matic < CRITICAL_MATIC:
                _maybe_alert(state, "matic_critical",
                    "MATIC critically low",
                    f"EOA MATIC: {matic:.4f}\n"
                    f"Live trades may fail.  Send ~5 MATIC to:\n{eoa}",
                    priority="urgent", tags="rotating_light")
                _clear_alert(state, "matic_low")  # subsumed
            elif matic < LOW_MATIC:
                _maybe_alert(state, "matic_low",
                    "MATIC running low",
                    f"EOA MATIC: {matic:.4f}\n"
                    f"Send ~5 MATIC to {eoa} when convenient.",
                    priority="high", tags="warning")
            else:
                _clear_alert(state, "matic_low")
                _clear_alert(state, "matic_critical")
    except Exception as e:  # noqa: BLE001
        print(f"MATIC check failed: {e}", file=sys.stderr)

    _save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
