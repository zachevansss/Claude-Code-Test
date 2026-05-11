"""Mint a JWT for user_id=1 and hit GET /wallet directly. Proves the
HTTP layer is wired correctly end-to-end (not just unit-test level)."""
import sys
import time

import httpx

from src.auth.jwt import create_access_token


def wait_for_server(url: str, attempts: int = 20) -> None:
    for _ in range(attempts):
        try:
            r = httpx.get(url, timeout=1)
            if r.status_code in (200, 401, 403):
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise SystemExit(f"server never came up at {url}")


def main() -> None:
    base = "http://127.0.0.1:8000"
    wait_for_server(f"{base}/docs")
    token = create_access_token(1)
    r = httpx.get(f"{base}/wallet", headers={"Authorization": f"Bearer {token}"}, timeout=15)
    print(f"GET /wallet  HTTP {r.status_code}")
    print(r.text)
    sys.exit(0 if r.status_code == 200 else 1)


if __name__ == "__main__":
    main()
