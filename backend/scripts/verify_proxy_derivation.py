"""Compute what proxy address SHOULD correspond to user_id=1's managed EOA
under Polymarket's ProxyWalletFactory CREATE2 derivation.

If the computed proxy matches the proxy_address in managed_wallets, the EOA
is the on-chain controller. If not, the EOA we have in the DB was never the
controller of that proxy — Polymarket's "Invalid L1 Request headers" error
is then expected and unfixable from our side.

Derivation (from Polymarket/proxy-factories ProxyWalletFactory.sol::makeWallet):
  salt = keccak256(abi.encodePacked(eoa))
  init_code = EIP-1167 minimal proxy creation code (delegates to IMPL)
  proxy   = keccak256(0xff || FACTORY || salt || keccak256(init_code))[12:]
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from eth_utils import keccak, to_checksum_address

from src.database.session import SessionLocal
from src.models import ManagedWallet


FACTORY = "0xab45c5a4b0c941a2f231c04c3f49182e1a254052"   # Polymarket: Proxy Wallet Factory
IMPL = "0x44e999d5c2f66ef0861317f9a4805ac2e90aeb4f"      # ProxyWallet implementation


def derive_proxy(eoa: str) -> str:
    init_code = bytes.fromhex(
        "3d602d80600a3d3981f3"
        "363d3d373d3d3d363d73" + IMPL[2:].lower() + "5af43d82803e903d91602b57fd5bf3"
    )
    salt = keccak(bytes.fromhex(eoa[2:].lower()))
    addr = keccak(
        b"\xff" + bytes.fromhex(FACTORY[2:].lower()) + salt + keccak(init_code)
    )[12:]
    return to_checksum_address("0x" + addr.hex())


def main() -> int:
    db = SessionLocal()
    try:
        w = db.query(ManagedWallet).filter(ManagedWallet.user_id == 1).first()
        if not w:
            print("no managed wallet for user_id=1"); return 1
    finally:
        db.close()

    eoa = w.address
    stored_proxy = w.proxy_address
    computed = derive_proxy(eoa)

    print(f"EOA in DB:       {eoa}")
    print(f"proxy in DB:     {stored_proxy or '(null)'}")
    print(f"computed proxy:  {computed}")
    if stored_proxy:
        match = computed.lower() == stored_proxy.lower()
        print(f"\nmatch: {match}")
        if match:
            print("  -> EOA IS the on-chain controller of this proxy.")
            print("     If L1 auth still fails, it's a Polymarket-DB issue (Magic Link signup may need to be re-completed).")
        else:
            print("  -> EOA is NOT the controller of this proxy. The PK we have in the DB")
            print("     doesn't correspond to the proxy address. Either:")
            print("     - The user exported a different Magic Link key than the one tied to this proxy")
            print("     - Or Magic Link uses an internal operator key that's never exported")
    return 0


if __name__ == "__main__":
    sys.exit(main())
