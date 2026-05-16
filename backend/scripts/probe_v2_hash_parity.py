"""Hash-equivalence regression test for src/executor/v2_signing.py.

Runs a matrix of orders through both the official TypeScript V2 client
(github.com/Polymarket/clob-client-v2) and the Python port, then verifies
the resulting signatures match byte-for-byte. Catches any drift in:

  * The Order EIP-712 type hash (field set + types)
  * Domain separator (name / version / chainId / verifyingContract)
  * Amount math (rounding + decimal scaling)
  * Per-exchange routing (CTF V2 vs NegRisk V2)
  * BUY vs SELL handling

Run after any change to v2_signing.py. Requires Node 20+ and the JS
helper scripts under scripts/v2_parity_ts/. The TS scripts ship with
the repo so the test is reproducible without external network calls
once the Node deps are installed (``cd backend/scripts/v2_parity_ts &&
npm install``).
"""
import json
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.executor import v2_signing as v2  # noqa: E402


TS_DIR = pathlib.Path(__file__).resolve().parent / "v2_parity_ts"
PRIV = "0x" + "11" * 32  # test-only deterministic key — NEVER use for real wallets


def _run_node(script: str) -> str:
    return subprocess.check_output(["node", str(TS_DIR / script)]).decode()


def check_signature_parity() -> bool:
    """Drive the TS client across a matrix and verify Python signs the same
    digest given the same salt+timestamp+inputs."""
    out = _run_node("emit_matrix.mjs")
    all_ok = True
    for line in out.strip().splitlines():
        rec = json.loads(line)
        ref = rec["signed"]
        exch = v2.NEG_RISK_EXCHANGE_V2 if rec["negRisk"] else v2.CTF_EXCHANGE_V2
        order = v2.OrderV2(
            salt=int(ref["salt"]),
            maker=ref["maker"],
            signer=ref["signer"],
            token_id=int(ref["tokenId"]),
            maker_amount=int(ref["makerAmount"]),
            taker_amount=int(ref["takerAmount"]),
            side=v2.SIDE_BUY if ref["side"] == "BUY" else v2.SIDE_SELL,
            signature_type=int(ref["signatureType"]),
            timestamp=int(ref["timestamp"]),
            metadata=ref["metadata"],
            builder=ref["builder"],
            expiration=int(ref["expiration"]),
        )
        py_signed = v2.sign_order(order, exch, PRIV)
        ok = py_signed.signature.lower() == ref["signature"].lower()
        print(f'{"OK " if ok else "FAIL"}  signature  {rec["label"]}')
        if not ok:
            all_ok = False
            print(f"      TS: {ref['signature']}")
            print(f"      Py: {py_signed.signature}")
    return all_ok


def check_amount_math_parity() -> bool:
    """Verify compute_amounts matches the TS getOrderRawAmounts +
    parseUnits pipeline for a range of prices, sizes, sides, ticks."""
    out = _run_node("emit_amounts.mjs")
    all_ok = True
    for line in out.strip().splitlines():
        c = json.loads(line)
        py_maker, py_taker = v2.compute_amounts(c["side"], c["size"], c["price"], c["tickSize"])
        ok = str(py_maker) == c["makerAmount"] and str(py_taker) == c["takerAmount"]
        label = f'{c["side"]} px={c["price"]} sz={c["size"]} tick={c["tickSize"]}'
        print(f'{"OK " if ok else "FAIL"}  amounts    {label}')
        if not ok:
            all_ok = False
            print(f'      TS=({c["makerAmount"]},{c["takerAmount"]}) Py=({py_maker},{py_taker})')
    return all_ok


if __name__ == "__main__":
    sig_ok = check_signature_parity()
    amt_ok = check_amount_math_parity()
    print()
    if sig_ok and amt_ok:
        print("All parity checks PASS.")
        sys.exit(0)
    print("Parity FAILED — Python signing has drifted from the TS reference.")
    sys.exit(1)
