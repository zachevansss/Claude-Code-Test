"""Risk management — sizing strategies and per-trade / per-market / daily caps."""
from dataclasses import dataclass

from src.utils.logging import get_logger

log = get_logger("RISK")


@dataclass
class TradeSignal:
    """Output of the wallet tracker; input to risk."""
    source_wallet: str
    market_id: str
    outcome: str
    side: str               # "buy" | "sell"
    price: float
    size: float             # source-wallet size (units, not USD)
    external_tx: str | None = None  # source-wallet tx hash; used for dedupe


@dataclass
class SizedOrder:
    """A signal post-risk: notional and size are what the engine should execute."""
    market_id: str
    outcome: str
    side: str
    price: float
    size: float
    notional_usd: float
    external_tx: str | None = None  # propagated from the source signal


class RiskRejection(Exception):
    """Raised when a signal is rejected by risk checks."""


class RiskManager:
    """Stateless besides the runtime context handed in (settings, balance, exposure, daily loss)."""

    def __init__(
        self,
        settings,                                  # UserSettings ORM row
        balance_usd: float,
        exposure_by_market_usd: dict[str, float],
        daily_loss_usd: float,
    ) -> None:
        self.settings = settings
        self.balance_usd = balance_usd
        self.exposure_by_market_usd = exposure_by_market_usd
        self.daily_loss_usd = daily_loss_usd

    def size(self, signal: TradeSignal) -> SizedOrder:
        # Daily loss cap — short-circuit before any sizing math.
        if self.daily_loss_usd >= self.settings.daily_loss_cap_usd:
            raise RiskRejection(
                f"daily loss cap reached "
                f"({self.daily_loss_usd:.2f} >= {self.settings.daily_loss_cap_usd:.2f})"
            )

        # 1. Base sizing strategy.
        if self.settings.sizing_strategy == "percent":
            notional = self.balance_usd * (self.settings.sizing_percent / 100.0)
        else:  # "fixed"
            notional = self.settings.sizing_fixed_usd

        # 2. Cap by max % per trade.
        max_per_trade = self.balance_usd * (self.settings.max_percent_per_trade / 100.0)
        notional = min(notional, max_per_trade)

        # 3. Cap by remaining exposure budget on this market.
        used = self.exposure_by_market_usd.get(signal.market_id, 0.0)
        remaining = max(0.0, self.settings.max_exposure_per_market_usd - used)
        notional = min(notional, remaining)

        if notional <= 0:
            raise RiskRejection("zero or negative notional after caps")
        if signal.price <= 0:
            raise RiskRejection(f"non-positive signal price: {signal.price}")

        size = notional / signal.price
        log.info(
            "sized %s %s @%.4f size=%.4f notional=%.2f",
            signal.side, signal.outcome, signal.price, size, notional,
        )
        return SizedOrder(
            market_id=signal.market_id,
            outcome=signal.outcome,
            side=signal.side,
            price=signal.price,
            size=size,
            notional_usd=notional,
            external_tx=signal.external_tx,
        )
