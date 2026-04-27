"""
AION SDK - Python SDK for AI Agent Trading APIs

This module provides a simple interface for interacting with the AION Market AI Agent APIs.
"""

from .client import AionMarketClient, ApiError

__version__ = "0.3.1"
__author__ = "AION Market"
__all__ = ["AionMarketClient", "ApiError"]
