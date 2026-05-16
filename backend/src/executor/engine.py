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

# Patch py-clob-client to use Polymarket's V2 exchange contracts BEFORE
# importing ClobClient — the V1 addresses hardcoded in the SDK get rejected
# by Polymarket's matching engine with order_version_mismatch since the
# 2026-04-28 exchange upgrade. See src/wallet/sdk_v2_compat.py.
from src.wallet import sdk_v2_compat  # noqa: F401 — import for side effect

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

    def _refuse_unless_v2_signing_ready(self) -> None:
        # py-clob-client 0.34.6 signs V1 orders; Polymarket migrated to V2 on
        # 2026-04-28. Submitting a V1-shaped order against V2 returns
        # `order_version_mismatch`. Until ExchangeOrderBuilderV2 is ported to
        # Python (see src/wallet/sdk_v2_compat.py and clob-client-v2 on GitHub),
        # refuse live orders even when the kill switch is on. Bot logs the
        # refusal as last_error and keeps polling; no money moves.
        raise ExecutionRefused(
            "py-clob-client SDK signs V1 orders; Polymarket requires V2 since "
            "2026-04-28. Going live is blocked until V2 signing is ported to "
            "Python. See src/wallet/sdk_v2_compat.py for status."
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
        # If the wallet has a proxy_address set, this is a Polymarket Magic Link
        # / email-signup user: the EOA signs orders but funds + approvals live
        # on the proxy contract. Use signature_type=1 (POLY_PROXY) and pass the
        # proxy as the funder. Otherwise treat as a self-funded EOA.
        if wallet.proxy_address:
            client = ClobClient(
                host=settings.polymarket_base_url,
                key=priv,
                chain_id=settings.polygon_chain_id,
                signature_type=1,  # POLY_PROXY (Magic Link / email)
                funder=wallet.proxy_address,
            )
            log.info(
                "CLOB client init proxy mode user=%s eoa=%s funder=%s",
                self.user_id, wallet.address, wallet.proxy_address,
            )
        else:
            client = ClobClient(
                host=settings.polymarket_base_url,
                key=priv,
                chain_id=settings.polygon_chain_id,
                signature_type=0,  # EOA — wallet is its own funder
            )
            log.info(
                "CLOB client init EOA mode user=%s address=%s",
                self.user_id, wallet.address,
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
        self._refuse_unless_v2_signing_ready()
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
            title=order.title,
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

        # Position is NOT updated here. The limit price is provisional and the
        # fill might be partial, better-priced, or never happen. reconcile_open_orders()
        # is the single source of truth for live Position rows; it polls actual fills
        # and replays Position from `filled`/`partial` trades. The bot loop calls
        # reconcile() before computing exposure each tick so risk sees up-to-date data,
        # and adds open-submitted notional on top to account for in-flight orders.
        self.db.commit()
        self.db.refresh(trade)
        log.info(
            "live-submit user=%s %s %s @%.4f size=%.4f order_id=%s",
            self.user_id, order.side, order.outcome,
            limit, order.size, order_id,
        )
        return trade

    # -- reconciliation -----------------------------------------------------

    @staticmethod
    def _coerce_float(value, default: float) -> float:
        try:
            return float(value) if value is not None else default
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _map_clob_status(clob_status: str, size_matched: float, ordered_size: float) -> str:
        clob_status = (clob_status or "").upper()
        if clob_status == "FILLED" or (ordered_size > 0 and size_matched >= ordered_size):
            return "filled"
        if clob_status == "MATCHED":
            # MATCHED means trade matched on the book but may not be settled yet.
            # Treat partial-match as partial; full-match as filled.
            return "filled" if size_matched >= ordered_size else "partial"
        if clob_status == "CANCELED":
            return "cancelled"
        if clob_status == "EXPIRED":
            return "expired"
        if size_matched > 0:
            return "partial"
        return "submitted"

    def _replay_position(self, market_id: str, outcome: str) -> None:
        """Rebuild the live Position row for (market, outcome) from filled trades.
        Idempotent — call after any Trade fill data changes."""
        trades = (
            self.db.query(Trade)
            .filter(
                Trade.user_id == self.user_id,
                Trade.market_id == market_id,
                Trade.outcome == outcome,
                Trade.mode == "live",
                Trade.status.in_(["filled", "partial"]),
            )
            .order_by(Trade.created_at.asc(), Trade.id.asc())
            .all()
        )

        pos = (
            self.db.query(Position)
            .filter(
                Position.user_id == self.user_id,
                Position.market_id == market_id,
                Position.outcome == outcome,
                Position.mode == "live",
            )
            .first()
        )
        if pos is None:
            pos = Position(
                user_id=self.user_id,
                market_id=market_id,
                outcome=outcome,
                mode="live",
            )
            self.db.add(pos)

        pos.size = 0.0
        pos.avg_price = 0.0
        pos.realized_pnl_usd = 0.0

        for t in trades:
            size_used = t.filled_size if t.filled_size is not None else t.size
            price_used = t.fill_price if t.fill_price is not None else t.price
            if size_used <= 0:
                continue
            if t.side == "buy":
                new_size = pos.size + size_used
                if new_size > 0:
                    pos.avg_price = (
                        pos.avg_price * pos.size + price_used * size_used
                    ) / new_size
                pos.size = new_size
            else:
                close_size = min(pos.size, size_used)
                pos.realized_pnl_usd += (price_used - pos.avg_price) * close_size
                pos.size = max(0.0, pos.size - size_used)

    def reconcile_open_orders(self) -> int:
        """Poll the CLOB for every open submitted/partial live trade, write
        actual fill_price/filled_size/status, and replay any Position rows that
        were touched. Returns the number of trade rows updated."""
        open_trades = (
            self.db.query(Trade)
            .filter(
                Trade.user_id == self.user_id,
                Trade.mode == "live",
                Trade.status.in_(["submitted", "partial"]),
                Trade.clob_order_id.isnot(None),
            )
            .all()
        )
        if not open_trades:
            return 0

        wallet = self._load_wallet()
        client = self._get_client(wallet)

        touched_keys: set[tuple[str, str]] = set()
        updated = 0

        for trade in open_trades:
            try:
                resp = client.get_order(trade.clob_order_id)
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "get_order failed user=%s clob=%s: %s",
                    self.user_id, trade.clob_order_id, e,
                )
                continue
            if not resp:
                continue

            clob_status = resp.get("status")
            size_matched = self._coerce_float(resp.get("size_matched"), 0.0)
            avg_fill_price = self._coerce_float(resp.get("price"), trade.price)
            new_status = self._map_clob_status(clob_status, size_matched, trade.size)

            changed = (
                trade.status != new_status
                or (trade.filled_size or 0.0) != size_matched
                or trade.fill_price != avg_fill_price
            )
            if not changed:
                continue

            trade.status = new_status
            trade.filled_size = size_matched
            trade.fill_price = avg_fill_price
            touched_keys.add((trade.market_id, trade.outcome))
            updated += 1
            log.info(
                "reconcile user=%s trade=%s %s %s filled=%.4f/%.4f avg=%.4f",
                self.user_id, trade.id, trade.outcome, new_status,
                size_matched, trade.size, avg_fill_price,
            )

        for market_id, outcome in touched_keys:
            self._replay_position(market_id, outcome)

        if updated:
            self.db.commit()
        return updated
