"""Polymarket V2 order signing + posting, pure Python.

Port of ``ExchangeOrderBuilderV2`` from
``github.com/Polymarket/clob-client-v2`` (TypeScript, v1.0.6). Reason for
porting: the only Python SDK on PyPI (``py-clob-client``) is archived and
still signs V1 orders, which Polymarket has rejected since 2026-04-28
with ``{"error": "order_version_mismatch"}``.

What this module does:

  * Builds an :class:`OrderV2` struct matching the V2 EIP-712 layout (11
    fields, different from V1).
  * Computes the V2 EIP-712 typed-data hash with the right type-hash and
    domain.
  * Signs it with ``eth_account`` using the EOA private key.
  * Serializes the order to the wire JSON expected by ``POST /order``.
  * Generates the L2 auth headers (HMAC over method+path+body+timestamp).

Verified hash-equivalence against the official TS V2 client for a set of
fixed deterministic inputs — see ``scripts/probe_v2_hash_parity.py``.

Constants captured from the reference, exact-byte-for-exact-byte:

  ORDER_TYPE_STRING — must match V2 contract's hash(Order) at byte level.
  CTF_EXCHANGE_V2_DOMAIN_NAME / VERSION — used in EIP-712 domain hash.
  Exchange addresses — verifyingContract for CTF V2 + NegRisk V2.
  Wire format — salt is JSON number, side is JSON string, taker+expiration
                 are wire-only and absent from the signed struct.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from typing import Literal

from eth_abi import encode as abi_encode
from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import keccak, to_checksum_address

# --- V2 spec constants — verbatim from clob-client-v2 -----------------------

# From src/order-utils/exchangeOrderBuilderV2.ts:
ORDER_TYPE_STRING = (
    "Order(uint256 salt,address maker,address signer,uint256 tokenId,"
    "uint256 makerAmount,uint256 takerAmount,uint8 side,uint8 signatureType,"
    "uint256 timestamp,bytes32 metadata,bytes32 builder)"
)
ORDER_TYPE_HASH = keccak(text=ORDER_TYPE_STRING)

# From src/order-utils/model/ctfExchangeV2TypedData.ts:
DOMAIN_NAME = "Polymarket CTF Exchange"
DOMAIN_VERSION = "2"

# From src/config.ts MATIC_CONTRACTS (chain 137):
CTF_EXCHANGE_V2 = "0xE111180000d2663C0091e4f400237545B87B996B"
NEG_RISK_EXCHANGE_V2 = "0xe2222d279d744050d28e00520010520000310F59"

# From src/constants.ts:
BYTES32_ZERO = "0x" + "00" * 32
ZERO_ADDRESS = "0x" + "00" * 20

# From src/order-utils/model/signatureTypeV2.ts:
SIG_EOA = 0
SIG_POLY_PROXY = 1
SIG_POLY_GNOSIS_SAFE = 2
SIG_POLY_1271 = 3

# From src/config.ts:
COLLATERAL_DECIMALS = 6  # pUSD has 6 decimals, same as USDC

# Side encoding for the signed struct (uint8). Wire JSON sends the string
# "BUY"/"SELL" instead — see _order_to_wire below.
SIDE_BUY = 0
SIDE_SELL = 1


# --- Order data classes -----------------------------------------------------


@dataclass(frozen=True)
class OrderV2:
    """The 11 fields that go into the V2 EIP-712 signature, plus the two
    wire-only fields (``taker``, ``expiration``) that travel with the order
    on the wire but are NOT part of the signed struct.

    Kept as a single dataclass so the wire-JSON serializer has everything it
    needs without a second lookup."""
    # --- signed fields (order matters for type hash) ---
    salt: int
    maker: str           # checksummed address
    signer: str          # checksummed address (EOA, or for POLY_1271 == maker)
    token_id: int
    maker_amount: int    # in 10^6 units (pUSD decimals)
    taker_amount: int    # in 10^6 units
    side: int            # 0 = BUY, 1 = SELL  — uint8 in signed struct
    signature_type: int
    timestamp: int       # ms since epoch, per the TS reference (Date.now())
    metadata: str        # 0x-prefixed bytes32 hex
    builder: str         # 0x-prefixed bytes32 hex
    # --- wire-only fields (NOT in the signed struct) ---
    expiration: int = 0  # unix seconds; 0 = no expiration


@dataclass(frozen=True)
class SignedOrderV2:
    order: OrderV2
    signature: str  # 0x-prefixed 65-byte hex


# --- Amount math (port of getOrderRawAmounts + parseUnits) -----------------


_ROUND_CONFIG_BY_TICK: dict[str, tuple[int, int, int]] = {
    # tick_size -> (price_decimals, size_decimals, amount_decimals)
    "0.1": (1, 2, 3),
    "0.01": (2, 2, 4),
    "0.001": (3, 2, 5),
    "0.0001": (4, 2, 6),
}


def _round_to(value: Decimal, decimals: int, mode) -> Decimal:
    """Round ``value`` to ``decimals`` places using ``mode`` (ROUND_DOWN/UP).

    Matches the TS roundDown/roundUp helpers which operate on JS numbers
    (doubles). We use Decimal to avoid float-noise mismatch across runtimes
    — the TS reference uses ``parseFloat(num.toFixed(decimals))`` which is
    equivalent to a half-even rounding for representation but plain truncation
    for roundDown / ceiling for roundUp."""
    quant = Decimal(10) ** -decimals
    return value.quantize(quant, rounding=mode)


def _decimal_places(value: Decimal) -> int:
    """Count decimal places of a Decimal. Matches TS ``decimalPlaces``
    semantics: trailing zeros don't count."""
    if value == value.to_integral_value():
        return 0
    s = format(value.normalize(), "f")
    return len(s.split(".")[1]) if "." in s else 0


def compute_amounts(side: str, size: float, price: float, tick_size: str) -> tuple[int, int]:
    """Port of getOrderRawAmounts + parseUnits. Returns
    (maker_amount_int, taker_amount_int), both already scaled by 10^6."""
    if tick_size not in _ROUND_CONFIG_BY_TICK:
        raise ValueError(f"unsupported tick size {tick_size}")
    p_dec, s_dec, a_dec = _ROUND_CONFIG_BY_TICK[tick_size]

    raw_price = _round_to(Decimal(str(price)), p_dec, ROUND_DOWN)

    if side == "BUY":
        raw_taker_amt = _round_to(Decimal(str(size)), s_dec, ROUND_DOWN)
        raw_maker_amt = raw_taker_amt * raw_price
        if _decimal_places(raw_maker_amt) > a_dec:
            raw_maker_amt = _round_to(raw_maker_amt, a_dec + 4, ROUND_UP)
            if _decimal_places(raw_maker_amt) > a_dec:
                raw_maker_amt = _round_to(raw_maker_amt, a_dec, ROUND_DOWN)
    elif side == "SELL":
        raw_maker_amt = _round_to(Decimal(str(size)), s_dec, ROUND_DOWN)
        raw_taker_amt = raw_maker_amt * raw_price
        if _decimal_places(raw_taker_amt) > a_dec:
            raw_taker_amt = _round_to(raw_taker_amt, a_dec + 4, ROUND_UP)
            if _decimal_places(raw_taker_amt) > a_dec:
                raw_taker_amt = _round_to(raw_taker_amt, a_dec, ROUND_DOWN)
    else:
        raise ValueError(f"side must be BUY or SELL, got {side!r}")

    scale = Decimal(10) ** COLLATERAL_DECIMALS
    maker_int = int((raw_maker_amt * scale).to_integral_value(rounding=ROUND_DOWN))
    taker_int = int((raw_taker_amt * scale).to_integral_value(rounding=ROUND_DOWN))
    return maker_int, taker_int


# --- Salt generator ---------------------------------------------------------


def generate_salt() -> int:
    """Random salt. The TS reference uses ``Math.round(Math.random() *
    Date.now())`` which is uniformly distributed up to ~2^53. We use Python
    ``secrets.randbits(63)`` — a wider range is fine, the contract accepts any
    uint256."""
    return secrets.randbits(63)


# --- Build, hash, sign ------------------------------------------------------


def build_order(
    *,
    maker: str,
    signer: str,
    token_id: str,
    maker_amount: int,
    taker_amount: int,
    side: Literal["BUY", "SELL"],
    signature_type: int = SIG_POLY_PROXY,
    timestamp_ms: int | None = None,
    metadata: str = BYTES32_ZERO,
    builder: str = BYTES32_ZERO,
    expiration: int = 0,
    salt: int | None = None,
) -> OrderV2:
    """Construct an OrderV2 with all the bookkeeping handled. Both ``salt``
    and ``timestamp_ms`` default to fresh random/now values so callers don't
    have to think about them."""
    return OrderV2(
        salt=salt if salt is not None else generate_salt(),
        maker=to_checksum_address(maker),
        signer=to_checksum_address(signer),
        token_id=int(token_id),
        maker_amount=int(maker_amount),
        taker_amount=int(taker_amount),
        side=SIDE_BUY if side == "BUY" else SIDE_SELL,
        signature_type=int(signature_type),
        timestamp=timestamp_ms if timestamp_ms is not None else int(time.time() * 1000),
        metadata=metadata,
        builder=builder,
        expiration=int(expiration),
    )


def _domain_separator(exchange_address: str) -> bytes:
    """EIP-712 domain separator hash. Matches the TS computation in
    ``ExchangeOrderBuilderV2``."""
    domain_type_hash = keccak(
        text="EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
    )
    return keccak(abi_encode(
        ["bytes32", "bytes32", "bytes32", "uint256", "address"],
        [
            domain_type_hash,
            keccak(text=DOMAIN_NAME),
            keccak(text=DOMAIN_VERSION),
            137,
            to_checksum_address(exchange_address),
        ],
    ))


def _struct_hash(order: OrderV2) -> bytes:
    """Hash of the typed Order struct. The field order must match
    ``ORDER_TYPE_STRING`` exactly or signatures verify against a different
    type and Polymarket returns ``order_version_mismatch``."""
    return keccak(abi_encode(
        ["bytes32", "uint256", "address", "address", "uint256", "uint256",
         "uint256", "uint8", "uint8", "uint256", "bytes32", "bytes32"],
        [
            ORDER_TYPE_HASH,
            order.salt,
            to_checksum_address(order.maker),
            to_checksum_address(order.signer),
            order.token_id,
            order.maker_amount,
            order.taker_amount,
            order.side,
            order.signature_type,
            order.timestamp,
            bytes.fromhex(order.metadata.removeprefix("0x")),
            bytes.fromhex(order.builder.removeprefix("0x")),
        ],
    ))


def typed_data_hash(order: OrderV2, exchange_address: str) -> bytes:
    """Final EIP-712 hash that gets signed: ``keccak("\\x19\\x01" || domain ||
    struct)``. Returns 32 bytes."""
    return keccak(b"\x19\x01" + _domain_separator(exchange_address) + _struct_hash(order))


def sign_order(order: OrderV2, exchange_address: str, private_key: str) -> SignedOrderV2:
    """Sign a V2 order. ``private_key`` is the EOA hex (with or without 0x).
    The address derived from this key must equal ``order.signer`` for the
    Polymarket matching engine to accept the order under signature types
    EOA/POLY_PROXY/POLY_GNOSIS_SAFE."""
    digest = typed_data_hash(order, exchange_address)
    # ``eth_account.Account.signHash`` is the recoverable ECDSA sign; the
    # output is a 65-byte (r || s || v) signature, which is what Polymarket
    # expects in the wire JSON.
    sig = Account._sign_hash(digest, private_key=private_key)
    sig_hex = "0x" + sig.signature.hex().removeprefix("0x")
    return SignedOrderV2(order=order, signature=sig_hex)


# --- Wire serialization -----------------------------------------------------


def order_to_wire(
    signed: SignedOrderV2,
    *,
    owner: str,
    order_type: str = "GTC",
    post_only: bool = False,
    defer_exec: bool = False,
) -> dict:
    """Build the JSON body for ``POST /order``.

    Three things to notice that differ from the signed struct:

    * ``salt`` is sent as a JSON number, not a string.
    * ``side`` is sent as the string "BUY" / "SELL", not the uint8.
    * ``taker`` and ``expiration`` are present on the wire but NOT in the
      signed struct.
    """
    o = signed.order
    return {
        "order": {
            "salt": o.salt,
            "maker": o.maker,
            "signer": o.signer,
            "taker": ZERO_ADDRESS,
            "tokenId": str(o.token_id),
            "makerAmount": str(o.maker_amount),
            "takerAmount": str(o.taker_amount),
            "side": "BUY" if o.side == SIDE_BUY else "SELL",
            "signatureType": o.signature_type,
            "timestamp": str(o.timestamp),
            "expiration": str(o.expiration),
            "metadata": o.metadata,
            "builder": o.builder,
            "signature": signed.signature,
        },
        "owner": owner,
        "orderType": order_type,
        "deferExec": defer_exec,
        "postOnly": post_only,
    }


# --- L2 auth headers --------------------------------------------------------


def _decode_base64url_secret(secret: str) -> bytes:
    """The API secret stored by Polymarket is url-safe base64. Restore +/=
    before decoding."""
    normalized = secret.replace("-", "+").replace("_", "/")
    padded = normalized + "=" * ((4 - len(normalized) % 4) % 4)
    return base64.b64decode(padded)


def build_l2_headers(
    *,
    signer_address: str,
    api_key: str,
    api_secret: str,
    api_passphrase: str,
    method: str,
    request_path: str,
    body: str | None = None,
    timestamp: int | None = None,
) -> dict[str, str]:
    """Generate Polymarket's L2 auth headers. The HMAC is over
    ``timestamp + method + request_path + body`` with the base64url-decoded
    secret. Output is url-safe base64.

    Matches buildPolyHmacSignature in clob-client-v2/src/signing/hmac.ts."""
    ts = timestamp if timestamp is not None else int(time.time())
    msg = f"{ts}{method}{request_path}"
    if body is not None:
        msg += body
    key_bytes = _decode_base64url_secret(api_secret)
    digest = hmac.new(key_bytes, msg.encode("utf-8"), hashlib.sha256).digest()
    sig = base64.b64encode(digest).decode("ascii").replace("+", "-").replace("/", "_")
    return {
        "POLY_ADDRESS": to_checksum_address(signer_address),
        "POLY_SIGNATURE": sig,
        "POLY_TIMESTAMP": str(ts),
        "POLY_API_KEY": api_key,
        "POLY_PASSPHRASE": api_passphrase,
    }
