"""Wallet activity tracker — polls Polymarket data API for trades on watched wallets.

One tracker per user, owned by the BotManager and persistent across ticks so
in-memory dedupe state is preserved. The BotManager pre-seeds `_seen` from the
trades table on construction, so a server restart resumes deduping correctly
(any source-wallet trades that happened during downtime get emitted on the
next poll because their tx hash isn't in `_seen` yet)."""
import asyncio
from typing import Any

import httpx

from src.config.settings import settings
from src.risk.manager import TradeSignal
from src.utils.logging import get_logger

log = get_logger("TRACKER")

_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
_FETCH_LIMIT = 50  # activity rows per wallet per poll


class WalletTracker:
    def __init__(
        self,
        addresses: list[str],
        seen: set[str] | None = None,
        initialized: bool = False,
    ) -> None:
        self._addresses = [a.lower() for a in addresses]
        self._seen: set[str] = set(seen) if seen else set()
        # When initialized=False, the first poll seeds `_seen` and emits nothing
        # (avoids flooding paper trades with historical fills on fresh bot start).
        # The BotManager sets initialized=True when DB pre-seed already populated _seen.
        self._initialized = initialized

    def update_addresses(self, addresses: list[str]) -> None:
        """Called when the user adds/removes wallets. `_seen` is keyed by tx hash
        (globally unique), so we keep it across address changes.

        KNOWN LIMITATION: a brand-new wallet's last 50 fills will all look new
        on the first poll after addition. Refine later with a per-address
        timestamp filter if it becomes an issue."""
        self._addresses = [a.lower() for a in addresses]

    async def poll(self) -> list[TradeSignal]:
        """Fetch new fills for tracked wallets and return deduped signals."""
        if not self._addresses:
            return []

        async with httpx.AsyncClient(
            base_url=settings.polymarket_data_api_url, timeout=_TIMEOUT
        ) as client:
            results = await asyncio.gather(
                *(self._fetch_one(client, addr) for addr in self._addresses),
                return_exceptions=True,
            )

        signals: list[TradeSignal] = []
        for addr, result in zip(self._addresses, results):
            if isinstance(result, Exception):
                log.warning("poll failed for %s: %s", addr, result)
                continue
            for activity in result:
                tx = activity.get("transactionHash")
                if not tx or tx in self._seen:
                    continue
                self._seen.add(tx)
                if not self._initialized:
                    continue  # first-tick seed only — don't emit historicals
                sig = self._to_signal(addr, activity)
                if sig:
                    signals.append(sig)

        if not self._initialized:
            log.info(
                "tracker initialized: seeded %d activities across %d wallets",
                len(self._seen), len(self._addresses),
            )
            self._initialized = True
        elif signals:
            log.info(
                "emitting %d new signal(s) from %d wallet(s)",
                len(signals), len(self._addresses),
            )
        return signals

    async def _fetch_one(
        self, client: httpx.AsyncClient, address: str
    ) -> list[dict[str, Any]]:
        resp = await client.get(
            "/activity", params={"user": address, "limit": _FETCH_LIMIT}
        )
        resp.raise_for_status()
        data = resp.json()
        # Some Polymarket endpoints wrap the rows; some return a bare array.
        if isinstance(data, dict):
            data = data.get("data") or data.get("activity") or []
        return [
            a for a in data
            if isinstance(a, dict) and (a.get("type") in (None, "TRADE"))
        ]

    def _to_signal(self, source_wallet: str, a: dict[str, Any]) -> TradeSignal | None:
        """Convert a raw activity row to a TradeSignal. Returns None if the row
        is malformed or not a tradable fill — never raises."""
        try:
            side_raw = (a.get("side") or "").upper()
            if side_raw not in {"BUY", "SELL"}:
                return None
            market_id = a.get("conditionId") or a.get("marketId") or a.get("market")
            outcome = a.get("outcome") or a.get("outcomeName") or "?"
            price = float(a.get("price") or 0)
            size = float(a.get("size") or 0)
            if not market_id or price <= 0 or size <= 0:
                return None
            asset_id = a.get("asset")
            title = a.get("title")
            return TradeSignal(
                source_wallet=source_wallet,
                market_id=str(market_id),
                outcome=str(outcome),
                side=side_raw.lower(),
                price=price,
                size=size,
                external_tx=a.get("transactionHash"),
                asset_id=str(asset_id) if asset_id is not None else None,
                title=str(title) if title is not None else None,
            )
        except (TypeError, ValueError) as e:
            log.warning("failed to parse activity row: %s (raw=%s)", e, a)
            return None
