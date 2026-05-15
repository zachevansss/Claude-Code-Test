"""Patch py-clob-client 0.34.6 to use Polymarket's V2 exchange contracts.

The SDK ships with V1 (pre-2026-04-28) addresses hardcoded in
``py_clob_client.config.get_contract_config``. After the 2026-04-28 exchange
upgrade, Polymarket's matching engine only accepts orders signed against the
V2 verifying contracts and rejects V1-domain signatures with::

    {"error": "order_version_mismatch"}

Until upstream ships V2 addresses, this module monkey-patches the SDK on
import. The patch:

  * rebinds ``get_contract_config`` in the three places it lives
    (``config``, ``client``, ``order_builder.builder``) so every code path
    inside the SDK sees the V2 addresses, and
  * also swaps the collateral token to pUSD (Polymarket's new exchange
    collateral) so any SDK helper that reads ``contract_config.collateral``
    returns the actually-funded token, not the deprecated USDC.e.

Idempotent — calling :func:`apply` multiple times is a no-op after the first.

Verified empirically 2026-05-15 via ``scripts/probe_sdk_post.py`` on proxy
``0xB386c5...8550``: V1 signing returned ``order_version_mismatch``;
V2 signing (via this patch) is the next test on that branch.
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


def apply() -> None:
    """Install the V2 patch. Safe to call repeatedly."""
    global _applied
    if _applied:
        return
    import py_clob_client.config as cfg_mod
    import py_clob_client.client as client_mod
    import py_clob_client.order_builder.builder as builder_mod

    cfg_mod.get_contract_config = _patched_get_contract_config
    client_mod.get_contract_config = _patched_get_contract_config
    builder_mod.get_contract_config = _patched_get_contract_config
    _applied = True


# Auto-apply at import time so that simply importing this module from anywhere
# that touches ClobClient guarantees the patch is live.
apply()
