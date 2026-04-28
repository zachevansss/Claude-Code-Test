"""Wallet activity tracker — polls Polymarket for trades on watched wallets.

Phase 1 stub: returns no signals so the bot loop runs end-to-end without
external calls. Phase 2 will hit the Polymarket Data API
(https://data-api.polymarket.com/activity?user=<addr>), parse fills,
dedupe via self._seen, and emit TradeSignal."""
from src.risk.manager import TradeSignal
from src.utils.logging import get_logger

log = get_logger("TRACKER")


class WalletTracker:
    def __init__(self, addresses: list[str]) -> None:
        self.addresses = [a.lower() for a in addresses]
        self._seen: set[str] = set()  # tx hashes / activity ids already emitted

    async def poll(self) -> list[TradeSignal]:
        # TODO Phase 2: GET data-api activity for each address, build TradeSignal list,
        #               dedupe via self._seen, return.
        log.debug("poll() stub — tracking %d wallets", len(self.addresses))
        return []
