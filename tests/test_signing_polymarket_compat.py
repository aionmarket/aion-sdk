"""Gold-standard cross-checks against Polymarket's official py-clob-client-v2.

These guarantee bit-for-bit signature compatibility with the on-chain
Polymarket CTF Exchange V2. If any of these fails, every V2 order signed
by aion-sdk is rejected by Polymarket CLOB.

Skipped automatically if ``py-clob-client-v2`` is not installed.
"""

from __future__ import annotations

import pytest

eth_account = pytest.importorskip("eth_account")
py_clob_client_v2 = pytest.importorskip("py_clob_client_v2")

from aion_sdk.signing import (  # noqa: E402
    V2_CTF_EXCHANGE,
    V2_NEG_RISK_EXCHANGE_A,
    ZERO_BYTES32,
    build_v2_signed_order,
)
from py_clob_client_v2.order_utils.exchange_order_builder_v2 import (  # noqa: E402
    ExchangeOrderBuilderV2,
)
from py_clob_client_v2.order_utils.model.order_data_v2 import OrderDataV2  # noqa: E402
from py_clob_client_v2.signer import Signer  # noqa: E402

_TEST_PRIVATE_KEY = "0x" + "11" * 32
_TEST_ADDRESS = eth_account.Account.from_key(_TEST_PRIVATE_KEY).address
_DEPOSIT_WALLET = "0xC4378BFEe30dBAc2A907ea1E486acCC78B02c185"


def _official_v2_signature(
    verifying_contract, sig_type, maker, signer=None, salt=12345, timestamp=1000000
):
    """Sign the same order via Polymarket's official py-clob-client-v2."""
    signer_obj = Signer(private_key=_TEST_PRIVATE_KEY, chain_id=137)
    builder = ExchangeOrderBuilderV2(
        contract_address=verifying_contract,
        chain_id=137,
        signer=signer_obj,
        generate_salt=lambda: salt,
    )
    order_data = OrderDataV2(
        maker=maker,
        tokenId="55555",
        makerAmount="5500000",
        takerAmount="10000000",
        side=0,
        signer=signer if signer else maker,
        signatureType=sig_type,
        timestamp=str(timestamp),
    )
    signed = builder.build_signed_order(order_data)
    return signed.signature


@pytest.mark.parametrize(
    "label,sig_type,maker,signer,verifying",
    [
        ("EOA vanilla", 0, _TEST_ADDRESS, None, V2_CTF_EXCHANGE),
        ("EOA neg-risk", 0, _TEST_ADDRESS, None, V2_NEG_RISK_EXCHANGE_A),
        ("Proxy vanilla", 1, _DEPOSIT_WALLET, _TEST_ADDRESS, V2_CTF_EXCHANGE),
        ("Proxy neg-risk", 1, _DEPOSIT_WALLET, _TEST_ADDRESS, V2_NEG_RISK_EXCHANGE_A),
        ("Safe vanilla", 2, _DEPOSIT_WALLET, _TEST_ADDRESS, V2_CTF_EXCHANGE),
        ("Safe neg-risk", 2, _DEPOSIT_WALLET, _TEST_ADDRESS, V2_NEG_RISK_EXCHANGE_A),
    ],
)
def test_signature_matches_polymarket_v2_sdk(
    label, sig_type, maker, signer, verifying
):
    """Every (sigType, exchange) combination must be byte-equal to py-clob-client-v2."""
    fixed_ts = 1000000
    ours = build_v2_signed_order(
        private_key=_TEST_PRIVATE_KEY,
        maker=maker,
        signer=signer,
        token_id="55555",
        maker_amount="5500000",
        taker_amount="10000000",
        side="BUY",
        salt=12345,
        timestamp=fixed_ts,
        signature_type=sig_type,
        verifying_contract=verifying,
    )
    expected = _official_v2_signature(
        verifying_contract=verifying,
        sig_type=sig_type,
        maker=maker,
        signer=signer,
        timestamp=fixed_ts,
    )
    assert ours["signature"].lower() == expected.lower(), (
        f"[{label}] signature mismatch: ours={ours['signature']} vs official={expected}"
    )


def test_deposit_wallet_sigtype3_matches_v2_sdk():
    """sigType=3 (POLY_1271 Deposit Wallet): ERC-7739 wrapped signature must
    match py-clob-client-v2 exactly."""
    fixed_ts = 1000000
    ours = build_v2_signed_order(
        private_key=_TEST_PRIVATE_KEY,
        maker=_DEPOSIT_WALLET,
        token_id="55555",
        maker_amount="5500000",
        taker_amount="10000000",
        side="BUY",
        salt=12345,
        timestamp=fixed_ts,
        signature_type=3,
        neg_risk=True,
    )
    expected = _official_v2_signature(
        verifying_contract=V2_NEG_RISK_EXCHANGE_A,
        sig_type=3,
        maker=_DEPOSIT_WALLET,
        signer=_DEPOSIT_WALLET,
        timestamp=fixed_ts,
    )
    assert ours["signature"].lower() == expected.lower(), (
        f"POLY_1271 signature mismatch: ours={ours['signature']} vs official={expected}"
    )
