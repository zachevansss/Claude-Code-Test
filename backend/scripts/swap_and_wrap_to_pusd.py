"""Convert the EOA's native USDC into Polymarket pUSD.

Native USDC is paused on Polymarket's CollateralOnramp, but USDC.e is active.
Pipeline:
  (a) approve USDC -> Uniswap V3 SwapRouter, then swap USDC -> USDC.e (0.01% fee tier)
  (b) approve USDC.e -> CollateralOnramp, then wrap USDC.e -> pUSD 1:1

Each step skips itself if already done (allowance already set, balance already zero).

Run on the VPS as: `python -m scripts.swap_and_wrap_to_pusd --user-id 1`
to dry-run, then add `--confirm` to broadcast.
"""
import argparse
import sys
import time

from web3 import Web3

sys.path.insert(0, ".")

from src.config.settings import settings
from src.database.session import SessionLocal
from src.models import ManagedWallet
from src.wallet.manager import WalletManager


USDC_NATIVE = Web3.to_checksum_address("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359")
USDC_E      = Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
PUSD        = Web3.to_checksum_address("0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB")
ONRAMP      = Web3.to_checksum_address("0x93070a847efEf7F70739046A929D47a521F5B8ee")
UNISWAP_V3_ROUTER = Web3.to_checksum_address("0xE592427A0AEce92De3Edee1F18E0157C05861564")
POOL_FEE = 100  # 0.01% — the USDC/USDC.e deepest pool

MAX_UINT = 2**256 - 1

ERC20_ABI = [
    {"constant": True, "inputs": [{"name": "o", "type": "address"}], "name": "balanceOf",
     "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
    {"constant": False, "inputs": [{"name": "spender", "type": "address"},
                                    {"name": "value", "type": "uint256"}],
     "name": "approve", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
    {"constant": True, "inputs": [{"name": "owner", "type": "address"},
                                   {"name": "spender", "type": "address"}],
     "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
]

ROUTER_ABI = [{
    "inputs": [{
        "components": [
            {"name": "tokenIn", "type": "address"},
            {"name": "tokenOut", "type": "address"},
            {"name": "fee", "type": "uint24"},
            {"name": "recipient", "type": "address"},
            {"name": "deadline", "type": "uint256"},
            {"name": "amountIn", "type": "uint256"},
            {"name": "amountOutMinimum", "type": "uint256"},
            {"name": "sqrtPriceLimitX96", "type": "uint160"},
        ],
        "name": "params", "type": "tuple",
    }],
    "name": "exactInputSingle",
    "outputs": [{"name": "amountOut", "type": "uint256"}],
    "stateMutability": "payable", "type": "function",
}]

ONRAMP_ABI = [{
    "inputs": [
        {"name": "_asset", "type": "address"},
        {"name": "_to", "type": "address"},
        {"name": "_amount", "type": "uint256"},
    ],
    "name": "wrap", "outputs": [], "stateMutability": "nonpayable", "type": "function",
}]

SLIPPAGE_BPS = 50  # 0.5% — loose, pool is super tight, easily passes


def send(w3, signer, tx_built):
    signed = signer.sign_transaction(tx_built)
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
    h = w3.eth.send_raw_transaction(raw)
    return h


def wait(w3, h, label):
    print(f"  {label} tx: {h.hex()}")
    rcpt = w3.eth.wait_for_transaction_receipt(h, timeout=180)
    print(f"  {label} status: {rcpt.status} block={rcpt.blockNumber} gas={rcpt.gasUsed}")
    return rcpt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        wallet = (
            db.query(ManagedWallet)
            .filter(ManagedWallet.user_id == args.user_id)
            .first()
        )
        if not wallet:
            print(f"no managed wallet for user_id={args.user_id}"); return 1
        signer = WalletManager.get_signer(wallet)
        eoa = signer.address
    finally:
        db.close()

    w3 = Web3(Web3.HTTPProvider(settings.polygon_rpc_url, request_kwargs={"timeout": 15}))
    chain_id = settings.polygon_chain_id

    usdc = w3.eth.contract(address=USDC_NATIVE, abi=ERC20_ABI)
    usdce = w3.eth.contract(address=USDC_E, abi=ERC20_ABI)
    pusd = w3.eth.contract(address=PUSD, abi=ERC20_ABI)
    router = w3.eth.contract(address=UNISWAP_V3_ROUTER, abi=ROUTER_ABI)
    onramp = w3.eth.contract(address=ONRAMP, abi=ONRAMP_ABI)

    usdc_bal = usdc.functions.balanceOf(eoa).call()
    usdce_bal = usdce.functions.balanceOf(eoa).call()
    pusd_bal = pusd.functions.balanceOf(eoa).call()
    matic = w3.eth.get_balance(eoa)
    router_allowance = usdc.functions.allowance(eoa, UNISWAP_V3_ROUTER).call()
    onramp_allowance = usdce.functions.allowance(eoa, ONRAMP).call()

    print(f"EOA:           {eoa}")
    print(f"USDC native:   {usdc_bal/1e6:,.6f}")
    print(f"USDC.e:        {usdce_bal/1e6:,.6f}")
    print(f"pUSD:          {pusd_bal/1e6:,.6f}")
    print(f"MATIC:         {matic/1e18:,.6f}")
    print(f"USDC -> router allowance:  {router_allowance}")
    print(f"USDC.e -> onramp allowance: {onramp_allowance}")

    # Plan
    swap_amount = usdc_bal
    will_swap = swap_amount > 0
    expected_usdce_out = int(swap_amount * (1 - 0.0001))  # ~0.01% fee
    min_out = int(swap_amount * (10000 - SLIPPAGE_BPS) // 10000)

    print("\n--- Plan ---")
    if will_swap:
        print(f"1) Swap {swap_amount/1e6:.6f} native USDC -> USDC.e (min out: {min_out/1e6:.6f}, expect ~{expected_usdce_out/1e6:.6f})")
        if router_allowance < swap_amount:
            print(f"   needs USDC -> SwapRouter approve (current allowance {router_allowance})")
    else:
        print("1) Skip swap (no native USDC)")
    print(f"2) Wrap USDC.e -> pUSD via CollateralOnramp (uses USDC.e balance after swap)")
    print(f"   needs USDC.e -> Onramp approve unless allowance >= amount")

    if not args.confirm:
        print("\nDRY RUN — pass --confirm to broadcast.")
        return 0

    nonce = w3.eth.get_transaction_count(eoa)
    gp = w3.eth.gas_price
    print(f"\nGas price: {gp/1e9:.2f} gwei | nonce start: {nonce}")

    # STEP 1a: approve USDC -> router if needed
    if will_swap and router_allowance < swap_amount:
        print("\n[1a] Approving USDC -> Uniswap V3 Router...")
        tx = usdc.functions.approve(UNISWAP_V3_ROUTER, MAX_UINT).build_transaction({
            "from": eoa, "nonce": nonce, "gas": 100_000,
            "gasPrice": gp, "chainId": chain_id,
        })
        rcpt = wait(w3, send(w3, signer, tx), "approve USDC->router")
        if rcpt.status != 1: print("FAIL"); return 2
        nonce += 1

    # STEP 1b: swap
    if will_swap:
        print("\n[1b] Swapping USDC -> USDC.e via Uniswap V3 (0.01% pool)...")
        deadline = int(time.time()) + 600
        params = (USDC_NATIVE, USDC_E, POOL_FEE, eoa, deadline, swap_amount, min_out, 0)
        tx = router.functions.exactInputSingle(params).build_transaction({
            "from": eoa, "nonce": nonce, "gas": 250_000, "value": 0,
            "gasPrice": gp, "chainId": chain_id,
        })
        rcpt = wait(w3, send(w3, signer, tx), "swap")
        if rcpt.status != 1: print("swap FAIL"); return 3
        nonce += 1
        time.sleep(2)

    # Re-read USDC.e balance after swap
    usdce_bal = usdce.functions.balanceOf(eoa).call()
    print(f"\nUSDC.e balance after swap: {usdce_bal/1e6:,.6f}")
    if usdce_bal == 0:
        print("No USDC.e to wrap — exiting."); return 0

    # STEP 2a: approve USDC.e -> onramp if needed
    cur_onramp_allowance = usdce.functions.allowance(eoa, ONRAMP).call()
    if cur_onramp_allowance < usdce_bal:
        print("\n[2a] Approving USDC.e -> CollateralOnramp...")
        tx = usdce.functions.approve(ONRAMP, MAX_UINT).build_transaction({
            "from": eoa, "nonce": nonce, "gas": 100_000,
            "gasPrice": gp, "chainId": chain_id,
        })
        rcpt = wait(w3, send(w3, signer, tx), "approve USDC.e->onramp")
        if rcpt.status != 1: print("FAIL"); return 4
        nonce += 1

    # STEP 2b: wrap
    print("\n[2b] Wrapping USDC.e -> pUSD via CollateralOnramp...")
    tx = onramp.functions.wrap(USDC_E, eoa, usdce_bal).build_transaction({
        "from": eoa, "nonce": nonce, "gas": 250_000,
        "gasPrice": gp, "chainId": chain_id,
    })
    rcpt = wait(w3, send(w3, signer, tx), "wrap")
    if rcpt.status != 1: print("wrap FAIL"); return 5

    time.sleep(2)
    final_usdc = usdc.functions.balanceOf(eoa).call()
    final_usdce = usdce.functions.balanceOf(eoa).call()
    final_pusd = pusd.functions.balanceOf(eoa).call()
    print(f"\nFINAL:  USDC={final_usdc/1e6:,.6f}  USDC.e={final_usdce/1e6:,.6f}  pUSD={final_pusd/1e6:,.6f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
