"""
Polymarket V2 order signing helpers.

This module is OPTIONAL and INDEPENDENT from the rest of ``aion_sdk``.

Why it exists:
    The official ``py-clob-client`` (<= 0.34.6) only signs Polymarket V1
    orders (USDC settlement). Polymarket V2 markets settle in ``pUSD``
    and require the EIP-712 ``Order`` struct to include three additional
    fields: ``timestamp``, ``metadata`` and ``builder``. Until upstream
    ``py-clob-client`` adds V2 support, agents that want to trade V2
    markets need an independent signer.

What it does NOT do:
    * It does not replace ``client.trade(...)`` — it only produces the
      signed payload that ``client.trade(...)`` accepts.
    * It does not depend on ``py-clob-client`` or ``py-order-utils``.

Install:
    The runtime requires ``eth-account``. Install with the optional extra::

        pip install "aion-sdk[signing]"

    Without that extra, the rest of the SDK works as before; only this
    module raises an informative error on import.

Wallet types:
    All four Polymarket signature types are supported (the helper does
    not enforce any). ``signatureType=0`` (EOA) is the default. Pass an
    explicit value if your wallet is a Polymarket Proxy / Gnosis Safe /
    Deposit Wallet.

Usage:
    >>> from aion_sdk.signing import build_v2_signed_order, V2_CTF_EXCHANGE
    >>> signed = build_v2_signed_order(
    ...     private_key="0x...",
    ...     maker="0x...",
    ...     token_id="6913636594...",
    ...     maker_amount="5500000",
    ...     taker_amount="10000000",
    ...     side="BUY",
    ...     verifying_contract=V2_CTF_EXCHANGE,
    ... )
    >>> client.trade({
    ...     "venue": "polymarket",
    ...     "marketConditionId": "0x...",
    ...     "marketQuestion": "...",
    ...     "orderSize": 10,
    ...     "price": 0.55,
    ...     "outcome": "YES",
    ...     "order": signed,
    ... })
"""

from __future__ import annotations

import secrets
import time
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Constants — Polygon mainnet (chainId = 137)
# ---------------------------------------------------------------------------

POLYGON_CHAIN_ID: int = 137

#: Polymarket CTF Exchange V2 (pUSD-collateralised, vanilla CTF markets).
V2_CTF_EXCHANGE: str = "0xE111180000d2663C0091e4f400237545B87B996B"

#: Polymarket Neg-Risk Exchange A (V2). Used by some neg-risk markets.
V2_NEG_RISK_EXCHANGE_A: str = "0xe2222d279d744050d28e00520010520000310F59"

#: Polymarket Neg-Risk Exchange B (V2). Used by other neg-risk markets.
V2_NEG_RISK_EXCHANGE_B: str = "0xe2222d002000Ba0053CEF3375333610F64600036"

ZERO_ADDRESS: str = "0x0000000000000000000000000000000000000000"
ZERO_BYTES32: str = "0x" + "00" * 32

#: Side encoding used by the Polymarket Order struct (V1 and V2).
_SIDE_BUY: int = 0
_SIDE_SELL: int = 1

#: EIP-712 domain name and version for the V2 CTF Exchange contracts.
_EIP712_DOMAIN_NAME: str = "Polymarket CTF Exchange"
_EIP712_DOMAIN_VERSION: str = "2"

#: V2 Order struct field layout. Field order matters — it is part of the
#: EIP-712 type hash. The first 12 fields match V1; the trailing three
#: (``timestamp``, ``metadata``, ``builder``) are V2-only.
_ORDER_FIELDS_V2 = [
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
    {"name": "timestamp", "type": "uint256"},
    {"name": "metadata", "type": "bytes32"},
    {"name": "builder", "type": "bytes32"},
]

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class SigningDependencyError(ImportError):
    """Raised when ``eth-account`` is not installed.

    Install with::

        pip install "aion-sdk[signing]"
    """


def _require_eth_account():
    """Lazy import so the rest of ``aion_sdk`` works without eth-account."""
    try:
        from eth_account import Account  # type: ignore[import-not-found]
        from eth_account.messages import encode_typed_data  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - exercised in env w/o eth-account
        raise SigningDependencyError(
            "aion_sdk.signing requires the optional 'signing' extra. "
            "Install with:  pip install 'aion-sdk[signing]'"
        ) from exc
    return Account, encode_typed_data


def _normalize_side(side: Any) -> int:
    """Accept BUY/SELL strings (any case) or 0/1 ints; return 0 or 1."""
    if isinstance(side, bool):
        raise ValueError("side must be 'BUY'/'SELL' or 0/1, not bool")
    if isinstance(side, int):
        if side in (_SIDE_BUY, _SIDE_SELL):
            return side
        raise ValueError("side int must be 0 (BUY) or 1 (SELL)")
    if isinstance(side, str):
        normalized = side.strip().upper()
        if "." in normalized:
            normalized = normalized.split(".")[-1]
        if normalized == "BUY":
            return _SIDE_BUY
        if normalized == "SELL":
            return _SIDE_SELL
    raise ValueError(f"unrecognised side value: {side!r}")


def _to_uint(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must not be bool")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError(f"{name} is required")
        try:
            result = int(text, 0) if text.lower().startswith("0x") else int(text)
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer-compatible string") from exc
    else:
        raise ValueError(f"{name} must be an int or str")
    if result < 0:
        raise ValueError(f"{name} must be non-negative")
    return result


def _normalize_address(value: str, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    text = value.strip()
    if not text.startswith("0x") or len(text) != 42:
        raise ValueError(f"{name} must be a 0x-prefixed 20-byte hex address")
    int(text, 16)  # validate hex
    return text


def _normalize_bytes32(value: str, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    text = value.strip()
    if not text.startswith("0x") or len(text) != 66:
        raise ValueError(f"{name} must be a 0x-prefixed 32-byte hex value")
    int(text, 16)
    return text


def _generate_salt() -> int:
    """Cryptographically-strong 12-byte salt (matches Polymarket's range)."""
    return secrets.randbits(96)


def build_v2_signed_order(
    *,
    private_key: str,
    maker: str,
    token_id: str,
    maker_amount: str,
    taker_amount: str,
    side: Any,
    verifying_contract: str = V2_CTF_EXCHANGE,
    signer: Optional[str] = None,
    taker: str = ZERO_ADDRESS,
    expiration: str = "0",
    nonce: str = "0",
    fee_rate_bps: str = "0",
    signature_type: int = 0,
    timestamp: Optional[int] = None,
    metadata: str = ZERO_BYTES32,
    builder: str = ZERO_BYTES32,
    salt: Optional[int] = None,
    chain_id: int = POLYGON_CHAIN_ID,
) -> Dict[str, Any]:
    """
    Build and sign a Polymarket V2 order.

    Returns a dict in the exact shape that
    :meth:`aion_sdk.AionMarketClient.trade` expects under the ``order``
    key. The dict is JSON-serialisable.

    All four ``signature_type`` values are accepted (0=EOA, 1=Polymarket
    Proxy, 2=Gnosis Safe, 3=Deposit Wallet). The ``private_key`` must be
    able to produce a valid signature for the ``signer`` field — for
    smart-contract wallets this is the controlling EOA.

    Args:
        private_key: 0x-prefixed hex private key used to ECDSA-sign the
            EIP-712 hash. Never logged.
        maker: Wallet address that owns the funds.
        token_id: Polymarket CTF token id (decimal string).
        maker_amount: Amount of the maker asset in atomic units (string).
        taker_amount: Amount of the taker asset in atomic units (string).
        side: ``"BUY"``/``"SELL"`` (case-insensitive) or ``0``/``1``.
        verifying_contract: V2 Exchange contract address. Defaults to
            :data:`V2_CTF_EXCHANGE`. For neg-risk markets pass
            :data:`V2_NEG_RISK_EXCHANGE_A` or
            :data:`V2_NEG_RISK_EXCHANGE_B` as appropriate.
        signer: Address that produced the signature. Defaults to
            ``maker``. For Proxy / Safe / Deposit Wallet, ``signer`` is
            the controlling EOA while ``maker`` is the smart contract.
        taker: Specific counter-party. ``ZERO_ADDRESS`` (default) means
            "any taker".
        expiration: Unix-seconds expiration. ``"0"`` (default) = never.
        nonce: On-chain nonce for cancellation. ``"0"`` (default) is fine
            unless you batch-cancel.
        fee_rate_bps: Maker fee in basis points. ``"0"`` (default) =
            inherit market default.
        signature_type: 0=EOA, 1=Proxy, 2=Safe, 3=Deposit Wallet.
        timestamp: Unix-seconds when the order was signed. Defaults to
            ``int(time.time())``.
        metadata: 0x-prefixed 32-byte tag. Defaults to zero.
        builder: 0x-prefixed 32-byte builder tag. Defaults to zero.
        salt: Optional explicit salt. Defaults to a fresh 96-bit random.
        chain_id: EIP-712 chain id. Defaults to Polygon mainnet (137).

    Returns:
        Dict with all V2 ``order`` fields and the hex-encoded
        ``signature``, ready to be passed to ``client.trade(...)``.

    Raises:
        SigningDependencyError: If ``eth-account`` is not installed.
        ValueError: If any input is malformed.
    """
    Account, encode_typed_data = _require_eth_account()

    # Normalise / validate inputs.
    maker_addr = _normalize_address(maker, "maker")
    signer_addr = _normalize_address(signer or maker, "signer")
    taker_addr = _normalize_address(taker, "taker")
    verifying = _normalize_address(verifying_contract, "verifying_contract")
    token_id_int = _to_uint(token_id, "token_id")
    maker_amt = _to_uint(maker_amount, "maker_amount")
    taker_amt = _to_uint(taker_amount, "taker_amount")
    expiration_int = _to_uint(expiration, "expiration")
    nonce_int = _to_uint(nonce, "nonce")
    fee_rate = _to_uint(fee_rate_bps, "fee_rate_bps")
    side_int = _normalize_side(side)
    sig_type_int = int(signature_type)
    if sig_type_int not in (0, 1, 2, 3):
        raise ValueError("signature_type must be one of 0, 1, 2, 3")
    metadata_hex = _normalize_bytes32(metadata, "metadata")
    builder_hex = _normalize_bytes32(builder, "builder")
    salt_int = int(salt) if salt is not None else _generate_salt()
    if salt_int < 0:
        raise ValueError("salt must be non-negative")
    ts_int = int(timestamp) if timestamp is not None else int(time.time())
    if ts_int <= 0:
        raise ValueError("timestamp must be a positive unix-seconds value")

    message: Dict[str, Any] = {
        "salt": salt_int,
        "maker": maker_addr,
        "signer": signer_addr,
        "taker": taker_addr,
        "tokenId": token_id_int,
        "makerAmount": maker_amt,
        "takerAmount": taker_amt,
        "expiration": expiration_int,
        "nonce": nonce_int,
        "feeRateBps": fee_rate,
        "side": side_int,
        "signatureType": sig_type_int,
        "timestamp": ts_int,
        "metadata": metadata_hex,
        "builder": builder_hex,
    }

    typed_data = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "Order": _ORDER_FIELDS_V2,
        },
        "primaryType": "Order",
        "domain": {
            "name": _EIP712_DOMAIN_NAME,
            "version": _EIP712_DOMAIN_VERSION,
            "chainId": int(chain_id),
            "verifyingContract": verifying,
        },
        "message": message,
    }

    signable = encode_typed_data(full_message=typed_data)
    signed = Account.sign_message(signable, private_key=private_key)
    signature_hex: str = signed.signature.hex()
    if not signature_hex.startswith("0x"):
        signature_hex = "0x" + signature_hex

    # Backend / Polymarket CLOB expect string-encoded big numbers + uppercase side.
    return {
        "salt": str(salt_int),
        "maker": maker_addr,
        "signer": signer_addr,
        "taker": taker_addr,
        "tokenId": str(token_id_int),
        "makerAmount": str(maker_amt),
        "takerAmount": str(taker_amt),
        "side": "BUY" if side_int == _SIDE_BUY else "SELL",
        "expiration": str(expiration_int),
        "nonce": str(nonce_int),
        "feeRateBps": str(fee_rate),
        "signatureType": sig_type_int,
        "timestamp": str(ts_int),
        "metadata": metadata_hex,
        "builder": builder_hex,
        "signature": signature_hex,
    }


__all__ = [
    "POLYGON_CHAIN_ID",
    "V2_CTF_EXCHANGE",
    "V2_NEG_RISK_EXCHANGE_A",
    "V2_NEG_RISK_EXCHANGE_B",
    "ZERO_ADDRESS",
    "ZERO_BYTES32",
    "SigningDependencyError",
    "build_v2_signed_order",
]
