"""Patch py-clob-client 0.34.6 to use Polymarket's V2 exchange contracts.

The SDK ships with V1 (pre-2026-04-28) addresses hardcoded in
``py_clob_client.config.get_contract_config``. After the 2026-04-28 exchange
upgrade, Polymarket's matching engine only accepts orders signed against the
V2 verifying contracts and rejects V1-domain signatures with::

    {"error": "order_version_mismatch"}

Two distinct things need patching:

1. ``py_clob_client.config.get_contract_config`` — the exchange/collateral
   address lookup. V1 addresses get rejected by Polymarket's matching
   engine; we substitute the V2 addresses + pUSD collateral.

2. ``py_order_utils.builders.base_builder.BaseBuilder._get_domain_separator``
   — the EIP-712 domain construction. py_order_utils hardcodes
   ``version="1"`` but the on-chain V2 contracts report ``version="2"``
   via their EIP-5267 ``eip712Domain()`` method. With only fix #1 applied,
   orders still get ``order_version_mismatch``. With both fixes applied,
   signatures verify against the V2 contracts and the matching engine
   accepts the order.

Idempotent — calling :func:`apply` multiple times is a no-op after the first.

Verified empirically 2026-05-15 via ``scripts/probe_sdk_post.py``: V1 path
returned ``order_version_mismatch``; V2 (this patch) returned a valid
``order_id`` on a tiny unfillable BUY.
"""
from py_clob_client.clob_types import ContractConfig

# Polymarket V2 contracts on Polygon mainnet (chainID 137), live since 2026-04-28.
# Verified against on-chain approvals on a freshly-funded Magic Link proxy:
# ``scripts/probe_approvals.py`` shows all V2 contracts pUSD-approved-max +
# CTF.setApprovalForAll=true automatically when the proxy is funded via the
# Polymarket UI / Coinbase onramp.
CTF_EXCHANGE_V2 = "0xE111180000d2663C0091e4f400237545B87B996B"
NEG_RISK_EXCHANGE_V2 = "0xe2222d279d744050d28e00520010520000310F59"
PUSD_COLLATERAL = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
CTF_CONDITIONAL_TOKENS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

_POLYGON_MAINNET = 137
_applied = False


def _patched_get_contract_config(chainID: int, neg_risk: bool = False) -> ContractConfig:
    """Drop-in replacement for ``py_clob_client.config.get_contract_config``.

    Only overrides the Polygon mainnet entry; other chains (Mumbai testnet,
    etc.) fall through to the original implementation so nothing in test
    environments breaks."""
    if chainID != _POLYGON_MAINNET:
        return _original_get_contract_config(chainID, neg_risk)
    if neg_risk:
        return ContractConfig(
            exchange=NEG_RISK_EXCHANGE_V2,
            collateral=PUSD_COLLATERAL,
            conditional_tokens=CTF_CONDITIONAL_TOKENS,
        )
    return ContractConfig(
        exchange=CTF_EXCHANGE_V2,
        collateral=PUSD_COLLATERAL,
        conditional_tokens=CTF_CONDITIONAL_TOKENS,
    )


# Capture the original before we overwrite — supports non-mainnet fallthrough
# and lets ``apply`` be idempotent.
import py_clob_client.config as _clob_config  # noqa: E402

_original_get_contract_config = _clob_config.get_contract_config


_V2_ADDRESSES_LOWER = {CTF_EXCHANGE_V2.lower(), NEG_RISK_EXCHANGE_V2.lower()}


def _patched_get_domain_separator(self, chain_id, verifying_contract):
    """Replacement for ``BaseBuilder._get_domain_separator``. Selects EIP-712
    domain ``version`` based on which exchange is verifying: V2 contracts
    report ``version="2"`` via on-chain ``eip712Domain()`` (EIP-5267), while
    the upstream SDK hardcoded ``version="1"``. Keeps version="1" for any
    non-V2 verifyingContract so testnet / legacy code paths still sign
    correctly."""
    from poly_eip712_structs import make_domain
    version = "2" if str(verifying_contract).lower() in _V2_ADDRESSES_LOWER else "1"
    return make_domain(
        name="Polymarket CTF Exchange",
        version=version,
        chainId=str(chain_id),
        verifyingContract=verifying_contract,
    )


def apply() -> None:
    """Install the V2 patches. Safe to call repeatedly."""
    global _applied
    if _applied:
        return
    import py_clob_client.config as cfg_mod
    import py_clob_client.client as client_mod
    import py_clob_client.order_builder.builder as builder_mod
    import py_order_utils.builders.base_builder as base_builder_mod

    cfg_mod.get_contract_config = _patched_get_contract_config
    client_mod.get_contract_config = _patched_get_contract_config
    builder_mod.get_contract_config = _patched_get_contract_config
    base_builder_mod.BaseBuilder._get_domain_separator = _patched_get_domain_separator
    _applied = True


# Auto-apply at import time so that simply importing this module from anywhere
# that touches ClobClient guarantees the patch is live.
apply()
