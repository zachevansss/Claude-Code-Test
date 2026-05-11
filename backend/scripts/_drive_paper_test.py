"""Drive a paper-mode end-to-end test:
  1. Add the given trader as a tracked wallet
  2. Start the bot
  3. Print bot status + recent paper trades

Usage:
    python scripts/_drive_paper_test.py <0xSOURCE_WALLET>
"""
import sys
import time

import httpx

from src.auth.jwt import create_access_token


BASE = "http://127.0.0.1:8000"


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: _drive_paper_test.py <0xSOURCE_WALLET>")
        sys.exit(1)
    addr = sys.argv[1].lower()

    token = create_access_token(1)
    h = {"Authorization": f"Bearer {token}"}
    client = httpx.Client(timeout=15)

    # 1. Add source wallet (ignore "already tracked" 400)
    r = client.post(f"{BASE}/wallets/add", json={"address": addr, "label": "paper-test"}, headers=h)
    print(f"POST /wallets/add  HTTP {r.status_code}  {r.text}")

    # 2. List tracked wallets
    r = client.get(f"{BASE}/wallets", headers=h)
    print(f"\nGET /wallets  HTTP {r.status_code}\n  {r.text}")

    # 3. Confirm mode is paper
    r = client.get(f"{BASE}/settings", headers=h)
    print(f"\nGET /settings  HTTP {r.status_code}\n  mode={r.json().get('mode')}  paper_balance={r.json().get('paper_balance_usd')}")

    # 4. Start bot
    r = client.post(f"{BASE}/bot/start", headers=h)
    print(f"\nPOST /bot/start  HTTP {r.status_code}\n  {r.text}")

    # 5. Show status
    r = client.get(f"{BASE}/bot/status", headers=h)
    print(f"\nGET /bot/status  HTTP {r.status_code}\n  {r.text}")

    # 6. Wait one poll interval, then show any trades
    print(f"\nWaiting 10 seconds for tracker poll (interval ~5s)...")
    time.sleep(10)
    r = client.get(f"{BASE}/data/trades?limit=20", headers=h)
    trades = r.json() if r.status_code == 200 else []
    print(f"\nGET /data/trades  HTTP {r.status_code}  count={len(trades) if isinstance(trades, list) else 'n/a'}")
    if isinstance(trades, list) and trades:
        for t in trades[:5]:
            print(f"  trade id={t.get('id')} mode={t.get('mode')} status={t.get('status')} market={t.get('market_id')[:20]}... side={t.get('side')} price={t.get('price')} size={t.get('size')}")
    else:
        print("  (no trades yet — expected on first iteration; the tracker seeds _seen before emitting)")


if __name__ == "__main__":
    main()
