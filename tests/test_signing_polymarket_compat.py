"""Gold-standard cross-checks against Polymarket's official py-order-utils.

These guarantee bit-for-bit signature compatibility with the on-chain
Polymarket CTF Exchange. If any of these fails, every order signed by
aion-sdk is rejected by Polymarket CLOB with ``Invalid order payload``.

Skipped automatically if ``py-order-utils`` (Polymarket's reference
signer) is not installed.
"""

from __future__ import annotations

import pytest

eth_account = pytest.importorskip("eth_account")
py_order_utils = pytest.importorskip("py_order_utils")

from aion_sdk.signing import (  # noqa: E402
    V2_CTF_EXCHANGE,
    V2_NEG_RISK_EXCHANGE_A,
    ZERO_ADDRESS,
    build_v2_signed_order,
)
from py_order_utils.builders import OrderBuilder as UtilsOrderBuilder  # noqa: E402
from py_order_utils.signer import Signer as UtilsSigner  # noqa: E402
from py_order_utils.model import OrderData  # noqa: E402

_TEST_PRIVATE_KEY = "0x" + "11" * 32
_TEST_ADDRESS = eth_account.Account.from_key(_TEST_PRIVATE_KEY).address
_DEPOSIT_WALLET = "0xC4378BFEe30dBAc2A907ea1E486acCC78B02c185"


def _official_signature(verifying_contract, sig_type, maker, signer, salt=12345):
    """Sign the same order via Polymarket's reference py-order-utils."""
    builder = UtilsOrderBuilder(
        verifying_contract,
        137,
        UtilsSigner(key=_TEST_PRIVATE_KEY),
        salt_generator=lambda: salt,
    )
    return builder.build_signed_order(
        OrderData(
            maker=maker,
            taker=ZERO_ADDRESS,
            tokenId="55555",
            makerAmount="5500000",
            takerAmount="10000000",
            side=0,
            feeRateBps="0",
            nonce="0",
            signer=signer,
            expiration="0",
            signatureType=sig_type,
        )
    ).signature


@pytest.mark.parametrize(
    "label,sig_type,maker,signer,verifying",
    [
        ("EOA vanilla",       0, _TEST_ADDRESS,    None,          V2_CTF_EXCHANGE),
        ("EOA neg-risk",      0, _TEST_ADDRESS,    None,          V2_NEG_RISK_EXCHANGE_A),
        ("Proxy vanilla",     1, _DEPOSIT_WALLET,  _TEST_ADDRESS, V2_CTF_EXCHANGE),
        ("Proxy neg-risk",    1, _DEPOSIT_WALLET,  _TEST_ADDRESS, V2_NEG_RISK_EXCHANGE_A),
        ("Safe vanilla",      2, _DEPOSIT_WALLET,  _TEST_ADDRESS, V2_CTF_EXCHANGE),
        ("Safe neg-risk",     2, _DEPOSIT_WALLET,  _TEST_ADDRESS, V2_NEG_RISK_EXCHANGE_A),
    ],
)
def test_signature_matches_polymarket_py_order_utils(
    label, sig_type, maker, signer, verifying
):
    """Every (sigType, exchange) combination must be byte-equal to py-order-utils."""
    ours = build_v2_signed_order(
        private_key=_TEST_PRIVATE_KEY,
        maker=maker,
        signer=signer,
        token_id="55555",
        maker_amount="5500000",
        taker_amount="10000000",
        side="BUY",
        salt=12345,
        signature_type=sig_type,
        verifying_contract=verifying,
    )
    expected = _official_signature(
        verifying_contract=verifying,
        sig_type=sig_type,
        maker=maker,
        signer=signer or maker,
    )
    assert ours["signature"].lower() == expected.lower(), (
        f"[{label}] signature mismatch: ours={ours['signature']} vs official={expected}"
    )


def test_deposit_wallet_sigtype3_recovers_to_eoa():
    """sigType=3 (POLY_1271 Deposit Wallet): py-order-utils refuses sigType=3,
    so verify by recovering the signer from the EIP-712 digest manually."""
    from eth_account.messages import encode_typed_data

    order = build_v2_signed_order(
        private_key=_TEST_PRIVATE_KEY,
        maker=_DEPOSIT_WALLET,
        signer=_TEST_ADDRESS,
        token_id="55555",
        maker_amount="5500000",
        taker_amount="10000000",
        side="BUY",
        salt=12345,
        signature_type=3,
        neg_risk=True,
    )

    typed = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "Order": [
                {"name": "salt", "type": "uint256"},
                {"name": "maker", "type": "address"},
                {"name": "signer", "type": "address"},
                {"name": "taker", "type": "address"},
                {"name": "tokenId", "type": "uint256"},
                {"name": "makerAmount", "type": "uint256"},
                {"name": "takerAmount", "type": "uint256"},
                {"name": "expiration", "type": "uint256"},
                {"name": "nonce", "type": "uint256"},
                {"name": "feeRateBps", "type": "uint256"},
                {"name": "side", "type": "uint8"},
                {"name": "signatureType", "type": "uint8"},
            ],
        },
        "primaryType": "Order",
        "domain": {
            "name": "Polymarket CTF Exchange",
            "version": "1",
            "chainId": 137,
            "verifyingContract": V2_NEG_RISK_EXCHANGE_A,
        },
        "message": {
            "salt": 12345,
            "maker": _DEPOSIT_WALLET,
            "signer": _TEST_ADDRESS,
            "taker": ZERO_ADDRESS,
            "tokenId": 55555,
            "makerAmount": 5500000,
            "takerAmount": 10000000,
            "expiration": 0,
            "nonce": 0,
            "feeRateBps": 0,
            "side": 0,
            "signatureType": 3,
        },
    }
    signable = encode_typed_data(full_message=typed)
    recovered = eth_account.Account.recover_message(
        signable, signature=order["signature"]
    )
    assert recovered.lower() == _TEST_ADDRESS.lower()
