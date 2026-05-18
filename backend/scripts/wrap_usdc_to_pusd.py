"""One-shot: wrap the EOA's native USDC into Polymarket pUSD.

Steps:
  1. Read EOA's native USDC balance and MATIC (gas).
  2. If allowance(USDC -> pUSD contract) is below the balance, approve max.
  3. Call pUSD.wrap(USDC, EOA, balance, address(0), 0x) to convert 1:1.
  4. Print resulting pUSD balance.

Run on the VPS (where the encrypted private key lives), as the bot user.
Set --user-id 1 (default) and --confirm to actually broadcast; without
--confirm it dry-runs and prints what it would do.

NOT idempotent in the sense that a second run will wrap any remaining USDC
to pUSD again — but since the first run wraps the full balance, a second
run will be a no-op (zero balance, nothing to wrap).
"""
import argparse
import sys
import time

from web3 import Web3

# allow running as `python -m scripts.wrap_usdc_to_pusd` from /backend
sys.path.insert(0, ".")

from src.config.settings import settings
from src.database.session import SessionLocal
from src.models import ManagedWallet
from src.wallet.manager import WalletManager


USDC_NATIVE = Web3.to_checksum_address("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359")
PUSD = Web3.to_checksum_address("0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB")
ZERO = "0x0000000000000000000000000000000000000000"

ERC20_ABI = [
    {"constant": True, "inputs": [{"name": "owner", "type": "address"}], "name": "balanceOf",
     "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
    {"constant": False, "inputs": [{"name": "spender", "type": "address"},
                                    {"name": "value", "type": "uint256"}],
     "name": "approve", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
    {"constant": True, "inputs": [{"name": "owner", "type": "address"},
                                   {"name": "spender", "type": "address"}],
     "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
]

PUSD_ABI = [
    {"inputs": [{"name": "_asset", "type": "address"},
                {"name": "_to", "type": "address"},
                {"name": "_amount", "type": "uint256"},
                {"name": "_callbackReceiver", "type": "address"},
                {"name": "_data", "type": "bytes"}],
     "name": "wrap", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument("--confirm", action="store_true", help="actually broadcast")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        wallet = (
            db.query(ManagedWallet)
            .filter(ManagedWallet.user_id == args.user_id)
            .first()
        )
        if not wallet:
            print(f"no managed wallet for user_id={args.user_id}")
            return 1
        signer = WalletManager.get_signer(wallet)
        eoa = signer.address
    finally:
        db.close()

    w3 = Web3(Web3.HTTPProvider(settings.polygon_rpc_url, request_kwargs={"timeout": 15}))
    usdc = w3.eth.contract(address=USDC_NATIVE, abi=ERC20_ABI)
    pusd = w3.eth.contract(address=PUSD, abi=ERC20_ABI + PUSD_ABI)

    usdc_bal = usdc.functions.balanceOf(eoa).call()
    pusd_bal_before = pusd.functions.balanceOf(eoa).call()
    matic = w3.eth.get_balance(eoa)
    allowance = usdc.functions.allowance(eoa, PUSD).call()

    print(f"EOA:            {eoa}")
    print(f"USDC balance:   {usdc_bal / 1e6:,.6f} ({usdc_bal} units)")
    print(f"pUSD balance:   {pusd_bal_before / 1e6:,.6f} (before)")
    print(f"MATIC balance:  {matic / 1e18:,.6f}")
    print(f"USDC->pUSD allowance: {allowance}")

    if usdc_bal == 0:
        print("\nUSDC balance is 0 — nothing to wrap.")
        return 0
    if matic < 5_000_000_000_000_000:  # 0.005 MATIC
        print("\nWARNING: MATIC < 0.005 — gas may be insufficient.")

    needs_approve = allowance < usdc_bal
    chain_id = settings.polygon_chain_id

    if not args.confirm:
        print("\n--- DRY RUN (no --confirm) ---")
        if needs_approve:
            print(f"Would: USDC.approve({PUSD}, max)")
        print(f"Would: pUSD.wrap(USDC, {eoa}, {usdc_bal}, 0, 0x)")
        print(f"\nRe-run with --confirm to broadcast.")
        return 0

    nonce = w3.eth.get_transaction_count(eoa)
    gas_price = w3.eth.gas_price
    print(f"\nGas price: {gas_price / 1e9:.2f} gwei | starting nonce: {nonce}")

    if needs_approve:
        print("\n[1/2] Approving USDC -> pUSD contract (max)...")
        tx = usdc.functions.approve(PUSD, 2**256 - 1).build_transaction({
            "from": eoa, "nonce": nonce, "gas": 100_000,
            "gasPrice": gas_price, "chainId": chain_id,
        })
        signed = signer.sign_transaction(tx)
        raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
        h = w3.eth.send_raw_transaction(raw)
        print(f"  approve tx: {h.hex()}")
        rcpt = w3.eth.wait_for_transaction_receipt(h, timeout=180)
        print(f"  approve status: {rcpt.status} (block {rcpt.blockNumber})")
        if rcpt.status != 1:
            print("approve FAILED, aborting"); return 2
        nonce += 1
    else:
        print("\n[1/2] Allowance already sufficient — skipping approve.")

    print("\n[2/2] Wrapping USDC -> pUSD ...")
    tx = pusd.functions.wrap(USDC_NATIVE, eoa, usdc_bal, ZERO, b"").build_transaction({
        "from": eoa, "nonce": nonce, "gas": 250_000,
        "gasPrice": gas_price, "chainId": chain_id,
    })
    signed = signer.sign_transaction(tx)
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
    h = w3.eth.send_raw_transaction(raw)
    print(f"  wrap tx: {h.hex()}")
    rcpt = w3.eth.wait_for_transaction_receipt(h, timeout=180)
    print(f"  wrap status: {rcpt.status} (block {rcpt.blockNumber}, gas {rcpt.gasUsed})")
    if rcpt.status != 1:
        print("wrap FAILED"); return 3

    time.sleep(2)
    usdc_after = usdc.functions.balanceOf(eoa).call()
    pusd_after = pusd.functions.balanceOf(eoa).call()
    print(f"\nFinal: USDC {usdc_after / 1e6:,.6f}  |  pUSD {pusd_after / 1e6:,.6f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
