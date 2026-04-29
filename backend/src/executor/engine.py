"""Live execution via Polymarket CLOB.

Three independent safety gates — ALL must pass before an order is placed:
  1. settings.live_trading_enabled is True (global kill switch)
  2. The user has a managed wallet
  3. The order carries an asset_id (Polymarket token id) — proves it came from
     a real tracked source-wallet fill, not synthesized somewhere upstream

A failed order is not a crashed bot. ExecutionEngine raises on failure;
BotManager catches, persists last_error to BotInstance, and continues.

Concurrency note: py-clob-client is sync. The bot loop is async. Each .execute()
call briefly blocks the event loop while it signs and posts. For a handful of
users this is fine; if you scale past ~50 concurrent bots, wrap calls with
asyncio.to_thread() to free the loop."""
import time
from datetime import datetime

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL
from sqlalchemy.orm import Session

from src.config.settings import settings
from src.models import ManagedWallet, Position, Trade
from src.risk.manager import SizedOrder
from src.utils.logging import get_logger
from src.wallet.manager import WalletManager

log = get_logger("EXECUTION")


class ExecutionRefused(RuntimeError):
    """A safety gate refused this order. Distinct from a network/CLOB failure."""


class ExecutionEngine:
    def __init__(self, db: Session, user_id: int) -> None:
        self.db = db
        self.user_id = user_id
        self._client: ClobClient | None = None
        self._slippage_pct: float = 1.0  # populated per-tick by BotManager via UserSettings

    def set_slippage(self, pct: float) -> None:
        self._slippage_pct = pct

    # -- safety gates -------------------------------------------------------

    def _refuse_unless_armed(self) -> None:
        if not settings.live_trading_enabled:
            raise ExecutionRefused(
                "LIVE_TRADING_ENABLED kill switch is OFF — no live orders."
            )

    def _load_wallet(self) -> ManagedWallet:
        wallet = (
            self.db.query(ManagedWallet)
            .filter(ManagedWallet.user_id == self.user_id)
            .first()
        )
        if not wallet:
            raise ExecutionRefused(f"user={self.user_id} has no managed wallet")
        return wallet

    # -- CLOB client (lazy, per-engine-instance) ----------------------------

    def _get_client(self, wallet: ManagedWallet) -> ClobClient:
        if self._client is not None:
            return self._client
        priv = WalletManager.get_private_key_hex(wallet)
        client = ClobClient(
            host=settings.polymarket_base_url,
            key=priv,
            chain_id=settings.polygon_chain_id,
            signature_type=0,  # EOA — wallet is its own funder
        )
        # API creds are derived deterministically from the wallet key the first
        # time and re-derived thereafter — no separate registration call needed
        # for each restart.
        client.set_api_creds(client.create_or_derive_api_creds())
        self._client = client
        return client

    # -- order placement ----------------------------------------------------

    def _limit_price(self, order: SizedOrder) -> float:
        """Apply slippage tolerance to widen the limit. Buyer pays up to
        price*(1+slip), seller accepts down to price*(1-slip). Clamp to (0, 1)
        — Polymarket prices are probabilities."""
        slip = self._slippage_pct / 100.0
        if order.side == "buy":
            limit = order.price * (1.0 + slip)
        else:
            limit = order.price * (1.0 - slip)
        return max(0.001, min(0.999, limit))

    def execute(self, order: SizedOrder, source_wallet: str) -> Trade:
        self._refuse_unless_armed()
        if not order.asset_id:
            raise ExecutionRefused("order missing asset_id — refusing live placement")
        if not order.external_tx:
            raise ExecutionRefused("order missing external_tx — refusing live placement")

        wallet = self._load_wallet()
        client = self._get_client(wallet)

        limit = self._limit_price(order)
        args = OrderArgs(
            price=limit,
            size=order.size,
            side=BUY if order.side == "buy" else SELL,
            token_id=order.asset_id,
        )

        last_err: Exception | None = None
        order_id: str | None = None
        for attempt in range(1, settings.execution_max_retries + 1):
            try:
                signed = client.create_order(args)
                resp = client.post_order(signed, OrderType.GTC)
                order_id = (
                    resp.get("orderID")
                    or resp.get("order_id")
                    or resp.get("orderHash")
                )
                if not resp.get("success", True) and "errorMsg" in resp:
                    raise RuntimeError(f"CLOB rejected order: {resp.get('errorMsg')}")
                last_err = None
                break
            except Exception as e:  # noqa: BLE001 — bubble up after retries
                last_err = e
                log.warning(
                    "execute attempt %d/%d failed user=%s: %s",
                    attempt, settings.execution_max_retries, self.user_id, e,
                )
                if attempt < settings.execution_max_retries:
                    time.sleep(settings.execution_retry_backoff_seconds * attempt)

        if last_err is not None:
            raise last_err

        # Persist the trade. We use the source-wallet tx as external_tx for
        # cross-tick dedupe. The CLOB order id is stored separately so the
        # reconciler can poll it without parsing strings.
        trade = Trade(
            user_id=self.user_id,
            source_wallet=source_wallet,
            market_id=order.market_id,
            asset_id=order.asset_id,
            outcome=order.outcome,
            side=order.side,
            price=limit,            # the price we actually offered, not the source price
            size=order.size,
            notional_usd=limit * order.size,
            mode="live",
            status="submitted",
            external_tx=order.external_tx,
            clob_order_id=order_id,
            created_at=datetime.utcnow(),
        )
        self.db.add(trade)

        # Update position book the same way simulation does. Note: `price` here
        # is the limit we offered, not the actual fill price — refine when we
        # poll order status post-submission to reconcile against fills.
        pos = (
            self.db.query(Position)
            .filter(
                Position.user_id == self.user_id,
                Position.market_id == order.market_id,
                Position.outcome == order.outcome,
                Position.mode == "live",
            )
            .first()
        )
        if pos is None:
            pos = Position(
                user_id=self.user_id,
                market_id=order.market_id,
                outcome=order.outcome,
                mode="live",
            )
            self.db.add(pos)

        if order.side == "buy":
            new_size = pos.size + order.size
            if new_size > 0:
                pos.avg_price = (
                    pos.avg_price * pos.size + limit * order.size
                ) / new_size
            pos.size = new_size
        else:
            close_size = min(pos.size, order.size)
            pos.realized_pnl_usd += (limit - pos.avg_price) * close_size
            pos.size = max(0.0, pos.size - order.size)

        self.db.commit()
        self.db.refresh(trade)
        log.info(
            "live-submit user=%s %s %s @%.4f size=%.4f order_id=%s",
            self.user_id, order.side, order.outcome,
            limit, order.size, order_id,
        )
        return trade
