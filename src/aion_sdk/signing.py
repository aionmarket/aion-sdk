"""
Polymarket V2 order signing helpers.

This module is OPTIONAL and INDEPENDENT from the rest of ``aion_sdk``.

Why it exists:
    The official ``py-clob-client`` (<= 0.34.6) only signs Polymarket V1
    orders (USDC settlement). Polymarket V2 markets settle in ``pUSD``
    and use a different 11-field EIP-712 ``Order`` struct (with
    ``timestamp``, ``metadata``, ``builder`` instead of V1's ``taker``,
    ``nonce``, ``feeRateBps``). For Deposit Wallet (POLY_1271 /
    signatureType=3), this module produces ERC-7739 wrapped signatures
    using the Solady TypedDataSign pattern.

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
    All four Polymarket signature types are supported:

    * ``signatureType=0`` (EOA): ``maker`` = ``signer`` = EOA address.
    * ``signatureType=1`` (Polymarket Proxy): ``maker`` = proxy contract,
      ``signer`` = controlling EOA.
    * ``signatureType=2`` (Gnosis Safe): ``maker`` = Safe contract,
      ``signer`` = Safe owner EOA.
    * ``signatureType=3`` (Deposit Wallet / POLY_1271): ``maker`` =
      ``signer`` = deposit wallet address. The ``private_key`` is the
      controlling EOA's key. The signature is ERC-7739 wrapped.

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
import warnings
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
#: The V2 CTF Exchange contracts use ``version="2"`` (as returned by
#: the on-chain ``eip712Domain()`` getter and confirmed by
#: ``py-clob-client-v2``). V1 contracts used ``version="1"``.
_EIP712_DOMAIN_NAME: str = "Polymarket CTF Exchange"
_EIP712_DOMAIN_VERSION: str = "2"

#: V2 Order struct field layout. Field order matters — it is part of the
#: EIP-712 type hash. The V2 CTF Exchange uses an 11-field Order struct
#: that differs from V1: ``taker``/``nonce``/``feeRateBps`` are removed
#: and ``timestamp``/``metadata``/``builder`` are added.
_ORDER_FIELDS_V2 = [
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
]

#: ERC-7739 / POLY_1271 constants for Deposit Wallet signing.
_ORDER_TYPE_STRING: str = (
    "Order(uint256 salt,address maker,address signer,uint256 tokenId,"
    "uint256 makerAmount,uint256 takerAmount,uint8 side,uint8 signatureType,"
    "uint256 timestamp,bytes32 metadata,bytes32 builder)"
)
_SOLADY_TYPE_STRING: str = (
    "TypedDataSign(Order contents,string name,string version,uint256 chainId,"
    "address verifyingContract,bytes32 salt)"
    + _ORDER_TYPE_STRING
)
_DOMAIN_TYPE_STRING: str = (
    "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
)
_DEPOSIT_WALLET_NAME: str = "DepositWallet"
_DEPOSIT_WALLET_VERSION: str = "1"

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


def _require_erc7739_deps():
    """Lazy import for ERC-7739 (POLY_1271) signing dependencies."""
    try:
        from eth_abi import encode as abi_encode  # type: ignore[import-not-found]
        from eth_utils import keccak  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover
        raise SigningDependencyError(
            "ERC-7739 (POLY_1271 / Deposit Wallet) signing requires "
            "'eth-abi' and 'eth-utils'. Install with:  pip install 'aion-sdk[signing]'"
        ) from exc
    return abi_encode, keccak


def _hex_to_bytes32(hex_str: str) -> bytes:
    """Convert a 0x-prefixed hex string to a 32-byte value."""
    return bytes.fromhex(hex_str.replace("0x", "").zfill(64))


def _to_bytes32(value) -> bytes:
    """Convert a hex string or bytes value to 32 bytes."""
    if isinstance(value, bytes):
        return value
    return _hex_to_bytes32(value)


def _build_poly_1271_signature(
    *,
    message: Dict[str, Any],
    signer_addr: str,
    chain_id: int,
    exchange_address: str,
    private_key: str,
) -> str:
    """
    Build an ERC-7739 wrapped signature for POLY_1271 (Deposit Wallet).

    The Polymarket Deposit Wallet uses the Solady ERC-7739 pattern:
    ``TypedDataSign(Order contents, string name, string version,
    uint256 chainId, address verifyingContract, bytes32 salt)``

    The ``verifyingContract`` for the nested wallet domain is the deposit
    wallet address (= ``signer`` = ``maker`` in the order).
    """
    Account, _ = _require_eth_account()
    abi_encode, keccak = _require_erc7739_deps()

    order_type_hash = keccak(text=_ORDER_TYPE_STRING)
    domain_type_hash = keccak(text=_DOMAIN_TYPE_STRING)
    solady_type_hash = keccak(text=_SOLADY_TYPE_STRING)
    dw_name_hash = keccak(text=_DEPOSIT_WALLET_NAME)
    dw_version_hash = keccak(text=_DEPOSIT_WALLET_VERSION)
    exchange_name_hash = keccak(text=_EIP712_DOMAIN_NAME)
    exchange_version_hash = keccak(text=_EIP712_DOMAIN_VERSION)
    deposit_wallet_domain_salt = bytes(32)  # 0x00...00

    # App domain separator (the CTF Exchange V2 domain)
    app_domain_separator = keccak(
        primitive=abi_encode(
            ["bytes32", "bytes32", "bytes32", "uint256", "address"],
            [
                domain_type_hash,
                exchange_name_hash,
                exchange_version_hash,
                chain_id,
                exchange_address,
            ],
        )
    )

    # Hash the Order struct contents.
    contents_hash = keccak(
        primitive=abi_encode(
            [
                "bytes32",
                "uint256",
                "address",
                "address",
                "uint256",
                "uint256",
                "uint256",
                "uint8",
                "uint8",
                "uint256",
                "bytes32",
                "bytes32",
            ],
            [
                order_type_hash,
                int(message["salt"]),
                message["maker"],
                message["signer"],
                int(message["tokenId"]),
                int(message["makerAmount"]),
                int(message["takerAmount"]),
                int(message["side"]),
                int(message["signatureType"]),
                int(message["timestamp"]),
                _to_bytes32(message["metadata"]),
                _to_bytes32(message["builder"]),
            ],
        )
    )

    # TypedDataSign struct hash (Solady ERC-7739 pattern).
    typed_data_sign_struct_hash = keccak(
        primitive=abi_encode(
            [
                "bytes32",
                "bytes32",
                "bytes32",
                "bytes32",
                "uint256",
                "address",
                "bytes32",
            ],
            [
                solady_type_hash,
                contents_hash,
                dw_name_hash,
                dw_version_hash,
                chain_id,
                signer_addr,  # deposit wallet address
                deposit_wallet_domain_salt,
            ],
        )
    )

    # Final digest: \x19\x01 + appDomainSeparator + typedDataSignStructHash
    digest = keccak(
        primitive=(
            b"\x19\x01" + app_domain_separator + typed_data_sign_struct_hash
        )
    )

    # Sign with the EOA's private key.
    signed = Account._sign_hash(digest, private_key=private_key)
    inner_signature = signed.signature.hex()
    if inner_signature.startswith("0x"):
        inner_signature = inner_signature[2:]

    # Append ERC-7739 wrapper: domainSeparator + contentsHash + contentsType + len
    contents_type = _ORDER_TYPE_STRING.encode("utf-8").hex()
    contents_type_len = len(_ORDER_TYPE_STRING).to_bytes(2, "big").hex()

    return (
        "0x"
        + inner_signature
        + app_domain_separator.hex()
        + contents_hash.hex()
        + contents_type
        + contents_type_len
    )


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
    """Generate a salt matching py-clob-client-v2's range.

    The Polymarket CLOB rejects salts larger than ~41 bits.
    py-clob-client-v2 uses ``int(random.random() * timestamp_ms)``
    which caps at roughly ``1.78e12`` (~41 bits).
    We replicate that range with a CSPRNG.
    """
    import time as _time

    timestamp_ms = _time.time_ns() // 1_000_000
    # random float in [0, 1) * timestamp_ms  →  fits within ~41 bits
    return int(secrets.randbelow(timestamp_ms))


def build_v2_signed_order(
    *,
    private_key: str,
    maker: str,
    token_id: str,
    maker_amount: str,
    taker_amount: str,
    side: Any,
    verifying_contract: Optional[str] = None,
    neg_risk: Optional[bool] = None,
    signer: Optional[str] = None,
    signature_type: int = 0,
    timestamp: Optional[int] = None,
    metadata: str = ZERO_BYTES32,
    builder: str = ZERO_BYTES32,
    expiration: str = "0",
    salt: Optional[int] = None,
    chain_id: int = POLYGON_CHAIN_ID,
    # Deprecated V1 kwargs — accepted silently for backward compat.
    taker: Optional[str] = None,
    nonce: Optional[str] = None,
    fee_rate_bps: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build and sign a Polymarket V2 order (11-field EIP-712 Order struct,
    domain ``name="Polymarket CTF Exchange"`` / ``version="2"``).

    Returns a dict in the exact shape that
    :meth:`aion_sdk.AionMarketClient.trade` expects under the ``order``
    key. The dict is JSON-serialisable.

    All four ``signature_type`` values are accepted:

    * ``0`` — EOA: ``maker`` and ``signer`` are the same EOA address;
      ``signer`` may be omitted.
    * ``1`` — Polymarket Proxy: ``maker`` is the proxy contract address;
      ``signer`` MUST be the controlling EOA.
    * ``2`` — Gnosis Safe: ``maker`` is the Safe contract address;
      ``signer`` MUST be a Safe owner EOA.
    * ``3`` — Deposit Wallet (POLY_1271): ``maker`` is the deposit
      wallet contract address; ``signer`` defaults to ``maker``. The
      signature is ERC-7739 wrapped (Solady TypedDataSign pattern). The
      ``private_key`` must belong to the EOA that controls the deposit
      wallet.

    Args:
        private_key: 0x-prefixed hex private key used to sign. For
            sigType 0/1/2, the derived EOA must equal ``signer``. For
            sigType 3 (POLY_1271), this is the controlling EOA's key;
            ``signer`` is the deposit wallet address.
        maker: Wallet that owns the funds.
        token_id: Polymarket CTF token id (decimal string).
        maker_amount: Maker asset amount in atomic units (6-decimal pUSD
            for V2 markets), as a string.
        taker_amount: Taker asset amount in atomic units, as a string.
        side: ``"BUY"``/``"SELL"`` (case-insensitive) or ``0``/``1``.
        verifying_contract: V2 Exchange contract address.
        neg_risk: When ``True``, signs against
            :data:`V2_NEG_RISK_EXCHANGE_A`.
        signer: For sigType 0/1/2: the signing EOA. For sigType 3
            (POLY_1271): defaults to ``maker`` (the deposit wallet).
        signature_type: 0=EOA, 1=Proxy, 2=Safe, 3=Deposit Wallet.
        timestamp: Unix-milliseconds timestamp. Defaults to current time.
        metadata: 32-byte hex metadata. Defaults to zero bytes.
        builder: 32-byte hex builder tag. Defaults to zero bytes.
        expiration: Expiration (included in HTTP payload, not EIP-712).
        salt: Optional explicit salt. Defaults to a fresh 96-bit random.
        chain_id: EIP-712 chain id. Defaults to Polygon mainnet (137).

    Returns:
        Dict with the V2 ``order`` fields plus hex-encoded ``signature``.

    Example (Deposit Wallet on a neg-risk market)::

        signed = build_v2_signed_order(
            private_key=EOA_PK,
            maker="0x0f151e...",      # deposit wallet address
            signature_type=3,
            neg_risk=True,
            token_id="55555",
            maker_amount="5500000",
            taker_amount="10000000",
            side="BUY",
        )
    """
    Account, encode_typed_data = _require_eth_account()

    # Warn about deprecated V1 kwargs.
    if taker is not None or nonce is not None or fee_rate_bps is not None:
        warnings.warn(
            "build_v2_signed_order: taker/nonce/fee_rate_bps are V1 Order "
            "fields and are not part of the V2 EIP-712 Order struct. They "
            "are ignored. Remove them from your call site.",
            DeprecationWarning,
            stacklevel=2,
        )

    # ------------------------------------------------------------------
    # Resolve verifying_contract
    # ------------------------------------------------------------------
    if verifying_contract is None and neg_risk is None:
        verifying_contract = V2_CTF_EXCHANGE
    elif verifying_contract is None:
        verifying_contract = (
            V2_NEG_RISK_EXCHANGE_A if neg_risk else V2_CTF_EXCHANGE
        )
    elif neg_risk is not None:
        expected = V2_NEG_RISK_EXCHANGE_A if neg_risk else V2_CTF_EXCHANGE
        if verifying_contract.lower() != expected.lower():
            raise ValueError(
                f"verifying_contract={verifying_contract!r} does not match "
                f"neg_risk={neg_risk!r} (expected {expected}). Pass only one "
                f"of the two, or make sure they agree."
            )

    # ------------------------------------------------------------------
    # Normalise / validate inputs.
    # ------------------------------------------------------------------
    maker_addr = _normalize_address(maker, "maker")
    sig_type_int = int(signature_type)
    if sig_type_int not in (0, 1, 2, 3):
        raise ValueError("signature_type must be one of 0, 1, 2, 3")

    pk_eoa = Account.from_key(private_key).address

    if signer is None:
        if sig_type_int == 3:
            # POLY_1271: signer = maker = deposit wallet address.
            # The EOA signs the payload via private_key, but the order
            # struct's signer field is the deposit wallet contract
            # (matching py-clob-client-v2 behavior). The CLOB uses
            # POLY_ADDRESS header (= EOA) for API key lookup, NOT
            # order.signer.
            signer_addr = maker_addr
        elif sig_type_int != 0:
            raise ValueError(
                "signer is required when signature_type is 1 (Proxy) or "
                "2 (Safe). `maker` is the smart-contract wallet address "
                "while `signer` must be the controlling EOA."
            )
        else:
            signer_addr = maker_addr
    else:
        signer_addr = _normalize_address(signer, "signer")

    # For sigType 0/1/2: signer must match the EOA derived from
    # private_key. For sigType 3 (POLY_1271): signer is the deposit
    # wallet address (a contract), so we skip this cross-check.
    if sig_type_int != 3:
        if pk_eoa.lower() != signer_addr.lower():
            raise ValueError(
                f"signer ({signer_addr}) does not match the EOA derived from "
                f"private_key ({pk_eoa}). For Polymarket Proxy/Safe wallets, "
                f"`signer` must be the controlling EOA, NOT the wallet "
                f"contract address."
            )

    # For sigType=0 (EOA), maker MUST equal signer (= the EOA).
    if sig_type_int == 0 and maker_addr.lower() != signer_addr.lower():
        raise ValueError(
            f"For signature_type=0 (EOA), maker and signer must be the same "
            f"address (the EOA itself). Got maker={maker_addr}, "
            f"signer={signer_addr}. If your wallet is a smart contract, set "
            f"signature_type to 1 (Polymarket Proxy), 2 (Gnosis Safe), or 3 "
            f"(Deposit Wallet) and pass signer=<controlling EOA>."
        )

    verifying = _normalize_address(verifying_contract, "verifying_contract")
    token_id_int = _to_uint(token_id, "token_id")
    maker_amt = _to_uint(maker_amount, "maker_amount")
    taker_amt = _to_uint(taker_amount, "taker_amount")
    side_int = _normalize_side(side)
    salt_int = int(salt) if salt is not None else _generate_salt()
    if salt_int < 0:
        raise ValueError("salt must be non-negative")

    # timestamp defaults to current time in milliseconds.
    ts_int = int(timestamp) if timestamp is not None else (time.time_ns() // 1_000_000)
    metadata_hex = _normalize_bytes32(metadata, "metadata") if metadata else ZERO_BYTES32
    builder_hex = _normalize_bytes32(builder, "builder") if builder else ZERO_BYTES32
    expiration_int = _to_uint(expiration, "expiration")

    # V2 EIP-712 message (11 fields — no taker/nonce/feeRateBps).
    message: Dict[str, Any] = {
        "salt": salt_int,
        "maker": maker_addr,
        "signer": signer_addr,
        "tokenId": token_id_int,
        "makerAmount": maker_amt,
        "takerAmount": taker_amt,
        "side": side_int,
        "signatureType": sig_type_int,
        "timestamp": ts_int,
        "metadata": _hex_to_bytes32(metadata_hex),
        "builder": _hex_to_bytes32(builder_hex),
    }

    # ------------------------------------------------------------------
    # Sign
    # ------------------------------------------------------------------
    if sig_type_int == 3:
        # POLY_1271: ERC-7739 wrapped signature (Solady TypedDataSign).
        signature_hex = _build_poly_1271_signature(
            message=message,
            signer_addr=signer_addr,
            chain_id=int(chain_id),
            exchange_address=verifying,
            private_key=private_key,
        )
    else:
        # EOA / Proxy / Safe: standard EIP-712 signature.
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
        signature_hex = signed.signature.hex()
        if not signature_hex.startswith("0x"):
            signature_hex = "0x" + signature_hex

    # Backend / Polymarket CLOB expect string-encoded big numbers + uppercase side.
    return {
        "salt": str(salt_int),
        "maker": maker_addr,
        "signer": signer_addr,
        "tokenId": str(token_id_int),
        "makerAmount": str(maker_amt),
        "takerAmount": str(taker_amt),
        "side": "BUY" if side_int == _SIDE_BUY else "SELL",
        "signatureType": sig_type_int,
        "timestamp": str(ts_int),
        "metadata": metadata_hex,
        "builder": builder_hex,
        "expiration": str(expiration_int),
        "signature": signature_hex,
        # Convenience field: the EOA that signed this order.
        # For sigType 3 (Deposit Wallet), callers MUST pass this as
        # ``polyAddress`` in the trade payload so the backend can set
        # the ``POLY_ADDRESS`` header to the EOA (not the deposit
        # wallet contract).
        "eoaAddress": pk_eoa,
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
