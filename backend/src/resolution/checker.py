"""Auto-close paper/live positions whose underlying market has resolved.

Why this exists: the source wallet doesn't sell winners — they hold to
resolution and call redeem() on-chain to convert outcome tokens to USDC.
Polymarket's activity API does NOT surface redemptions as TRADE events, so
the tracker has nothing to copy. Without this checker our open positions just
sit forever, with avg_price as cost basis and no realized PnL.

Detection strategy (pragmatic, not perfect):
  1. /midpoints batch for all distinct asset_ids
  2. Anything missing from the response has no live orderbook — typically
     resolved, occasionally just illiquid
  3. /last-trades-prices for those missing assets
  4. If the last trade settled at an extreme (<=0.02 or >=0.98), treat as
     resolved at that price
  5. Otherwise leave it open — better to under-close than wrongly close an
     illiquid-but-not-resolved market

Closing writes a synthetic Trade row (side='sell', status='resolved',
external_tx=NULL) and zeros out the Position size, capturing the realized PnL
on the Position row via the standard sell math."""
from __future__ import annotations

from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from src.models import Position, Trade
from src.utils.logging import get_logger

log = get_logger("RESOLUTION")

CLOB_BASE = "https://clob.polymarket.com"
RESOLVE_LOW = 0.02
RESOLVE_HIGH = 0.98


def _fetch_prices(asset_ids: list[str]) -> dict[str, float]:
    """Return asset_id -> price using midpoints first, last-trade-price for
    anything missing. Empty dict on network failure."""
    if not asset_ids:
        return {}
    out: dict[str, float] = {}
    body = [{"token_id": a} for a in asset_ids]
    try:
        r = httpx.post(f"{CLOB_BASE}/midpoints", json=body, timeout=10.0)
        r.raise_for_status()
        for k, v in r.json().items():
            if v is not None:
                try:
                    out[k] = float(v)
                except (TypeError, ValueError):
                    pass
    except Exception as e:  # noqa: BLE001
        log.warning("midpoints fetch failed: %s", e)

    return out


def _fetch_last_prices(asset_ids: list[str]) -> dict[str, float]:
    if not asset_ids:
        return {}
    out: dict[str, float] = {}
    body = [{"token_id": a} for a in asset_ids]
    try:
        r = httpx.post(f"{CLOB_BASE}/last-trades-prices", json=body, timeout=10.0)
        r.raise_for_status()
        for row in r.json():
            tok = row.get("token_id")
            price = row.get("price")
            if tok and price is not None:
                try:
                    out[tok] = float(price)
                except (TypeError, ValueError):
                    pass
    except Exception as e:  # noqa: BLE001
        log.warning("last-trades-prices fetch failed: %s", e)
    return out


def check_resolutions(db: Session, user_id: int, mode: str = "paper") -> int:
    """Close any open positions for this user whose markets have resolved.

    Returns the number of positions closed. Idempotent — if no markets have
    resolved since the last call, returns 0 with no DB writes."""
    rows = db.execute(
        # Pull each open position with one asset_id from a matching trade row.
        # SQLAlchemy text() would also work; using the session's bind for
        # raw SQL keeps this self-contained.
        Position.__table__.select().where(
            (Position.user_id == user_id)
            & (Position.mode == mode)
            & (Position.size > 0)
        )
    ).fetchall()
    if not rows:
        return 0

    # Build position list with asset_ids resolved via Trade lookup.
    positions: list[tuple[Position, str]] = []
    for row in rows:
        pos = db.get(Position, row.id)
        if pos is None:
            continue
        asset = (
            db.query(Trade.asset_id)
            .filter(
                Trade.user_id == user_id,
                Trade.market_id == pos.market_id,
                Trade.outcome == pos.outcome,
                Trade.mode == mode,
                Trade.asset_id.isnot(None),
            )
            .first()
        )
        if asset and asset[0]:
            positions.append((pos, asset[0]))

    if not positions:
        return 0

    asset_ids = [a for _, a in positions]
    mids = _fetch_prices(asset_ids)
    missing = [a for a in asset_ids if a not in mids]
    last_prices = _fetch_last_prices(missing) if missing else {}

    closed = 0
    for pos, asset in positions:
        if asset in mids:
            continue  # market still has an orderbook — not resolved
        last = last_prices.get(asset)
        if last is None:
            continue  # no signal at all — leave open
        if RESOLVE_LOW < last < RESOLVE_HIGH:
            continue  # mid-range last trade — likely just illiquid, skip

        # Close the position at the settled price.
        size = pos.size
        avg = pos.avg_price
        realized_delta = (last - avg) * size
        notional = last * size

        synth = Trade(
            user_id=user_id,
            source_wallet="resolution",
            market_id=pos.market_id,
            asset_id=asset,
            outcome=pos.outcome,
            side="sell",
            price=last,
            size=size,
            notional_usd=notional,
            mode=mode,
            status="resolved",
            external_tx=None,
            created_at=datetime.utcnow(),
        )
        db.add(synth)
        pos.realized_pnl_usd += realized_delta
        pos.size = 0.0
        closed += 1
        log.info(
            "resolved user=%s %s @%.4f size=%.2f cost_avg=%.4f pnl=%+.2f",
            user_id, pos.outcome, last, size, avg, realized_delta,
        )

    if closed:
        db.commit()
    return closed
