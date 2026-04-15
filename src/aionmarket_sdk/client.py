"""
AION Market Client - Main API client for AI Agent Trading operations
"""

from __future__ import annotations

import json as jsonlib
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib import error, parse, request

# Production base URL — never changes between releases.
# Override via AIONMARKET_BASE_URL env var or explicit base_url parameter.
_PRODUCTION_URL = "https://api.aionmarket.com/bvapi"


@dataclass
class ApiError(Exception):
    """Exception raised when API returns an error"""

    message: str
    code: int = 500
    status_code: int = 500

    def __str__(self) -> str:
        return f"ApiError(code={self.code}, status={self.status_code}): {self.message}"


class AionMarketClient:
    """
    Python SDK client for the AION Market AI Agent API.

    Provides methods for agent management, market operations, wallet credentials,
    and trading operations on Polymarket prediction markets.

    Example:
        >>> from aionmarket_sdk import AionMarketClient
        >>> client = AionMarketClient(api_key="your-api-key")
        >>> agent_info = client.get_me()
        >>> markets = client.get_markets(q="bitcoin", limit=5)
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: int = 20,
    ) -> None:
        """
        Initialize the AION Market SDK client.

        Base URL resolution priority (highest to lowest):
          1. Explicit ``base_url`` parameter
          2. ``AIONMARKET_BASE_URL`` environment variable
          3. Production URL (https://api.aionmarket.com/bvapi)

        For sandbox / staging use, set the environment variable instead of
        modifying code:
            export AIONMARKET_BASE_URL="https://pm-t1.bxingupdate.com/bvapi"

        Args:
            api_key: API key for authentication. Falls back to
                ``AIONMARKET_API_KEY`` env var when not provided.
            base_url: Override the API base URL. See priority above.
            timeout: Request timeout in seconds. Defaults to 20.
        """
        self.base_url = (
            base_url
            or os.environ.get("AIONMARKET_BASE_URL")
            or _PRODUCTION_URL
        ).rstrip("/")
        self.timeout = timeout
        self.api_key = api_key or os.environ.get("AIONMARKET_API_KEY")

    def set_api_key(self, api_key: str) -> None:
        """
        Set or update the API key for authentication.

        Args:
            api_key: The API key to use for subsequent requests.
        """
        self.api_key = api_key

    def _headers(self) -> Dict[str, str]:
        """Generate request headers with authentication"""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """
        Make an HTTP request and handle errors.

        Args:
            method: HTTP method (GET, POST, etc.)
            path: API endpoint path
            params: Query parameters
            json: JSON request body

        Returns:
            Response data (dict or list)

        Raises:
            ApiError: When the API returns an error response
        """
        url = f"{self.base_url}/{path.lstrip('/')}"
        if params:
            query = parse.urlencode(params, doseq=True)
            url = f"{url}?{query}"

        payload: Optional[bytes] = None
        if json is not None:
            payload = jsonlib.dumps(json).encode("utf-8")

        req = request.Request(
            url=url,
            data=payload,
            headers=self._headers(),
            method=method.upper(),
        )

        status_code = 200
        raw_text = ""
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                status_code = resp.getcode()
                raw_text = resp.read().decode("utf-8")
        except error.HTTPError as exc:
            status_code = exc.code
            raw_text = exc.read().decode("utf-8", errors="replace")
        except error.URLError as exc:
            raise ApiError(
                message=f"Request failed: {exc.reason}",
                status_code=500,
            ) from exc

        body: Any
        try:
            body = jsonlib.loads(raw_text)
        except Exception as exc:  # pragma: no cover
            raise ApiError(
                message=f"Non-JSON response: {raw_text}",
                status_code=status_code,
            ) from exc

        if status_code >= 400:
            if isinstance(body, dict):
                raise ApiError(
                    message=str(body.get("error") or body.get("message") or body),
                    code=int(body.get("code", status_code)),
                    status_code=status_code,
                )
            raise ApiError(
                message=str(body),
                code=status_code,
                status_code=status_code,
            )

        if isinstance(body, dict) and "success" in body:
            if not body.get("success"):
                raise ApiError(
                    message=str(body.get("error") or "Request failed"),
                    code=int(body.get("code", 500)),
                    status_code=status_code,
                )
            return body.get("data")

        return body

    # ============================================================
    # Agent Management
    # ============================================================

    def register_agent(self, name: str) -> Dict[str, Any]:
        """
        Register a new AI Agent.

        Args:
            name: Name for the agent

        Returns:
            Dict containing agent_id, api_key, claim_code, etc.
        """
        return self._request("POST", "/agents/register", json={"name": name})

    def claim_preview(self, claim_code: str) -> Dict[str, Any]:
        """
        Get agent information using a claim code.

        Args:
            claim_code: Claim code from agent registration

        Returns:
            Agent preview information
        """
        return self._request("GET", f"/agents/claim/{claim_code}")

    def get_me(self) -> Dict[str, Any]:
        """
        Get current agent information using API key.

        Returns:
            Current agent's details
        """
        return self._request("GET", "/agents/me")

    def get_settings(self) -> Dict[str, Any]:
        """
        Get risk control settings for the current agent.

        Returns:
            Agent risk control settings (max trades, amount limits, etc.)
        """
        return self._request("GET", "/agents/settings")

    def update_settings(
        self,
        max_trades_per_day: Optional[int] = None,
        max_trade_amount: Optional[float] = None,
        trading_paused: Optional[bool] = None,
        auto_redeem_enabled: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Update risk control settings for the current agent.

        Args:
            max_trades_per_day: Maximum number of trades per day
            max_trade_amount: Maximum amount per single trade
            trading_paused: Whether trading is paused
            auto_redeem_enabled: Whether auto-redeem is enabled

        Returns:
            Updated settings
        """
        payload: Dict[str, Any] = {}
        if max_trades_per_day is not None:
            payload["maxTradesPerDay"] = max_trades_per_day
        if max_trade_amount is not None:
            payload["maxTradeAmount"] = max_trade_amount
        if trading_paused is not None:
            payload["tradingPaused"] = trading_paused
        if auto_redeem_enabled is not None:
            payload["autoRedeemEnabled"] = auto_redeem_enabled
        return self._request("POST", "/agents/settings", json=payload)

    def get_skills(
        self,
        category: Optional[int] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """
        Get available skills for the agent.

        Args:
            category: Optional skill category filter
            limit: Maximum results to return (default 20)
            offset: Pagination offset (default 0)

        Returns:
            List of available skills
        """
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if category is not None:
            params["category"] = category
        return self._request("GET", "/agents/skills", params=params)

    # ============================================================
    # Market Operations
    # ============================================================

    def get_markets(
        self,
        q: str,
        limit: int = 20,
        page: int = 1,
        venue: str = "polymarket",
        events_status: str = "active",
    ) -> Any:
        """
        Search for prediction markets.

        Args:
            q: Search query string
            limit: Results per page (default 20)
            page: Page number for pagination (default 1)
            venue: Market venue (default "polymarket")
            events_status: Filter by event status (default "active")

        Returns:
            List of matching markets
        """
        return self._request(
            "GET",
            "/markets",
            params={
                "q": q,
                "limit": limit,
                "page": page,
                "venue": venue,
                "eventsStatus": events_status,
            },
        )

    def get_market(self, market_id: str, venue: str = "polymarket") -> Dict[str, Any]:
        """
        Get details for a specific market.

        Args:
            market_id: Polymarket market ID
            venue: Market venue (default "polymarket")

        Returns:
            Market details
        """
        return self._request("GET", f"/markets/{market_id}", params={"venue": venue})

    def check_market_exists(self, market_id: str, venue: str = "polymarket") -> Dict[str, Any]:
        """
        Check if a market exists.

        Args:
            market_id: Polymarket market ID
            venue: Market venue (default "polymarket")

        Returns:
            Market existence check result
        """
        return self._request(
            "GET",
            "/markets/check",
            params={"marketId": market_id, "venue": venue},
        )

    def get_prices_history(
        self,
        token_id: str,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        interval: Optional[str] = None,
        fidelity: Optional[int] = None,
        venue: str = "polymarket",
    ) -> Dict[str, Any]:
        """
        Get historical price data for a market asset.

        Args:
            token_id: CLOB token ID
            start_ts: Start timestamp (optional)
            end_ts: End timestamp (optional)
            interval: Time interval (optional)
            fidelity: Data fidelity (optional)
            venue: Market venue (default "polymarket")

        Returns:
            Historical price data
        """
        params: Dict[str, Any] = {"market": token_id, "venue": venue}
        if start_ts is not None:
            params["startTs"] = start_ts
        if end_ts is not None:
            params["endTs"] = end_ts
        if interval is not None:
            params["interval"] = interval
        if fidelity is not None:
            params["fidelity"] = fidelity
        return self._request("GET", "/markets/prices-history", params=params)

    def get_briefing(
        self,
        venue: str = "polymarket",
        since: Optional[str] = None,
        user: Optional[str] = None,
        include_markets: bool = True,
    ) -> Dict[str, Any]:
        """
        Get agent briefing with risk alerts, position summary, and opportunities.

        Args:
            venue: Market venue (default "polymarket")
            since: Get updates since timestamp (optional)
            user: User address (optional)
            include_markets: Include opportunity markets (default True)

        Returns:
            Briefing data with alerts, positions, and recommendations
        """
        params: Dict[str, Any] = {
            "venue": venue,
            "includeMarkets": include_markets,
        }
        if since:
            params["since"] = since
        if user:
            params["user"] = user
        return self._request("GET", "/markets/briefing", params=params)

    def get_market_context(
        self,
        market_id: str,
        venue: str = "polymarket",
        user: Optional[str] = None,
        my_probability: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Get pre-trade market context and risk assessment.

        Recommended to call before each trade decision.

        Args:
            market_id: Market ID to get context for
            venue: Market venue (default "polymarket")
            user: User address (optional)
            my_probability: User's probability assessment (optional)

        Returns:
            Market context including details, positions, and risk info
        """
        params: Dict[str, Any] = {"venue": venue}
        if user:
            params["user"] = user
        if my_probability is not None:
            params["myProbability"] = my_probability
        return self._request("GET", f"/markets/context/{market_id}", params=params)

    # ============================================================
    # Wallet Management
    # ============================================================

    def check_wallet_credentials(self, wallet_address: str) -> Dict[str, Any]:
        """
        Check if wallet credentials are registered.

        Args:
            wallet_address: Wallet address to check

        Returns:
            Credential check result
        """
        return self._request(
            "GET",
            "/wallet/credentials/check",
            params={"walletAddress": wallet_address},
        )

    def register_wallet_credentials(
        self,
        wallet_address: str,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
    ) -> Dict[str, Any]:
        """
        Register Polymarket CLOB credentials for a wallet.

        Args:
            wallet_address: Wallet address to bind credentials to
            api_key: Polymarket CLOB API key
            api_secret: Polymarket CLOB API secret
            api_passphrase: Polymarket CLOB API passphrase

        Returns:
            Registration result
        """
        return self._request(
            "POST",
            "/wallet/credentials",
            json={
                "walletAddress": wallet_address,
                "apiKey": api_key,
                "apiSecret": api_secret,
                "apiPassphrase": api_passphrase,
            },
        )

    # ============================================================
    # Trading Operations
    # ============================================================

    def trade(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a market trade order.

        Args:
            payload: Trade order payload with market_id, side, amount, price, etc.

        Returns:
            Trade execution result with order ID and status
        """
        return self._request("POST", "/markets/trade", json=payload)

    def get_open_orders(
        self,
        venue: str = "polymarket",
        market_condition_id: Optional[str] = None,
        limit: int = 20,
    ) -> Any:
        """
        Get list of pending (unfilled) orders.

        Args:
            venue: Market venue (default "polymarket")
            market_condition_id: Filter by market condition ID (optional)
            limit: Maximum results to return (default 20)

        Returns:
            List of open orders
        """
        params: Dict[str, Any] = {"venue": venue, "limit": limit}
        if market_condition_id:
            params["marketConditionId"] = market_condition_id
        return self._request("GET", "/markets/orders/open", params=params)

    def get_order_history(
        self,
        venue: str = "polymarket",
        market_condition_id: Optional[str] = None,
        order_status: Optional[int] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Any:
        """
        Get order history with optional filters.

        Args:
            venue: Market venue (default "polymarket")
            market_condition_id: Filter by market condition ID (optional)
            order_status: Filter by order status (optional)
            limit: Maximum results to return (default 20)
            offset: Pagination offset (default 0)

        Returns:
            List of historical orders
        """
        params: Dict[str, Any] = {
            "venue": venue,
            "limit": limit,
            "offset": offset,
        }
        if market_condition_id:
            params["marketConditionId"] = market_condition_id
        if order_status is not None:
            params["orderStatus"] = order_status
        return self._request("GET", "/markets/orders", params=params)

    def get_order_detail(
        self,
        order_id: str,
        venue: str = "polymarket",
        wallet_address: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get details for a specific order.

        Args:
            order_id: Order ID (order hash)
            venue: Market venue (default "polymarket")
            wallet_address: Wallet address (optional)

        Returns:
            Order details including status, amounts, etc.
        """
        params: Dict[str, Any] = {"venue": venue}
        if wallet_address:
            params["walletAddress"] = wallet_address
        return self._request("GET", f"/markets/orders/{order_id}", params=params)

    def cancel_order(
        self,
        order_id: str,
        venue: str = "polymarket",
        wallet_address: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Cancel a single pending order.

        Args:
            order_id: Order ID to cancel
            venue: Market venue (default "polymarket")
            wallet_address: Wallet address (optional)

        Returns:
            Cancellation result
        """
        payload: Dict[str, Any] = {"orderId": order_id, "venue": venue}
        if wallet_address:
            payload["walletAddress"] = wallet_address
        return self._request("POST", "/markets/orders/cancel", json=payload)

    def cancel_all_orders(
        self,
        venue: str = "polymarket",
        wallet_address: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Cancel all pending orders for the agent.

        Args:
            venue: Market venue (default "polymarket")
            wallet_address: Wallet address (optional)

        Returns:
            Result of cancelling all orders
        """
        payload: Dict[str, Any] = {"venue": venue}
        if wallet_address:
            payload["walletAddress"] = wallet_address
        return self._request("POST", "/markets/orders/cancel-all", json=payload)

    def redeem(
        self,
        market_id: str,
        side: str,
        venue: str = "polymarket",
        wallet_address: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Claim settlement rewards (redeem) for a settled market.

        Args:
            market_id: Market ID for redemption
            side: Position side (YES/NO)
            venue: Market venue (default "polymarket")
            wallet_address: Wallet address (optional)

        Returns:
            Unsigned redemption transaction for client to sign and broadcast
        """
        payload: Dict[str, Any] = {
            "marketId": market_id,
            "side": side,
            "venue": venue,
        }
        if wallet_address:
            payload["walletAddress"] = wallet_address
        return self._request("POST", "/markets/redeem", json=payload)
