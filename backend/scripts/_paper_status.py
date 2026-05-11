"""Pull a clean paper-mode status snapshot via the HTTP API."""
import httpx

from src.auth.jwt import create_access_token

BASE = "http://127.0.0.1:8000"
token = create_access_token(1)
h = {"Authorization": f"Bearer {token}"}
client = httpx.Client(timeout=15)

print("=== bot status ===")
print(client.get(f"{BASE}/bot/status", headers=h).text)
print("\n=== recent trades (5 newest) ===")
trades = client.get(f"{BASE}/trades?limit=5", headers=h).json()
for t in trades:
    print(f"  #{t['id']:>5}  {t['created_at']}  {t['mode']:<5}  {t['status']:<10}  {t['side']:<4} {t['size']:>10.4f} @ {t['price']:>6.4f}  notional=${t['notional_usd']:>7.2f}  market={t['market_id'][:18]}...")

print("\n=== stats ===")
print(client.get(f"{BASE}/stats", headers=h).text)
