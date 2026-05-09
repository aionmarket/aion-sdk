"""
AION SDK - Python SDK for AI Agent Trading APIs

This module provides a simple interface for interacting with the AION Market AI Agent APIs.
"""

from .client import AionMarketClient, ApiError

# Polymarket V2 signing helpers live in an optional submodule so that the
# core SDK keeps zero runtime dependencies. Users who need V2 signing
# install the extra explicitly:  ``pip install 'aion-sdk[signing]'``.
# Importing the submodule lazily keeps ``import aion_sdk`` working even
# when ``eth-account`` is not present.
try:  # pragma: no cover - exercised only when eth-account is installed
    from .signing import (  # noqa: F401
        approve_pusd_for_fireblocks,
        build_v2_signed_order,
    )
except ImportError:  # pragma: no cover
    build_v2_signed_order = None  # type: ignore[assignment]
    approve_pusd_for_fireblocks = None  # type: ignore[assignment]

__version__ = "0.10.1"
__author__ = "AION Market"
__all__ = [
    "AionMarketClient",
    "ApiError",
    "build_v2_signed_order",
    "approve_pusd_for_fireblocks",
]
