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
from src.models import BotInstance, Position, Trade, UserSettings, UserWallet
from src.risk.manager import RiskManager, RiskRejection
from src.simulation.engine import SimulationEngine
from src.tracker.poller import WalletTracker
from src.utils.logging import get_logger

log = get_logger("BOT_MANAGER")


def _today_utc_start() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).replace(tzinfo=None)


def _load_seen_tx(db: Session, user_id: int) -> set[str]:
    """Pre-seed dedupe set from Trade.external_tx rows for this user."""
    rows = (
        db.query(Trade.external_tx)
        .filter(Trade.user_id == user_id, Trade.external_tx.isnot(None))
        .all()
    )
    return {r[0] for r in rows if r[0]}


class BotManager:
    def __init__(self) -> None:
        self._tasks: dict[int, asyncio.Task] = {}
        self._trackers: dict[int, WalletTracker] = {}

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

            balance = user_settings.paper_balance_usd  # TODO: real balance for live mode
            exposure: dict[str, float] = {}
            for p in (
                db.query(Position)
                .filter(Position.user_id == user_id, Position.mode == user_settings.mode)
                .all()
            ):
                exposure[p.market_id] = (
                    exposure.get(p.market_id, 0.0) + p.size * p.avg_price
                )

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

            risk = RiskManager(user_settings, balance, exposure, daily_loss)
            if user_settings.mode == "paper":
                engine: SimulationEngine | ExecutionEngine = SimulationEngine(db, user_id)
            else:
                engine = ExecutionEngine(db, user_id)
                engine.set_slippage(user_settings.slippage_tolerance_pct)

            for sig in fresh:
                try:
                    order = risk.size(sig)
                    engine.execute(order, source_wallet=sig.source_wallet)
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
