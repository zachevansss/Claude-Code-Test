"""Auto-close paper/live positions whose underlying market has resolved.

Why this exists: the source wallet doesn't sell winners — they hold to
resolution and call redeem() on-chain to convert outcome tokens to USDC.
Polymarket's activity API does NOT surface redemptions as TRADE events, so
the tracker has nothing to copy. Without this checker our open positions just
sit forever, with avg_price as cost basis and no realized PnL.

Detection strategy (in order, fail-open at each step):
  1. gamma-api `/markets?closed=true&condition_ids=...` — Polymarket's
     authoritative resolution status. Returns final outcomePrices ([1,0] or
     [0,1]) for resolved markets. Close at that exact price.
  2. /midpoints batch — anything still in the orderbook is unresolved.
  3. /last-trades-prices for missing midpoints, with extreme-price heuristic
     (<=0.02 or >=0.98) as a backstop for markets that don't appear in
     gamma-api yet but have clearly settled.

The gamma-api step was added after observing that some markets resolve while
their last on-CLOB trade still sat mid-range (e.g., 0.23 for a side that
ultimately settled at 0). Without it, those positions stay open forever and
the dashboard shows stale mark-to-market values.

Closing writes a synthetic Trade row (side='sell', status='resolved',
external_tx=NULL) and zeros out the Position size, capturing the realized PnL
on the Position row via the standard sell math."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from src.models import Position, Trade
from src.utils.logging import get_logger

log = get_logger("RESOLUTION")

CLOB_BASE = "https://clob.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
RESOLVE_LOW = 0.02
RESOLVE_HIGH = 0.98
GAMMA_BATCH_SIZE = 20
GAMMA_MIN_BATCH = 5  # below this, we give up rather than spam single-cid retries


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


def _gamma_query(condition_ids: list[str]) -> list[dict] | None:
    """Single batched call. Returns the payload list on 2xx, None on error
    (signal to caller that this batch needs to be retried with a smaller size)."""
    params: list[tuple[str, str]] = [("condition_ids", c) for c in condition_ids]
    params.append(("closed", "true"))
    params.append(("limit", str(len(condition_ids))))
    try:
        r = httpx.get(f"{GAMMA_API}/markets", params=params, timeout=10.0)
        r.raise_for_status()
        payload = r.json()
    except Exception as e:  # noqa: BLE001
        log.debug("gamma-api batch of %d failed: %s", len(condition_ids), e)
        return None
    return payload if isinstance(payload, list) else None


def _parse_gamma_close_time(m: dict) -> datetime | None:
    """Pull the market's actual resolution timestamp.

    Preference order: closedTime (UMA resolved-at) > umaEndDate > endDate.
    Returns None if all are missing or malformed. Future-dated values are
    discarded since they indicate the resolution time hasn't been set yet
    (some markets report endDate while still awaiting UMA finalization).
    """
    for field in ("closedTime", "umaEndDate", "endDate"):
        raw = m.get(field)
        if not raw:
            continue
        # Normalize to UTC-aware datetime. Polymarket emits a mix of:
        #   '2026-05-10T02:16:57Z'
        #   '2026-05-10 02:16:57+00'
        #   '2026-05-09T19:00:00.000Z'
        s = raw.replace("Z", "+00:00") if isinstance(raw, str) else raw
        if isinstance(s, str) and s.endswith("+00"):
            s = s + ":00"
        try:
            dt = datetime.fromisoformat(s) if isinstance(s, str) else None
        except ValueError:
            dt = None
        if dt is None:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        # Reject future-dated (means UMA hasn't finalized yet — let utcnow win).
        now = datetime.now(timezone.utc)
        if dt > now:
            continue
        # SQLite stores naive UTC; strip tz to match.
        return dt.replace(tzinfo=None)
    return None


def _parse_gamma_settle(m: dict) -> tuple[str, dict[str, float], datetime | None] | None:
    """Pull (conditionId, {asset_id: settle_price}, close_at) out of one market
    row. Returns None for malformed rows; close_at None means "use now"."""
    cid = m.get("conditionId")
    if not cid:
        return None
    prices_raw = m.get("outcomePrices")
    tokens_raw = m.get("clobTokenIds")
    if isinstance(prices_raw, str):
        try:
            prices_raw = json.loads(prices_raw)
        except json.JSONDecodeError:
            return None
    if isinstance(tokens_raw, str):
        try:
            tokens_raw = json.loads(tokens_raw)
        except json.JSONDecodeError:
            return None
    if not (isinstance(prices_raw, list) and isinstance(tokens_raw, list)):
        return None
    if len(prices_raw) != len(tokens_raw):
        return None
    try:
        settle = {str(tok): float(p) for tok, p in zip(tokens_raw, prices_raw)}
    except (TypeError, ValueError):
        return None
    return cid, settle, _parse_gamma_close_time(m)


def _fetch_resolved_from_gamma(
    condition_ids: list[str],
) -> dict[str, tuple[dict[str, float], datetime | None]]:
    """Return {conditionId: ({asset_id: settle_price}, close_at)} for markets
    gamma-api reports as closed. close_at is the market's actual resolution
    time (UMA closedTime / endDate) for accurate dating of synthetic trades.

    Batches at GAMMA_BATCH_SIZE; on failure recursively halves until either
    success or batch size < GAMMA_MIN_BATCH. Fails open per branch."""
    if not condition_ids:
        return {}
    out: dict[str, tuple[dict[str, float], datetime | None]] = {}

    def consume(batch: list[str]) -> None:
        if not batch:
            return
        payload = _gamma_query(batch)
        if payload is not None:
            for m in payload:
                parsed = _parse_gamma_settle(m)
                if parsed is not None:
                    cid, settles, close_at = parsed
                    out[cid] = (settles, close_at)
            return
        if len(batch) < GAMMA_MIN_BATCH:
            log.warning("gamma-api batch of %d failed, giving up on these ids", len(batch))
            return
        mid = len(batch) // 2
        consume(batch[:mid])
        consume(batch[mid:])

    for i in range(0, len(condition_ids), GAMMA_BATCH_SIZE):
        consume(condition_ids[i:i + GAMMA_BATCH_SIZE])
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


def _close_position(
    db: Session,
    user_id: int,
    mode: str,
    pos: Position,
    asset: str,
    settle_price: float,
    *,
    source: str,
    closed_at: datetime | None = None,
) -> None:
    """Synthesize a 'sell' Trade row at settle_price and zero out the position.
    `source` is just a label for the log line: 'gamma' vs 'heuristic'.
    `closed_at` is the actual market resolution time; falls back to utcnow()
    if not known (e.g., the heuristic path doesn't have this info)."""
    size = pos.size
    avg = pos.avg_price
    realized_delta = (settle_price - avg) * size
    notional = settle_price * size
    title_row = (
        db.query(Trade.title)
        .filter(
            Trade.user_id == user_id,
            Trade.market_id == pos.market_id,
            Trade.outcome == pos.outcome,
            Trade.mode == mode,
            Trade.title.isnot(None),
        )
        .first()
    )
    synth = Trade(
        user_id=user_id,
        source_wallet="resolution",
        market_id=pos.market_id,
        asset_id=asset,
        outcome=pos.outcome,
        title=title_row[0] if title_row else None,
        side="sell",
        price=settle_price,
        size=size,
        notional_usd=notional,
        mode=mode,
        status="resolved",
        external_tx=None,
        created_at=closed_at if closed_at is not None else datetime.utcnow(),
    )
    db.add(synth)
    pos.realized_pnl_usd += realized_delta
    pos.size = 0.0
    log.info(
        "resolved [%s] user=%s %s @%.4f size=%.2f cost_avg=%.4f pnl=%+.2f close_at=%s",
        source, user_id, pos.outcome, settle_price, size, avg, realized_delta,
        closed_at.isoformat() if closed_at else "now",
    )


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

    # Step 1: authoritative gamma-api resolution. Markets here are officially
    # closed with deterministic outcomePrices ([1,0] or [0,1]). Settle exact.
    condition_ids = list({pos.market_id for pos, _ in positions})
    gamma_resolved = _fetch_resolved_from_gamma(condition_ids)

    closed = 0
    unresolved: list[tuple[Position, str]] = []

    for pos, asset in positions:
        entry = gamma_resolved.get(pos.market_id)
        if entry is None:
            unresolved.append((pos, asset))
            continue
        settles, closed_at = entry
        if asset not in settles:
            unresolved.append((pos, asset))
            continue
        _close_position(
            db, user_id, mode, pos, asset, settles[asset],
            source="gamma", closed_at=closed_at,
        )
        closed += 1

    if not unresolved:
        if closed:
            db.commit()
        return closed

    # Step 2 + 3: fall back to /midpoints + /last-trades-prices heuristic for
    # markets gamma-api didn't return (e.g., very fresh resolutions that haven't
    # propagated yet, or markets gamma-api doesn't index).
    asset_ids = [a for _, a in unresolved]
    mids = _fetch_prices(asset_ids)
    missing = [a for a in asset_ids if a not in mids]
    last_prices = _fetch_last_prices(missing) if missing else {}

    for pos, asset in unresolved:
        if asset in mids:
            continue  # market still has an orderbook — not resolved
        last = last_prices.get(asset)
        if last is None:
            continue  # no signal at all — leave open
        if RESOLVE_LOW < last < RESOLVE_HIGH:
            continue  # mid-range last trade — likely just illiquid, skip
        _close_position(db, user_id, mode, pos, asset, last, source="heuristic")
        closed += 1

    if closed:
        db.commit()
    return closed
