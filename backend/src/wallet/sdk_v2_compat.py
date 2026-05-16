"""PARTIAL patch — DOES NOT make py-clob-client V2-compatible by itself.

Background: Polymarket migrated to V2 exchange contracts on 2026-04-28.
The archived py-clob-client (v0.34.6, last on PyPI) signs V1 orders, which
Polymarket's matching engine rejects with ``order_version_mismatch``.

This module fixes TWO of the THREE things that need to change to sign V2:

1. ``get_contract_config`` — return V2 exchange addresses + pUSD collateral
   on Polygon mainnet. (Done here.)
2. EIP-712 domain ``version`` field — V2 contracts report ``version="2"``
   via their EIP-5267 ``eip712Domain()`` view. (Done here.)
3. **Order struct + type hash** — V2 drops ``taker``, ``expiration``,
   ``nonce``, ``feeRateBps`` from the signed struct and adds ``timestamp``,
   ``metadata``, ``builder``. The type hash is therefore completely
   different. **(NOT DONE.)** py_order_utils.model.order.Order is still
   the V1 layout; patching the struct in place is non-trivial because the
   layout drives both the type-hash and the on-wire JSON.

Empirically verified 2026-05-15 via ``scripts/probe_sdk_post*.py``:
V1 (unpatched) → ``order_version_mismatch``.
V1 + this partial patch (V2 address + version='2', V1 struct) →
``order_version_mismatch`` (because the struct/type-hash is still V1).

Until #3 is implemented (port of ``ExchangeOrderBuilderV2`` from
github.com/Polymarket/clob-client-v2 into Python), :class:`ExecutionEngine`
refuses live orders even when ``LIVE_TRADING_ENABLED=True``. See the
``_refuse_unless_v2_signing_ready`` gate in ``src/executor/engine.py``.

This file is kept as a partial fix so that the V2 port, when done, only
needs to add the missing struct/type-hash work and not redo the address
and domain-version pieces.
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
