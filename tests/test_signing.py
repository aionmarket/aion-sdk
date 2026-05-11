"""Tests for the optional V2 signing helper.

The whole module is skipped when ``eth-account`` is not installed, so the
existing test suite for users without the ``signing`` extra remains
unaffected.
"""

from __future__ import annotations

import pytest

eth_account = pytest.importorskip("eth_account")

from aion_sdk.signing import (  # noqa: E402  (import after importorskip)
    V2_CTF_EXCHANGE,
    V2_NEG_RISK_EXCHANGE_A,
    ZERO_ADDRESS,
    ZERO_BYTES32,
    build_v2_signed_order,
)

# A throwaway test private key (NEVER use for real funds).
_TEST_PRIVATE_KEY = "0x" + "11" * 32
_TEST_ADDRESS = eth_account.Account.from_key(_TEST_PRIVATE_KEY).address


def _base_kwargs(**overrides):
    kwargs = dict(
        private_key=_TEST_PRIVATE_KEY,
        maker=_TEST_ADDRESS,
        token_id="69136365945621600854789649488423522395843457249417452310260493085275775221076",
        maker_amount="5500000",
        taker_amount="10000000",
        side="BUY",
        salt=12345,
        timestamp=1000000,
    )
    kwargs.update(overrides)
    return kwargs


def test_build_v2_signed_order_returns_complete_payload():
    order = build_v2_signed_order(**_base_kwargs())

    # V2 Order struct: 11 EIP-712 fields + expiration (HTTP only) + signature.
    expected_keys = {
        "salt",
        "maker",
        "signer",
        "tokenId",
        "makerAmount",
        "takerAmount",
        "side",
        "signatureType",
        "timestamp",
        "metadata",
        "builder",
        "expiration",
        "signature",
    }
    assert set(order.keys()) == expected_keys

    assert order["maker"] == _TEST_ADDRESS
    assert order["signer"] == _TEST_ADDRESS  # defaults to maker
    assert order["side"] == "BUY"
    assert order["signatureType"] == 0
    assert order["signature"].startswith("0x")
    # 65-byte ECDSA signature -> 130 hex chars + "0x"
    assert len(order["signature"]) == 132
    assert int(order["timestamp"]) > 0
    assert order["metadata"] == ZERO_BYTES32
    assert order["builder"] == ZERO_BYTES32


def test_signature_is_deterministic_for_fixed_inputs():
    a = build_v2_signed_order(**_base_kwargs())
    b = build_v2_signed_order(**_base_kwargs())
    # Same private key, same fixed salt + timestamp -> same signature.
    assert a["signature"] == b["signature"]


def test_random_salt_differs_when_not_pinned():
    a = build_v2_signed_order(**_base_kwargs(salt=None))
    b = build_v2_signed_order(**_base_kwargs(salt=None))
    assert a["salt"] != b["salt"]
    assert a["signature"] != b["signature"]


def test_signature_recovers_to_signer_address():
    """Signature must verify back to the signer EOA via standard EIP-712."""
    from eth_account.messages import encode_typed_data

    order = build_v2_signed_order(**_base_kwargs())

    typed_data = {
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
                {"name": "tokenId", "type": "uint256"},
                {"name": "makerAmount", "type": "uint256"},
                {"name": "takerAmount", "type": "uint256"},
                {"name": "side", "type": "uint8"},
                {"name": "signatureType", "type": "uint8"},
                {"name": "timestamp", "type": "uint256"},
                {"name": "metadata", "type": "bytes32"},
                {"name": "builder", "type": "bytes32"},
            ],
        },
        "primaryType": "Order",
        "domain": {
            "name": "Polymarket CTF Exchange",
            "version": "2",
            "chainId": 137,
            "verifyingContract": V2_CTF_EXCHANGE,
        },
        "message": {
            "salt": int(order["salt"]),
            "maker": order["maker"],
            "signer": order["signer"],
            "tokenId": int(order["tokenId"]),
            "makerAmount": int(order["makerAmount"]),
            "takerAmount": int(order["takerAmount"]),
            "side": 0 if order["side"] == "BUY" else 1,
            "signatureType": order["signatureType"],
            "timestamp": int(order["timestamp"]),
            "metadata": bytes.fromhex(order["metadata"].replace("0x", "").zfill(64)),
            "builder": bytes.fromhex(order["builder"].replace("0x", "").zfill(64)),
        },
    }

    signable = encode_typed_data(full_message=typed_data)
    recovered = eth_account.Account.recover_message(signable, signature=order["signature"])
    assert recovered.lower() == _TEST_ADDRESS.lower()


def test_supports_all_signature_types():
    # sigType=0 (EOA): signer defaults to maker (= the EOA itself).
    order0 = build_v2_signed_order(**_base_kwargs(signature_type=0))
    assert order0["signatureType"] == 0
    # sigType in {1,2}: maker is the smart-contract wallet, signer must be
    # the controlling EOA (the address derived from private_key).
    deposit_wallet = "0xC4378BFEe30dBAc2A907ea1E486acCC78B02c185"
    for sig_type in (1, 2):
        order = build_v2_signed_order(
            **_base_kwargs(
                signature_type=sig_type,
                maker=deposit_wallet,
                signer=_TEST_ADDRESS,
            )
        )
        assert order["signatureType"] == sig_type
        assert order["maker"] == deposit_wallet
        assert order["signer"] == _TEST_ADDRESS
    # sigType=3 (POLY_1271): signer defaults to maker (= deposit wallet).
    order3 = build_v2_signed_order(
        **_base_kwargs(
            signature_type=3,
            maker=deposit_wallet,
        )
    )
    assert order3["signatureType"] == 3
    assert order3["maker"] == deposit_wallet
    assert order3["signer"] == deposit_wallet


def test_smart_contract_wallet_requires_explicit_signer():
    """sigType=1/2 must reject the call when signer is omitted.
    sigType=3 (POLY_1271) defaults signer to maker so does not error."""
    deposit_wallet = "0xC4378BFEe30dBAc2A907ea1E486acCC78B02c185"
    for sig_type in (1, 2):
        with pytest.raises(ValueError, match="signer is required"):
            build_v2_signed_order(
                **_base_kwargs(signature_type=sig_type, maker=deposit_wallet)
            )


def test_signer_must_match_private_key_eoa():
    """Detect the common ``private_key from EOA-A but signer=EOA-B`` mistake.
    Only applies to sigType 0/1/2; sigType=3 skips this check."""
    other_eoa = "0x000000000000000000000000000000000000dEaD"
    with pytest.raises(ValueError, match="does not match the EOA derived from"):
        build_v2_signed_order(
            **_base_kwargs(
                signature_type=1,
                maker="0xC4378BFEe30dBAc2A907ea1E486acCC78B02c185",
                signer=other_eoa,
            )
        )


def test_eoa_sigtype_rejects_mismatched_maker_signer():
    """sigType=0 must reject when maker != signer (would be an EOA-vs-contract trap)."""
    other = "0x000000000000000000000000000000000000dEaD"
    with pytest.raises(ValueError, match="signature_type=0"):
        build_v2_signed_order(
            **_base_kwargs(signature_type=0, maker=other, signer=_TEST_ADDRESS)
        )


def test_supports_neg_risk_exchange():
    order = build_v2_signed_order(
        **_base_kwargs(verifying_contract=V2_NEG_RISK_EXCHANGE_A)
    )
    # Different verifying contract must produce a different signature.
    baseline = build_v2_signed_order(**_base_kwargs())
    assert order["signature"] != baseline["signature"]


def test_neg_risk_flag_picks_correct_verifying_contract():
    """neg_risk=True is equivalent to passing V2_NEG_RISK_EXCHANGE_A."""
    a = build_v2_signed_order(**_base_kwargs(neg_risk=True))
    b = build_v2_signed_order(
        **_base_kwargs(verifying_contract=V2_NEG_RISK_EXCHANGE_A)
    )
    assert a["signature"] == b["signature"]


def test_neg_risk_default_is_vanilla_ctf_exchange():
    """neg_risk=False is equivalent to V2_CTF_EXCHANGE (the default)."""
    a = build_v2_signed_order(**_base_kwargs(neg_risk=False))
    b = build_v2_signed_order(**_base_kwargs())
    assert a["signature"] == b["signature"]


def test_neg_risk_and_verifying_contract_must_agree():
    """Catch the foot-gun where caller passes both and they conflict."""
    with pytest.raises(ValueError, match="does not match"):
        build_v2_signed_order(
            **_base_kwargs(
                neg_risk=True, verifying_contract="0xE111180000d2663C0091e4f400237545B87B996B"
            )
        )


def test_sell_side_is_normalized():
    order = build_v2_signed_order(**_base_kwargs(side="sell"))
    assert order["side"] == "SELL"


def test_invalid_side_raises():
    with pytest.raises(ValueError, match="side"):
        build_v2_signed_order(**_base_kwargs(side="HOLD"))


def test_invalid_address_raises():
    with pytest.raises(ValueError, match="maker"):
        build_v2_signed_order(**_base_kwargs(maker="not-an-address"))


def test_deprecated_v1_kwargs_with_warning():
    """taker/nonce/fee_rate_bps are V1 kwargs — accepted but emit deprecation warning."""
    with pytest.warns(DeprecationWarning, match="taker/nonce/fee_rate_bps"):
        order = build_v2_signed_order(**_base_kwargs(taker=ZERO_ADDRESS))
    # V1 fields should not appear in output.
    assert "taker" not in order
    assert "nonce" not in order
    assert "feeRateBps" not in order
    # V2 fields should be present.
    assert "timestamp" in order
    assert "metadata" in order
    assert "builder" in order


def test_poly_1271_produces_erc7739_wrapped_signature():
    """sigType=3 (Deposit Wallet) produces an ERC-7739 wrapped signature,
    which is significantly longer than a standard 65-byte ECDSA signature."""
    deposit_wallet = "0xC4378BFEe30dBAc2A907ea1E486acCC78B02c185"
    order = build_v2_signed_order(
        **_base_kwargs(
            signature_type=3,
            maker=deposit_wallet,
        )
    )
    assert order["signature"].startswith("0x")
    # ERC-7739 wrapped: 65-byte ECDSA + 32-byte domainSep + 32-byte contentsHash
    # + contentsType bytes + 2-byte length = much longer than 132 hex chars.
    assert len(order["signature"]) > 132
    assert order["signer"] == deposit_wallet
    assert order["maker"] == deposit_wallet
