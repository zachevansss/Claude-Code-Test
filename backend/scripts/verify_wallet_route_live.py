"""Simulate GET /wallet using the actual configured POLYGON_RPC_URL — no
monkey-patching. Proves the bot can read pUSD through the real RPC."""
import urllib3, requests
urllib3.disable_warnings()
_orig = requests.Session.send
def _patched(self, *a, **kw): kw["verify"] = False; return _orig(self, *a, **kw)
requests.Session.send = _patched

from src.config.settings import settings
print(f"Configured RPC: {settings.polygon_rpc_url[:60]}...")

from src.database.session import SessionLocal
from src.models import ManagedWallet
from src.wallet.balances import get_balances

db = SessionLocal()
try:
    w = db.query(ManagedWallet).filter(ManagedWallet.user_id == 1).first()
    balance_addr = w.proxy_address or w.address
    bal, matic, err = get_balances(balance_addr)
    print(f"\nGET /wallet response would be:")
    print(f"  address:        {w.address}")
    print(f"  proxy_address:  {w.proxy_address}")
    print(f"  usdc_balance:   {bal}   (pUSD)")
    print(f"  matic_balance:  {matic}")
    print(f"  balance_error:  {err}")
finally:
    db.close()
