"""Risk management — sizing strategies and per-trade / per-market / daily caps."""
from dataclasses import dataclass

from src.utils.logging import get_logger

log = get_logger("RISK")


@dataclass
class TradeSignal:
    """Output of the wallet tracker; input to risk."""
    source_wallet: str
    market_id: str          # Polymarket conditionId
    outcome: str            # human-readable outcome label
    side: str               # "buy" | "sell"
    price: float
    size: float             # source-wallet size (units, not USD)
    external_tx: str | None = None   # source-wallet tx hash; used for dedupe
    asset_id: str | None = None      # ERC-1155 token id; CLOB orders need this


@dataclass
class SizedOrder:
    """A signal post-risk: notional and size are what the engine should execute."""
    market_id: str
    outcome: str
    side: str
    price: float
    size: float
    notional_usd: float
    external_tx: str | None = None
    asset_id: str | None = None


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
        account_value_usd: float | None = None,
    ) -> None:
        self.settings = settings
        self.balance_usd = balance_usd
        self.exposure_by_market_usd = exposure_by_market_usd
        self.daily_loss_usd = daily_loss_usd
        # Total account value (cash + cost basis of open positions). Used as
        # the baseline for daily-loss-cap %. If not provided, falls back to
        # balance_usd for backwards compat.
        self.account_value_usd = account_value_usd if account_value_usd is not None else balance_usd

    def size(self, signal: TradeSignal) -> SizedOrder:
        # Daily loss cap as a % of total account value (not just available
        # cash) — scales up when you've banked profits, down after losses.
        daily_loss_cap = self.account_value_usd * (self.settings.daily_loss_cap_pct / 100.0)
        if self.daily_loss_usd >= daily_loss_cap:
            raise RiskRejection(
                f"daily loss cap reached "
                f"({self.daily_loss_usd:.2f} >= {daily_loss_cap:.2f}, "
                f"{self.settings.daily_loss_cap_pct:.1f}% of account)"
            )

        # Total portfolio leverage cap. Rejects any new order once the sum
        # of open-position cost exceeds X% of account value. Source's
        # high-frequency activity can otherwise stack hundreds of $1.50
        # fills on a small bankroll into 60-90% deployment.
        total_committed = sum(self.exposure_by_market_usd.values())
        leverage_cap = self.account_value_usd * (self.settings.max_total_leverage_pct / 100.0)
        if total_committed >= leverage_cap:
            raise RiskRejection(
                f"total leverage cap reached "
                f"({total_committed:.2f} >= {leverage_cap:.2f}, "
                f"{self.settings.max_total_leverage_pct:.1f}% of account)"
            )

        if signal.price <= 0:
            raise RiskRejection(f"non-positive signal price: {signal.price}")
        source_notional = signal.price * signal.size

        # 1. Base sizing strategy.
        strategy = self.settings.sizing_strategy
        if strategy == "percent":
            notional = self.balance_usd * (self.settings.sizing_percent / 100.0)
        elif strategy == "fixed":
            notional = self.settings.sizing_fixed_usd
        elif strategy == "mirror":
            notional = source_notional * self.settings.mirror_scale
            min_floor = self.settings.min_trade_usd
            if notional < min_floor:
                raise RiskRejection(
                    f"mirror size {notional:.4f} < min_trade_usd {min_floor:.2f} — skipping"
                )
        else:
            raise RiskRejection(f"unknown sizing_strategy: {strategy!r}")

        # 2. Cap by max % per trade. Computed against balance (already net of
        # in-flight in BotManager) so concurrent orders self-throttle.
        max_per_trade = self.balance_usd * (self.settings.max_percent_per_trade / 100.0)
        notional = min(notional, max_per_trade)

        # 3. Cap by remaining exposure budget on this market. The cap is a
        # fraction of *current* balance so it tracks the bankroll up and down.
        market_cap = self.balance_usd * (self.settings.max_exposure_per_market_pct / 100.0)
        used = self.exposure_by_market_usd.get(signal.market_id, 0.0)
        remaining = max(0.0, market_cap - used)
        notional = min(notional, remaining)

        if notional <= 0:
            raise RiskRejection("zero or negative notional after caps")

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
            asset_id=signal.asset_id,
        )
