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
#: NOTE: although the contract's ERC-5267 ``eip712Domain()`` getter
#: returns ``version="2"``, the actual ``_hashTypedDataV4`` inside the
#: Polymarket CTF Exchange uses ``version="1"`` (see Polymarket's
#: ``py-order-utils.builders.base_builder._get_domain_separator``). The
#: signed digest must use the version that matches the on-chain
#: ``DOMAIN_SEPARATOR``, i.e. ``"1"``. Using ``"2"`` produced a digest
#: that did not match and caused the CLOB to reject every order with
#: ``Invalid order payload``.
_EIP712_DOMAIN_NAME: str = "Polymarket CTF Exchange"
_EIP712_DOMAIN_VERSION: str = "1"

#: V2 Order struct field layout. Field order matters — it is part of the
#: EIP-712 type hash. The Polymarket CTF Exchange V2 (and Neg-Risk
#: variants) verify a 12-field Order struct identical to V1. ``timestamp``
#: / ``metadata`` / ``builder`` are HTTP-payload extensions used by the
#: CLOB book; they are NOT part of the on-chain Order struct and MUST
#: NOT be included in the EIP-712 typed data, otherwise the recovered
#: signer will not match and the CLOB rejects the order with
#: ``Invalid order payload``.
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
    verifying_contract: Optional[str] = None,
    neg_risk: Optional[bool] = None,
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
    Build and sign a Polymarket V2 order (12-field EIP-712 Order struct,
    domain ``name="Polymarket CTF Exchange"`` / ``version="1"``).

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
      wallet contract address (e.g. ``0xC4378...``); ``signer`` MUST be
      the controlling EOA.

    For Polymarket "neg-risk" markets, the V2 verifying contract is
    different from vanilla CTF Exchange. Either:

    * pass ``neg_risk=True`` (recommended) to let the SDK pick
      :data:`V2_NEG_RISK_EXCHANGE_A` automatically, or
    * pass an explicit ``verifying_contract=...``.

    Picking the wrong exchange (vanilla vs neg-risk) is the #1 cause of
    ``Invalid order payload`` rejections from the CLOB.

    Args:
        private_key: 0x-prefixed hex private key used to ECDSA-sign the
            EIP-712 hash. Never logged. The address derived from this
            key MUST equal ``signer``.
        maker: Wallet that owns the funds. For sigType=0 this is the
            EOA itself; for sigType=1/2/3 this is the smart-contract
            wallet.
        token_id: Polymarket CTF token id (decimal string).
        maker_amount: Maker asset amount in atomic units (6-decimal pUSD
            for V2 markets), as a string.
        taker_amount: Taker asset amount in atomic units, as a string.
        side: ``"BUY"``/``"SELL"`` (case-insensitive) or ``0``/``1``.
        verifying_contract: V2 Exchange contract address. Mutually
            exclusive with ``neg_risk``. Defaults to
            :data:`V2_CTF_EXCHANGE` if neither is supplied.
        neg_risk: When ``True``, signs against
            :data:`V2_NEG_RISK_EXCHANGE_A`; when ``False`` (or unset),
            signs against :data:`V2_CTF_EXCHANGE`. Determine this from
            the market metadata's ``neg_risk`` field (returned by
            Polymarket gamma-api / our market list endpoint).
        signer: Address that produced the signature. REQUIRED when
            ``signature_type != 0``; for sigType=0 it defaults to
            ``maker``. Always the controlling EOA, never the smart
            contract.
        taker: Specific counter-party. ``ZERO_ADDRESS`` (default) means
            "any taker".
        expiration: Unix-seconds expiration. ``"0"`` (default) = never.
        nonce: On-chain nonce for cancellation. ``"0"`` is fine unless
            you batch-cancel.
        fee_rate_bps: Maker fee in basis points. ``"0"`` (default) =
            inherit market default.
        signature_type: 0=EOA, 1=Proxy, 2=Safe, 3=Deposit Wallet.
        timestamp: DEPRECATED. Ignored \u2014 not part of the on-chain Order
            struct. Kept for back-compat with aion-sdk \u2264 0.10.1 callers.
        metadata: DEPRECATED. Ignored.
        builder: DEPRECATED. Ignored.
        salt: Optional explicit salt. Defaults to a fresh 96-bit random.
        chain_id: EIP-712 chain id. Defaults to Polygon mainnet (137).

    Returns:
        Dict with the 12 V2 ``order`` fields plus the hex-encoded
        ``signature``, ready to be passed to ``client.trade(...)``.

    Raises:
        SigningDependencyError: If ``eth-account`` is not installed.
        ValueError: If any input is malformed, ``signer`` is missing for
            sigType != 0, or ``signer`` does not match the EOA derived
            from ``private_key``.

    Example (Deposit Wallet on a neg-risk market):

    >>> from aion_sdk.signing import build_v2_signed_order
    >>> signed = build_v2_signed_order(
    ...     private_key=EOA_PK,
    ...     maker="0xC4378BFEe30dBAc2A907ea1E486acCC78B02c185",  # deposit wallet
    ...     signer=EOA_ADDRESS,                                   # controlling EOA
    ...     signature_type=3,
    ...     neg_risk=True,
    ...     token_id="55555",
    ...     maker_amount="5500000",
    ...     taker_amount="10000000",
    ...     side="BUY",
    ... )
    """
    Account, encode_typed_data = _require_eth_account()

    # ------------------------------------------------------------------
    # Resolve verifying_contract
    # ------------------------------------------------------------------
    # Polymarket has two V2 exchange families on Polygon:
    #   * Vanilla CTF Exchange     -> 0xE111180000d2663C0091e4f400237545B87B996B
    #   * Neg-Risk CTF Exchange A  -> 0xe2222d279d744050d28e00520010520000310F59
    # The signed digest must use the same exchange the market settles on.
    # Signing a neg-risk market against the vanilla exchange (or vice
    # versa) produces a digest the on-chain Exchange.verifyOrder cannot
    # recover, and the CLOB rejects with ``Invalid order payload``.
    if verifying_contract is None and neg_risk is None:
        # Backward compatible default: vanilla CTF Exchange.
        verifying_contract = V2_CTF_EXCHANGE
    elif verifying_contract is None:
        verifying_contract = (
            V2_NEG_RISK_EXCHANGE_A if neg_risk else V2_CTF_EXCHANGE
        )
    elif neg_risk is not None:
        # Both supplied — sanity-check they agree, otherwise the caller
        # almost certainly has a mismatched market/contract pair.
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

    # For sigType in {1,2,3} (Polymarket Proxy / Gnosis Safe / Deposit
    # Wallet) ``maker`` is the smart-contract wallet that holds the
    # funds and ``signer`` MUST be the controlling EOA whose private key
    # produced the signature. Defaulting ``signer = maker`` here would
    # generate a signature that recovers to the EOA but be advertised as
    # signed-by-the-contract — the CLOB then rejects with
    # ``Invalid order payload`` (the cause of every reported deposit
    # wallet trade failure prior to aion-sdk 0.10.3). Force callers to
    # be explicit so the trap goes away.
    if signer is None:
        if sig_type_int != 0:
            raise ValueError(
                "signer is required when signature_type != 0. For Polymarket "
                "Proxy (1) / Gnosis Safe (2) / Deposit Wallet (3), `maker` is "
                "the smart-contract wallet address while `signer` must be the "
                "controlling EOA whose private_key is signing this order."
            )
        signer_addr = maker_addr
    else:
        signer_addr = _normalize_address(signer, "signer")

    # Cross-check: the signature is produced by ``private_key``. Recover
    # that EOA and ensure it matches ``signer`` — catches the very
    # common ``private_key from EOA-A but signer=EOA-B`` mistake.
    pk_eoa = Account.from_key(private_key).address
    if pk_eoa.lower() != signer_addr.lower():
        raise ValueError(
            f"signer ({signer_addr}) does not match the EOA derived from "
            f"private_key ({pk_eoa}). For Polymarket Proxy/Safe/Deposit "
            f"wallets, `signer` must be the controlling EOA, NOT the wallet "
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

    taker_addr = _normalize_address(taker, "taker")
    verifying = _normalize_address(verifying_contract, "verifying_contract")
    token_id_int = _to_uint(token_id, "token_id")
    maker_amt = _to_uint(maker_amount, "maker_amount")
    taker_amt = _to_uint(taker_amount, "taker_amount")
    expiration_int = _to_uint(expiration, "expiration")
    nonce_int = _to_uint(nonce, "nonce")
    fee_rate = _to_uint(fee_rate_bps, "fee_rate_bps")
    side_int = _normalize_side(side)
    salt_int = int(salt) if salt is not None else _generate_salt()
    if salt_int < 0:
        raise ValueError("salt must be non-negative")

    # NOTE: ``timestamp`` / ``metadata`` / ``builder`` are still accepted
    # as kwargs for backward compatibility with callers written against
    # aion-sdk <= 0.10.1, but they are NOT part of the EIP-712 typed
    # data hashed by the Polymarket CTF Exchange V2 contracts. Including
    # them caused the recovered signer to mismatch and the CLOB to
    # reject every V2 order with ``Invalid order payload``. We silently
    # ignore them here so old callers keep working.
    if (
        timestamp is not None
        or metadata not in (None, ZERO_BYTES32)
        or builder not in (None, ZERO_BYTES32)
    ):
        warnings.warn(
            "build_v2_signed_order: timestamp/metadata/builder are not part "
            "of the Polymarket CTF Exchange V2 EIP-712 Order struct and are "
            "ignored. Remove them from your call site.",
            DeprecationWarning,
            stacklevel=2,
        )

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
    "approve_pusd_for_fireblocks",
]


# ---------------------------------------------------------------------------
# EOA pUSD approve helper (one-time setup so the platform Fireblocks Vault
# can pull trade fees via ``transferFrom``)
# ---------------------------------------------------------------------------


_DEFAULT_PUSD_TOKEN_ADDRESS: str = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
_MAX_UINT256: int = (1 << 256) - 1
_DEFAULT_POLYGON_RPC: str = "https://polygon-rpc.com"


def approve_pusd_for_fireblocks(
    *,
    private_key: str,
    spender: str,
    amount: int = _MAX_UINT256,
    token_address: str = _DEFAULT_PUSD_TOKEN_ADDRESS,
    rpc_url: str = _DEFAULT_POLYGON_RPC,
    gas_limit: int = 100_000,
    chain_id: int = POLYGON_CHAIN_ID,
    skip_if_already_sufficient: bool = True,
) -> Dict[str, Any]:
    """
    Sign and broadcast ``pUSD.approve(spender, amount)`` from an EOA wallet.

    This is the one-time on-chain setup an EOA agent must run before the
    platform can charge trade fees via
    ``POST /aiagent/charge-fee/polymarket/eoa-trade-fee``. After it
    succeeds, the platform Fireblocks Vault is authorized to pull up to
    ``amount`` pUSD from the EOA via ``transferFrom`` whenever a trade
    fee is owed.

    What you are approving:
      * **Token**   pUSD on Polygon (``0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB``).
        NOT USDC, NOT USDC.e \u2014 those are different ERC-20 contracts and
        an approve to either of them will leave
        ``pUSD.allowance(eoa, vault) = 0``.
      * **Spender** the platform **Fireblocks Vault Polygon address** returned by
        :meth:`AionMarketClient.get_polymarket_eoa_spender`. NOT the
        ``platformFeeAddress`` field \u2014 although the two values currently
        happen to be equal, that is implementation-defined and may change.
        Always pass the ``spender`` field through.
      * **Amount**  raw token units. **Recommended default: ``1_000 * 10**6``
        (= 1000 pUSD)**, which covers ~100,000 USD of cumulative trade
        volume at the 1% fee rate. The default of ``MAX_UINT256``
        (unlimited) is also safe and means the EOA never has to re-approve.
      * **Chain**   Polygon mainnet (chainId 137).

    Idempotency: when ``skip_if_already_sufficient=True`` (the default) the
    helper first reads the current on-chain allowance and returns
    ``{"status": "skipped", ...}`` without sending any transaction whenever
    the existing allowance already covers ``amount``. This makes it safe
    to call before every trade.

    Quick start::

        from aion_sdk import AionMarketClient, approve_pusd_for_fireblocks

        client = AionMarketClient(api_key="YOUR_API_KEY")

        # 1. Resolve spender + token address dynamically (do NOT hard-code).
        info = client.get_polymarket_eoa_spender()

        # 2. Approve 1000 pUSD (idempotent: no-op when allowance is already enough).
        result = approve_pusd_for_fireblocks(
            private_key=EOA_PRIVATE_KEY,
            spender=info["spender"],            # MUST be the Vault, not platformFeeAddress
            token_address=info["tokenAddress"], # pUSD on Polygon
            amount=1_000 * 10**6,               # 1000 pUSD; covers ~100k USD trade volume
        )
        print(result)
        # {"status": "ok",      "txHash": "0x...", "amount": 1_000_000_000, ...}  on a fresh approve
        # {"status": "skipped", "currentAllowance": <int>, ...}                   when already sufficient

    Args:
        private_key: User's EOA private key (hex string, optionally 0x-prefixed).
        spender: Platform Fireblocks Vault Polygon address. Always read
            this from
            :meth:`AionMarketClient.get_polymarket_eoa_spender`\\ ``["spender"]``;
            never hard-code it. Passing ``platformFeeAddress`` here will
            cause subsequent fee charging to fail with ``\u6388\u6743\u989d\u5ea6\u4e0d\u8db3``.
        amount: Allowance to grant in **raw token units** (no decimals
            applied). pUSD has 6 decimals, so ``1_000 * 10**6`` = 1000 pUSD.
            Defaults to ``2**256-1`` (unlimited) so a single approve covers
            all future trades.
        token_address: pUSD ERC-20 contract address. Defaults to the
            Polymarket V2 collateral token on Polygon. Override only if
            you explicitly read a different ``tokenAddress`` from
            :meth:`AionMarketClient.get_polymarket_eoa_spender`.
        rpc_url: Polygon JSON-RPC endpoint. Defaults to the public
            ``https://polygon-rpc.com``.
        gas_limit: Transaction gas limit.
        chain_id: EVM chain ID (default: 137, Polygon mainnet).
        skip_if_already_sufficient: If True (default), check the current
            allowance first and return ``{"status": "skipped", ...}``
            when ``allowance(owner, spender) >= amount``. Set to False
            to force a fresh approve transaction even when not needed.

    Returns:
        ``{"status": "ok", "txHash": "0x...", "owner": "0x...",
        "spender": "0x...", "tokenAddress": "0x...", "amount": <int>,
        "chainId": 137}`` on a freshly-broadcast transaction, or
        ``{"status": "skipped", "reason": "allowance already sufficient",
        "currentAllowance": <int>, "requestedAmount": <int>, ...}`` when
        the existing allowance already covers ``amount``.

    Raises:
        SigningDependencyError: If ``eth-account`` or ``web3`` is not
            installed. Install with ``pip install 'aion-sdk[signing]'``.
        RuntimeError: If the Polygon RPC is unreachable or broadcasting
            fails. The EOA must hold a small amount of MATIC to pay gas
            for this one-time transaction.
    """
    Account, _encode_typed_data = _require_eth_account()
    try:
        from web3 import Web3  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover
        raise SigningDependencyError(
            "approve_pusd_for_fireblocks requires 'web3'. "
            "Install with:  pip install 'aion-sdk[signing]'"
        ) from exc

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():  # pragma: no cover - network
        raise RuntimeError(f"unable to reach Polygon RPC at {rpc_url}")

    account = Account.from_key(private_key)
    owner = account.address
    spender_checksum = Web3.to_checksum_address(spender)
    token_checksum = Web3.to_checksum_address(token_address)

    erc20_abi = [
        {
            "name": "approve",
            "type": "function",
            "stateMutability": "nonpayable",
            "inputs": [
                {"name": "spender", "type": "address"},
                {"name": "amount", "type": "uint256"},
            ],
            "outputs": [{"name": "", "type": "bool"}],
        },
        {
            "name": "allowance",
            "type": "function",
            "stateMutability": "view",
            "inputs": [
                {"name": "owner", "type": "address"},
                {"name": "spender", "type": "address"},
            ],
            "outputs": [{"name": "", "type": "uint256"}],
        },
    ]
    contract = w3.eth.contract(address=token_checksum, abi=erc20_abi)

    if skip_if_already_sufficient:
        try:
            current = int(
                contract.functions.allowance(owner, spender_checksum).call()
            )
        except Exception:  # pragma: no cover - network
            current = 0
        if current >= amount:
            return {
                "status": "skipped",
                "reason": "allowance already sufficient",
                "owner": owner,
                "spender": spender_checksum,
                "tokenAddress": token_checksum,
                "currentAllowance": current,
                "requestedAmount": amount,
            }

    nonce = w3.eth.get_transaction_count(owner)
    gas_price = w3.eth.gas_price
    tx = contract.functions.approve(
        spender_checksum, amount
    ).build_transaction(
        {
            "from": owner,
            "nonce": nonce,
            "gas": gas_limit,
            "gasPrice": gas_price,
            "chainId": chain_id,
        }
    )

    signed = account.sign_transaction(tx)
    raw_tx = getattr(signed, "raw_transaction", None) or getattr(
        signed, "rawTransaction"
    )
    tx_hash = w3.eth.send_raw_transaction(raw_tx)
    return {
        "status": "ok",
        "txHash": tx_hash.hex() if hasattr(tx_hash, "hex") else str(tx_hash),
        "owner": owner,
        "spender": spender_checksum,
        "tokenAddress": token_checksum,
        "amount": amount,
        "chainId": chain_id,
    }
