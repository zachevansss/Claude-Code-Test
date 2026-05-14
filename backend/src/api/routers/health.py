"""Health endpoint with real liveness checks.

200 + status="ok"        — every critical check passed
200 + status="degraded"  — bot alive but signals are stale; still safe to serve
                            traffic / let bot keep running
503 + status="unhealthy" — bot is not ticking, DB unreachable, or running in
                            live mode without a managed wallet. Tooling on a
                            VPS can poll this and alert.

Intentionally unauthenticated — same posture as /dashboard. No secrets, no
control surface. Only exposes timestamps and counts the dashboard already shows.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.config.settings import settings
from src.database.session import SessionLocal, engine
from src.models import BotInstance, ManagedWallet, UserSettings

router = APIRouter()

# Tick freshness threshold. The bot loop sleeps `bot_poll_interval_seconds`
# (default 5s), so 30s is ~6 missed ticks — enough to absorb a slow tick
# without false alarms, tight enough to catch a stuck/dead coroutine.
TICK_STALE_SECONDS = 30

# Signal freshness threshold. Source wallet can legitimately be quiet for
# hours, so this is "warn" not "fail" — degraded status, still 200.
SIGNAL_STALE_SECONDS = 4 * 3600


def _age(ts: datetime | None) -> float | None:
    if ts is None:
        return None
    # bot_instances columns are stored as naive UTC (datetime.utcnow()).
    return (datetime.now(timezone.utc) - ts.replace(tzinfo=timezone.utc)).total_seconds()


@router.get("/health")
def health(response: Response) -> dict:
    checks: dict[str, dict] = {}
    overall_ok = True
    degraded = False

    # --- DB reachable -------------------------------------------------------
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = {"ok": True}
    except Exception as e:  # noqa: BLE001
        checks["database"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        overall_ok = False

    # --- Bot state ----------------------------------------------------------
    with SessionLocal() as db:
        bot = db.query(BotInstance).first()
        if not bot:
            checks["bot"] = {"ok": False, "error": "no BotInstance row"}
            overall_ok = False
        else:
            tick_age = _age(bot.last_tick_at)
            sig_age = _age(bot.last_signal_emitted_at)
            bot_check: dict = {
                "user_id": bot.user_id,
                "status": bot.status,
                "last_started_at": bot.last_started_at.isoformat() if bot.last_started_at else None,
                "last_tick_at": bot.last_tick_at.isoformat() if bot.last_tick_at else None,
                "last_signal_emitted_at": (
                    bot.last_signal_emitted_at.isoformat() if bot.last_signal_emitted_at else None
                ),
                "tick_age_seconds": int(tick_age) if tick_age is not None else None,
                "signal_age_seconds": int(sig_age) if sig_age is not None else None,
                "last_poll_status": bot.last_poll_status,
                "last_error": bot.last_error,
            }
            ok = True
            reasons: list[str] = []
            if bot.status != "running":
                ok = False
                reasons.append(f"status={bot.status}")
            elif tick_age is None or tick_age > TICK_STALE_SECONDS:
                ok = False
                reasons.append(
                    f"last_tick stale: {int(tick_age) if tick_age is not None else 'never'}s"
                )
            elif sig_age is not None and sig_age > SIGNAL_STALE_SECONDS:
                # Degraded, not failed — source could be legitimately quiet.
                degraded = True
                reasons.append(f"last_signal stale: {int(sig_age)}s")
            if bot.last_error:
                degraded = True
                reasons.append("last_error present")
            bot_check["ok"] = ok
            if reasons:
                bot_check["reasons"] = reasons
            checks["bot"] = bot_check
            if not ok:
                overall_ok = False

        # --- Live-mode wallet check (only when running live) ----------------
        if bot:
            us = db.query(UserSettings).filter(UserSettings.user_id == bot.user_id).first()
            if us and us.mode == "live":
                managed = db.query(ManagedWallet).filter(
                    ManagedWallet.user_id == bot.user_id
                ).first()
                if not managed:
                    checks["managed_wallet"] = {"ok": False, "error": "no managed wallet"}
                    overall_ok = False
                else:
                    checks["managed_wallet"] = {"ok": True}
                checks["live_trading_enabled"] = {
                    "ok": True,
                    "value": settings.live_trading_enabled,
                }

    if not overall_ok:
        status = "unhealthy"
        response.status_code = 503
    elif degraded:
        status = "degraded"
    else:
        status = "ok"

    return {
        "status": status,
        "mode": settings.mode,
        "checks": checks,
    }
