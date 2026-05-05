"""BotManager — owns one asyncio loop per user with isolated state.

Lifecycle:
  start(user_id)   → spawn loop task + init persistent tracker
  stop(user_id)    → cancel and await; drop tracker
  restart_all()    → on server boot, restart any bot whose DB row says 'running'
  stop_all()       → cancel every running task (used on shutdown)

Each tick: load settings + wallets, sync tracker addresses, poll for new signals,
dedupe vs DB, run risk, dispatch to SimulationEngine (paper) or ExecutionEngine
(live, currently NotImplementedError)."""
import asyncio
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from src.config.settings import settings
from src.database.session import SessionLocal
from src.executor.engine import ExecutionEngine, ExecutionRefused
from src.models import BotInstance, ManagedWallet, Position, Trade, UserSettings, UserWallet
from src.resolution.checker import check_resolutions
from src.risk.manager import RiskManager, RiskRejection
from src.simulation.engine import SimulationEngine
from src.tracker.poller import WalletTracker
from src.utils.logging import get_logger
from src.wallet.balances import get_usdc_balance

log = get_logger("BOT_MANAGER")


def _today_utc_start() -> datetime:
    """Start of today in the system's *local* time, expressed as a naive UTC
    datetime so it can be compared against Position.updated_at (which is
    stored via datetime.utcnow()). Honors the operator's local trading day —
    if you're in Central, the daily-loss window resets at midnight Central,
    not midnight UTC."""
    local_now = datetime.now().astimezone()
    local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight.astimezone(timezone.utc).replace(tzinfo=None)


def _load_seen_tx(db: Session, user_id: int) -> set[str]:
    """Pre-seed dedupe set from Trade.external_tx rows for this user."""
    rows = (
        db.query(Trade.external_tx)
        .filter(Trade.user_id == user_id, Trade.external_tx.isnot(None))
        .all()
    )
    return {r[0] for r in rows if r[0]}


def _paper_balance(db: Session, user_id: int, starting_bankroll: float) -> float:
    """Available paper cash = starting bankroll - capital tied up in open
    positions (size * avg_price) + realized PnL on closes. Goes up and down
    as trades fill so the bot can't spend money it doesn't have."""
    committed = 0.0
    realized = 0.0
    for p in (
        db.query(Position)
        .filter(Position.user_id == user_id, Position.mode == "paper")
        .all()
    ):
        committed += p.size * p.avg_price
        realized += p.realized_pnl_usd
    return max(0.0, starting_bankroll - committed + realized)


RESOLUTION_INTERVAL_SECONDS = 60


class BotManager:
    def __init__(self) -> None:
        self._tasks: dict[int, asyncio.Task] = {}
        self._trackers: dict[int, WalletTracker] = {}
        # Per-user timestamp of the last resolution sweep. Throttles the
        # checker so we don't spam Polymarket every 5-second tick.
        self._last_resolution: dict[int, float] = {}

    async def start(self, user_id: int) -> None:
        existing = self._tasks.get(user_id)
        if existing and not existing.done():
            log.info("bot already running for user=%s", user_id)
            return
        task = asyncio.create_task(self._run(user_id), name=f"bot-{user_id}")
        self._tasks[user_id] = task
        log.info("started bot for user=%s", user_id)

    async def stop(self, user_id: int) -> None:
        task = self._tasks.pop(user_id, None)
        self._trackers.pop(user_id, None)
        if not task:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.exception("error while stopping bot user=%s: %s", user_id, e)
        log.info("stopped bot for user=%s", user_id)

    async def stop_all(self) -> None:
        for user_id in list(self._tasks):
            await self.stop(user_id)

    async def restart_all(self) -> None:
        with SessionLocal() as db:
            running = db.query(BotInstance).filter(BotInstance.status == "running").all()
            user_ids = [r.user_id for r in running]
        for uid in user_ids:
            await self.start(uid)
        log.info("restarted %d bots from DB state", len(user_ids))

    async def _run(self, user_id: int) -> None:
        log.info("bot loop entered for user=%s", user_id)
        try:
            while True:
                try:
                    await self._tick(user_id)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.exception("tick error user=%s: %s", user_id, e)
                    self._record_error(user_id, str(e))
                await asyncio.sleep(settings.bot_poll_interval_seconds)
        except asyncio.CancelledError:
            log.info("bot loop cancelled for user=%s", user_id)
            raise

    def _get_tracker(
        self, db: Session, user_id: int, addresses: list[str]
    ) -> WalletTracker:
        """Persistent per-user tracker. On first creation, pre-seed `_seen` from
        prior trades in DB so a restart doesn't re-emit historical signals."""
        tracker = self._trackers.get(user_id)
        if tracker is None:
            seen = _load_seen_tx(db, user_id)
            tracker = WalletTracker(
                addresses, seen=seen, initialized=bool(seen)
            )
            self._trackers[user_id] = tracker
            log.info(
                "tracker created for user=%s with %d pre-seeded tx",
                user_id, len(seen),
            )
        else:
            tracker.update_addresses(addresses)
        return tracker

    async def _tick(self, user_id: int) -> None:
        with SessionLocal() as db:
            user_settings = (
                db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
            )
            if not user_settings:
                return

            # Once per RESOLUTION_INTERVAL_SECONDS, sweep open positions and
            # close any whose markets have resolved. Source wallet doesn't sell
            # winners (they redeem on-chain), so without this our positions
            # stay open forever and never realize PnL.
            now = asyncio.get_event_loop().time()
            last = self._last_resolution.get(user_id, 0.0)
            if now - last >= RESOLUTION_INTERVAL_SECONDS:
                self._last_resolution[user_id] = now
                try:
                    closed = check_resolutions(db, user_id, mode=user_settings.mode)
                    if closed:
                        log.info("auto-resolved %d positions for user=%s", closed, user_id)
                except Exception as e:  # noqa: BLE001
                    log.warning("resolution sweep failed user=%s: %s", user_id, e)
            wallets = (
                db.query(UserWallet)
                .filter(UserWallet.user_id == user_id, UserWallet.is_active == True)  # noqa: E712
                .all()
            )
            if not wallets:
                return

            tracker = self._get_tracker(db, user_id, [w.address for w in wallets])
            signals = await tracker.poll()
            if not signals:
                return

            # DB-level dedupe — final safety net against double execution.
            tx_to_check = [s.external_tx for s in signals if s.external_tx]
            already_seen: set[str] = set()
            if tx_to_check:
                rows = (
                    db.query(Trade.external_tx)
                    .filter(
                        Trade.user_id == user_id,
                        Trade.external_tx.in_(tx_to_check),
                    )
                    .all()
                )
                already_seen = {r[0] for r in rows}

            fresh = [s for s in signals if s.external_tx not in already_seen]
            if not fresh:
                return

            # Reconcile any open live orders against the CLOB before computing
            # exposure or sizing new orders. Failures here are non-fatal — we
            # log and proceed; next tick will retry.
            live_engine: ExecutionEngine | None = None
            if user_settings.mode == "live":
                live_engine = ExecutionEngine(db, user_id)
                try:
                    live_engine.reconcile_open_orders()
                except ExecutionRefused as e:
                    # Safety gate (e.g. no managed wallet). Skip this tick.
                    log.warning("reconcile refused user=%s: %s", user_id, e)
                    return
                except Exception as e:  # noqa: BLE001
                    log.warning("reconcile error user=%s: %s — continuing tick", user_id, e)

            if user_settings.mode == "paper":
                balance = _paper_balance(
                    db, user_id, user_settings.paper_balance_usd
                )
            else:
                managed = (
                    db.query(ManagedWallet)
                    .filter(ManagedWallet.user_id == user_id)
                    .first()
                )
                if not managed:
                    log.warning("user=%s in live mode has no managed wallet — skipping tick", user_id)
                    return
                live_balance = get_usdc_balance(managed.address)
                if live_balance is None:
                    log.warning(
                        "user=%s live balance lookup failed — skipping tick (will retry next interval)",
                        user_id,
                    )
                    return
                balance = live_balance

            exposure: dict[str, float] = {}
            for p in (
                db.query(Position)
                .filter(Position.user_id == user_id, Position.mode == user_settings.mode)
                .all()
            ):
                exposure[p.market_id] = (
                    exposure.get(p.market_id, 0.0) + p.size * p.avg_price
                )

            # In live mode, also reserve notional for in-flight orders that
            # haven't filled yet. Two distinct effects:
            #   1. add unfilled notional to per-market exposure → per-market cap
            #      can't be blown by stacked orders before the first fill lands
            #   2. subtract total in-flight from balance → base sizing % shrinks
            #      as concurrent orders accumulate, so a high-frequency source
            #      doesn't exhaust the wallet by submitting hundreds in parallel
            in_flight_total = 0.0
            if user_settings.mode == "live":
                open_trades = (
                    db.query(Trade)
                    .filter(
                        Trade.user_id == user_id,
                        Trade.mode == "live",
                        Trade.status.in_(["submitted", "partial"]),
                    )
                    .all()
                )
                for t in open_trades:
                    filled = t.filled_size or 0.0
                    unfilled = max(0.0, t.size - filled)
                    if unfilled > 0:
                        unfilled_notional = unfilled * t.price
                        exposure[t.market_id] = (
                            exposure.get(t.market_id, 0.0) + unfilled_notional
                        )
                        in_flight_total += unfilled_notional
                balance = max(0.0, balance - in_flight_total)

            since = _today_utc_start()
            todays_realized = sum(
                p.realized_pnl_usd
                for p in db.query(Position).filter(
                    Position.user_id == user_id,
                    Position.mode == user_settings.mode,
                    Position.updated_at >= since,
                )
            )
            daily_loss = max(0.0, -todays_realized)

            # account_value = available cash + cost basis of open positions.
            # For paper that's just starting_bankroll + total_realized.
            # For live we approximate with balance (USDC) + in-flight + position
            # cost basis (already summed via exposure dict).
            account_value = balance + sum(exposure.values())
            risk = RiskManager(user_settings, balance, exposure, daily_loss, account_value)
            if user_settings.mode == "paper":
                engine: SimulationEngine | ExecutionEngine = SimulationEngine(db, user_id)
            else:
                # Reuse the engine instance from reconcile so the CLOB client +
                # API creds stay cached across the tick.
                assert live_engine is not None  # mode == "live" branch above always set this
                engine = live_engine
                engine.set_slippage(user_settings.slippage_tolerance_pct)

            for sig in fresh:
                try:
                    order = risk.size(sig)
                    engine.execute(order, source_wallet=sig.source_wallet)
                    # Refresh per-market exposure and available balance so
                    # subsequent signals in this tick see post-fill state.
                    # Buy: commits cash, adds to exposure. Sell: returns cash,
                    # reduces exposure (we approximate the sell delta as the
                    # full notional; the next tick reads exact state from DB).
                    if order.side == "buy":
                        exposure[order.market_id] = (
                            exposure.get(order.market_id, 0.0) + order.notional_usd
                        )
                        risk.balance_usd = max(0.0, risk.balance_usd - order.notional_usd)
                    else:  # sell
                        exposure[order.market_id] = max(
                            0.0,
                            exposure.get(order.market_id, 0.0) - order.notional_usd,
                        )
                        risk.balance_usd += order.notional_usd
                except RiskRejection as e:
                    log.info("risk rejected user=%s: %s", user_id, e)
                except ExecutionRefused as e:
                    # Safety gate triggered — log and stop processing further
                    # signals this tick. Not an error; a planned stop.
                    log.warning("execution refused user=%s: %s", user_id, e)
                    return

    def _record_error(self, user_id: int, msg: str) -> None:
        with SessionLocal() as db:
            inst = db.query(BotInstance).filter(BotInstance.user_id == user_id).first()
            if inst:
                inst.last_error = msg[:512]
                db.commit()


bot_manager = BotManager()
