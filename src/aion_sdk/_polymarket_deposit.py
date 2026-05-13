"""
Polymarket Deposit Wallet derivation (POLY_1271, signatureType=3).

Polymarket's new-onboarding "Deposit Wallet" is an ERC-1967 minimal proxy
deployed counterfactually via CREATE2. Its address depends ONLY on the
user's EOA plus two fixed Polymarket contract constants, so we can derive
it locally without any network call — exactly what the front-end does
when ``GET /api/profile/userData`` returns 404 for a new EOA.

This is a faithful port of:
    @polymarket/builder-relayer-client@0.0.9
    dist/builder/derive.js  →  deriveDepositWallet(owner, factory, impl)
    dist/config/index.js    →  POL.DepositWalletContracts (chainId=137)

The derived address is "counterfactual" — the proxy contract is not
deployed on-chain until the user (or Polymarket's relayer) first
interacts with it. Funds sent to this address are safe; orders signed
under POLY_1271 are accepted by Polymarket once the wallet is funded
and the relayer deploys the proxy on the user's behalf.

Pure-Python keccak-256 is bundled here so the SDK has zero runtime
dependencies. Verified against the official Keccak-256 test vectors.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Polygon mainnet (chainId = 137) Deposit Wallet contracts.
# Sourced from @polymarket/builder-relayer-client@0.0.9 dist/config/index.js.
# These are the addresses the Polymarket front-end uses in production.
# ---------------------------------------------------------------------------
POLYMARKET_DEPOSIT_WALLET_FACTORY = "0x00000000000Fb5C9ADea0298D729A0CB3823Cc07"
POLYMARKET_DEPOSIT_WALLET_IMPLEMENTATION = (
    "0x58CA52ebe0DadfdF531Cde7062e76746de4Db1eB"
)

# Solady LibClone ERC-1967 minimal-proxy template constants.
# Byte-for-byte identical to the JS reference.
_ERC1967_CONST1 = bytes.fromhex(
    "cc3735a920a3ca505d382bbc545af43d6000803e6038573d6000fd5b3d6000f3"
)
_ERC1967_CONST2 = bytes.fromhex(
    "5155f3363d3d373d3d363d7f360894a13ba1a3210667c828492db98dca3e2076"
)
_ERC1967_PREFIX = 0x61003D3D8160233D3973  # 10-byte big-endian


# ---------------------------------------------------------------------------
# Minimal pure-Python keccak-256 (FIPS-202 draft / Ethereum flavour).
# NOTE: hashlib.sha3_256 is the NIST-finalized SHA3 with different padding
# and cannot be used here. Do not "simplify" this away.
# ---------------------------------------------------------------------------

_KECCAK_ROUND_CONSTANTS = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)

_KECCAK_ROTATIONS = (
    (0, 36, 3, 41, 18),
    (1, 44, 10, 45, 2),
    (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56),
    (27, 20, 39, 8, 14),
)


def _rotl64(x: int, n: int) -> int:
    n &= 63
    return ((x << n) | (x >> (64 - n))) & 0xFFFFFFFFFFFFFFFF


def _keccak_f1600(state: list) -> None:
    for rc in _KECCAK_ROUND_CONSTANTS:
        # θ
        c = [state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20]
             for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rotl64(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(0, 25, 5):
                state[x + y] ^= d[x]
        # ρ and π
        b = [0] * 25
        for x in range(5):
            for y in range(5):
                b[y + ((2 * x + 3 * y) % 5) * 5] = _rotl64(
                    state[x + 5 * y], _KECCAK_ROTATIONS[x][y]
                )
        # χ
        for y in range(0, 25, 5):
            t = [b[y + x] for x in range(5)]
            for x in range(5):
                state[x + y] = t[x] ^ ((~t[(x + 1) % 5]) & t[(x + 2) % 5]) & 0xFFFFFFFFFFFFFFFF
        # ι
        state[0] ^= rc


def keccak256(data: bytes) -> bytes:
    """Pure-Python Keccak-256 (Ethereum-style, NOT SHA3-256)."""
    rate_bytes = 136  # (1600 - 2*256) / 8
    # Pad with Keccak (NOT SHA3) suffix: 0x01 ... 0x80
    pad = bytearray(data)
    pad.append(0x01)
    while len(pad) % rate_bytes != 0:
        pad.append(0x00)
    pad[-1] |= 0x80

    state = [0] * 25
    for offset in range(0, len(pad), rate_bytes):
        block = pad[offset:offset + rate_bytes]
        for i in range(rate_bytes // 8):
            lane = int.from_bytes(block[i * 8:(i + 1) * 8], "little")
            state[i] ^= lane
        _keccak_f1600(state)

    out = bytearray()
    for i in range(4):  # 4 lanes = 32 bytes
        out += state[i].to_bytes(8, "little")
    return bytes(out)


# ---------------------------------------------------------------------------
# EIP-55 checksum + CREATE2
# ---------------------------------------------------------------------------

def _hex_to_bytes(addr: str) -> bytes:
    s = addr.lower()
    if s.startswith("0x"):
        s = s[2:]
    return bytes.fromhex(s)


def _to_checksum_address(addr_bytes: bytes) -> str:
    addr_hex = addr_bytes.hex()
    hash_hex = keccak256(addr_hex.encode("ascii")).hex()
    out = ["0x"]
    for i, ch in enumerate(addr_hex):
        if ch.isdigit():
            out.append(ch)
        else:
            out.append(ch.upper() if int(hash_hex[i], 16) >= 8 else ch.lower())
    return "".join(out)


def _create2_address(factory: str, salt: bytes, bytecode_hash: bytes) -> str:
    data = b"\xff" + _hex_to_bytes(factory) + salt + bytecode_hash
    return _to_checksum_address(keccak256(data)[-20:])


def _init_code_hash_erc1967(implementation: str, args: bytes) -> bytes:
    n = len(args)
    combined = _ERC1967_PREFIX + (n << 56)
    combined_bytes = combined.to_bytes(10, "big")
    blob = (
        combined_bytes
        + _hex_to_bytes(implementation)
        + bytes.fromhex("6009")
        + _ERC1967_CONST2
        + _ERC1967_CONST1
        + args
    )
    return keccak256(blob)


def derive_polymarket_deposit_wallet(eoa_address: str) -> str:
    """
    Locally derive the Polymarket POLY_1271 Deposit Wallet for an EOA.

    Matches the front-end's ``deriveDepositWalletAddress(eoa)`` from
    ``apps/prediction-market-starter-kit/lib/polymarket/deposit-wallet.ts``
    exactly — no network call required.

    Args:
        eoa_address: 0x-prefixed EOA address (42 chars).

    Returns:
        EIP-55 checksummed Deposit Wallet address (counterfactual until
        first on-chain interaction by the Polymarket relayer).
    """
    factory = POLYMARKET_DEPOSIT_WALLET_FACTORY
    implementation = POLYMARKET_DEPOSIT_WALLET_IMPLEMENTATION
    owner_bytes = _hex_to_bytes(eoa_address)
    if len(owner_bytes) != 20:
        raise ValueError(
            f"derive_polymarket_deposit_wallet: expected 20-byte address, "
            f"got {len(owner_bytes)} bytes from {eoa_address!r}"
        )
    wallet_id = owner_bytes.rjust(32, b"\x00")
    # abi.encode(["address","bytes32"], [factory, walletId])
    args = _hex_to_bytes(factory).rjust(32, b"\x00") + wallet_id
    salt = keccak256(args)
    bytecode_hash = _init_code_hash_erc1967(implementation, args)
    return _create2_address(factory, salt, bytecode_hash)
