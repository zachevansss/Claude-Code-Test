"""Live execution engine. NEVER instantiated when MODE=paper.

Concrete implementation depends on the wallet/custody decision (open with the user):
  - non-custodial (user signs via WalletConnect)
  - custodial managed wallet (server holds keys)
The stub raises NotImplementedError so any accidental live-mode call fails loudly
rather than silently doing the wrong thing with real funds."""
from sqlalchemy.orm import Session

from src.models import Trade
from src.risk.manager import SizedOrder
from src.utils.logging import get_logger

log = get_logger("EXECUTION")


class ExecutionEngine:
    def __init__(self, db: Session, user_id: int) -> None:
        self.db = db
        self.user_id = user_id

    def execute(self, order: SizedOrder, source_wallet: str) -> Trade:
        log.error("live execution called but not implemented (user=%s)", self.user_id)
        raise NotImplementedError(
            "Live execution is not implemented. Resolve the wallet/custody model "
            "(non-custodial WalletConnect signing vs. managed wallet) before wiring up."
        )
