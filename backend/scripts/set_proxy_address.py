"""One-shot: set the Polymarket proxy address on a user's managed wallet row.

Use when a user signed up before Magic Link / proxy-wallet support existed and
their managed_wallets row has proxy_address = NULL even though they actually
fund through a Polymarket proxy. Idempotent: re-running with the same address
is a no-op; refuses to overwrite a different existing proxy.

Usage:
    python scripts/set_proxy_address.py <user_id> <0xPROXY>
"""
import sqlite3
import sys

from web3 import Web3


def main() -> None:
    if len(sys.argv) != 3:
        print("usage: set_proxy_address.py <user_id> <0xPROXY>")
        sys.exit(1)
    user_id = int(sys.argv[1])
    proxy = Web3.to_checksum_address(sys.argv[2])

    con = sqlite3.connect("copytrade.db")
    con.execute("BEGIN")
    try:
        row = con.execute(
            "SELECT user_id, address, proxy_address FROM managed_wallets WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            raise SystemExit(f"no managed_wallets row for user_id={user_id}")
        print(f"BEFORE: user_id={row[0]} address={row[1]} proxy_address={row[2]!r}")

        if row[2] is not None and row[2].lower() != proxy.lower():
            raise SystemExit(
                f"refusing to overwrite proxy_address={row[2]!r} with {proxy!r}. "
                "Edit the DB by hand if this swap is really intended."
            )

        con.execute(
            "UPDATE managed_wallets SET proxy_address = ? WHERE user_id = ?",
            (proxy, user_id),
        )
        after = con.execute(
            "SELECT user_id, address, proxy_address FROM managed_wallets WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        print(f"AFTER:  user_id={after[0]} address={after[1]} proxy_address={after[2]!r}")
        con.commit()
        print("committed")
    except Exception:
        con.rollback()
        raise


if __name__ == "__main__":
    main()
