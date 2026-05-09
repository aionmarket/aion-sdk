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
    )
    kwargs.update(overrides)
    return kwargs


def test_build_v2_signed_order_returns_complete_payload():
    order = build_v2_signed_order(**_base_kwargs())

    # Schema check — every field the backend expects must be present.
    # Polymarket CTF Exchange V2 verifies a 12-field Order struct (same
    # as V1); timestamp/metadata/builder are NOT part of the EIP-712
    # typed data and are no longer emitted by build_v2_signed_order.
    expected_keys = {
        "salt",
        "maker",
        "signer",
        "taker",
        "tokenId",
        "makerAmount",
        "takerAmount",
        "side",
        "expiration",
        "nonce",
        "feeRateBps",
        "signatureType",
        "signature",
    }
    assert set(order.keys()) == expected_keys

    assert order["maker"] == _TEST_ADDRESS
    assert order["signer"] == _TEST_ADDRESS  # defaults to maker
    assert order["taker"] == ZERO_ADDRESS
    assert order["side"] == "BUY"
    assert order["signatureType"] == 0
    assert order["signature"].startswith("0x")
    # 65-byte ECDSA signature -> 130 hex chars + "0x"
    assert len(order["signature"]) == 132


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
            "verifyingContract": V2_CTF_EXCHANGE,
        },
        "message": {
            "salt": int(order["salt"]),
            "maker": order["maker"],
            "signer": order["signer"],
            "taker": order["taker"],
            "tokenId": int(order["tokenId"]),
            "makerAmount": int(order["makerAmount"]),
            "takerAmount": int(order["takerAmount"]),
            "expiration": int(order["expiration"]),
            "nonce": int(order["nonce"]),
            "feeRateBps": int(order["feeRateBps"]),
            "side": 0 if order["side"] == "BUY" else 1,
            "signatureType": order["signatureType"],
        },
    }

    signable = encode_typed_data(full_message=typed_data)
    recovered = eth_account.Account.recover_message(signable, signature=order["signature"])
    assert recovered.lower() == _TEST_ADDRESS.lower()


def test_supports_all_signature_types():
    # sigType=0 (EOA): signer defaults to maker (= the EOA itself).
    order0 = build_v2_signed_order(**_base_kwargs(signature_type=0))
    assert order0["signatureType"] == 0
    # sigType in {1,2,3}: maker is the smart-contract wallet, signer must be
    # the controlling EOA (the address derived from private_key).
    deposit_wallet = "0xC4378BFEe30dBAc2A907ea1E486acCC78B02c185"
    for sig_type in (1, 2, 3):
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


def test_smart_contract_wallet_requires_explicit_signer():
    """sigType=1/2/3 must reject the call when signer is omitted."""
    deposit_wallet = "0xC4378BFEe30dBAc2A907ea1E486acCC78B02c185"
    for sig_type in (1, 2, 3):
        with pytest.raises(ValueError, match="signer is required"):
            build_v2_signed_order(
                **_base_kwargs(signature_type=sig_type, maker=deposit_wallet)
            )


def test_signer_must_match_private_key_eoa():
    """Detect the common ``private_key from EOA-A but signer=EOA-B`` mistake."""
    other_eoa = "0x000000000000000000000000000000000000dEaD"
    with pytest.raises(ValueError, match="does not match the EOA derived from"):
        build_v2_signed_order(
            **_base_kwargs(
                signature_type=3,
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


def test_legacy_v2_extension_kwargs_are_ignored_with_warning():
    """timestamp/metadata/builder kwargs are accepted but ignored (deprecated)."""
    with pytest.warns(DeprecationWarning, match="timestamp/metadata/builder"):
        order = build_v2_signed_order(**_base_kwargs(timestamp=1714400000))
    assert "timestamp" not in order
    assert "metadata" not in order
    assert "builder" not in order
