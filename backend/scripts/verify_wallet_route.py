"""Simulate the GET /wallet route end-to-end: read the managed_wallets row,
pick proxy_address if present, hit on-chain balance via a working RPC."""
import os, urllib3, requests

# Temporarily route through a working public RPC so we can verify even though
# the configured POLYGON_RPC_URL is currently rate-limited.
os.environ["POLYGON_RPC_URL"] = "https://polygon-bor-rpc.publicnode.com"
urllib3.disable_warnings()
_orig = requests.Session.send
def _patched(self, *a, **kw): kw["verify"] = False; return _orig(self, *a, **kw)
requests.Session.send = _patched

from src.database.session import SessionLocal
from src.models import ManagedWallet
from src.wallet.balances import get_balances

db = SessionLocal()
try:
    w = db.query(ManagedWallet).filter(ManagedWallet.user_id == 1).first()
    balance_addr = w.proxy_address or w.address
    print(f"user_id=1 address={w.address}")
    print(f"          proxy_address={w.proxy_address}")
    print(f"          balance_addr={balance_addr}  <- this is what the bot now queries")
    bal, matic, err = get_balances(balance_addr)
    print(f"\nGET /wallet would return:")
    print(f"  usdc_balance:  {bal}   (actually pUSD)")
    print(f"  matic_balance: {matic}")
    print(f"  balance_error: {err}")
finally:
    db.close()
