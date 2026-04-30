"""
AION Market Client - Main API client for AI Agent Trading operations
"""

from __future__ import annotations

import json as jsonlib
import os
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib import error, parse, request

# Production base URL — never changes between releases.
# Override via AIONMARKET_BASE_URL env var or explicit base_url parameter.
_PRODUCTION_URL = "https://api.aionmarket.com/bvapi"


@dataclass
class ApiError(Exception):
    """Exception raised when API returns an error response."""

    message: str
    code: int = 500
    status_code: int = 500
    response_body: Any = None
    response_headers: Optional[Dict[str, str]] = None
    url: str = ""
    method: str = ""

    def __str__(self) -> str:
        return (
            f"ApiError(code={self.code}, status={self.status_code}, "
            f"method={self.method}, url={self.url}): {self.message}"
        )


class AionMarketClient:
    """
    Python SDK client for the AION Market AI Agent API.

    Provides methods for agent management, market operations, wallet credentials,
    and trading operations on Polymarket prediction markets.

    Example:
        >>> from aion_sdk import AionMarketClient
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

    def _normalize_query_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize query params so booleans are compatible with backend validators."""
        normalized: Dict[str, Any] = {}
        for key, value in params.items():
            if isinstance(value, bool):
                normalized[key] = "true" if value else "false"
            elif isinstance(value, (list, tuple)):
                normalized[key] = [
                    "true" if item is True else "false" if item is False else item
                    for item in value
                ]
            else:
                normalized[key] = value
        return normalized

    def _normalize_trade_side_value(self, side: Any) -> str:
        """Normalize trade side into strict BUY/SELL expected by backend DTO."""
        candidate = side
        if hasattr(candidate, "value"):
            candidate = getattr(candidate, "value")

        if isinstance(candidate, bytes):
            candidate = candidate.decode("utf-8", errors="ignore")

        if not isinstance(candidate, str):
            raise ValueError("trade.order.side must be a string compatible with BUY/SELL")

        normalized = candidate.strip().upper()
        if "." in normalized:
            normalized = normalized.split(".")[-1]

        if normalized not in {"BUY", "SELL"}:
            raise ValueError("trade.order.side must be BUY or SELL")

        return normalized

    def _normalize_trade_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize payload values so SDK callers can pass lightweight enum/object types."""
        normalized = deepcopy(payload)
        order_payload = normalized.get("order")
        if not isinstance(order_payload, dict):
            raise ValueError("trade payload field 'order' must be a dict")

        order_payload["side"] = self._normalize_trade_side_value(order_payload.get("side"))

        if "signatureType" in order_payload and order_payload["signatureType"] is not None:
            try:
                order_payload["signatureType"] = int(order_payload["signatureType"])
            except (TypeError, ValueError) as exc:
                raise ValueError("trade.order.signatureType must be an integer") from exc

        raw_order_type = normalized.get("orderType")
        if raw_order_type is None or str(raw_order_type).strip() == "":
            normalized["orderType"] = (
                "FAK" if normalized.get("isLimitOrder") is False else "GTC"
            )
        else:
            normalized_order_type = str(raw_order_type).strip().upper()
            if normalized_order_type not in {"GTC", "FOK", "GTD", "FAK"}:
                raise ValueError("trade.orderType must be one of: GTC, FOK, GTD, FAK")
            normalized["orderType"] = normalized_order_type

        return normalized

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
            query = parse.urlencode(self._normalize_query_params(params), doseq=True)
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

        normalized_method = method.upper()
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                raw_text = resp.read().decode("utf-8")
                try:
                    return jsonlib.loads(raw_text)
                except Exception:
                    return raw_text
        except error.HTTPError as exc:
            raw_text = exc.read().decode("utf-8", errors="replace")
            try:
                body = jsonlib.loads(raw_text)
            except Exception:
                body = raw_text

            message = str(body)
            code = exc.code
            if isinstance(body, dict):
                message = str(
                    body.get("error")
                    or body.get("message")
                    or body.get("detail")
                    or body
                )
                body_code = body.get("code")
                if isinstance(body_code, int):
                    code = body_code

            raise ApiError(
                message=message,
                code=code,
                status_code=exc.code,
                response_body=body,
                response_headers=dict(exc.headers.items()) if exc.headers else {},
                url=url,
                method=normalized_method,
            ) from exc
        except error.URLError as exc:
            raise ApiError(
                message=f"Request failed: {exc.reason}",
                code=500,
                status_code=500,
                response_body={"reason": str(exc.reason)},
                response_headers={},
                url=url,
                method=normalized_method,
            ) from exc

    def request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Raw request passthrough for agents needing full API responses."""
        return self._request(method=method, path=path, params=params, json=json)

    # ============================================================
    # Agent Management
    # ============================================================

    def register_agent(
        self,
        name: str,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Register a new AI Agent.

        Args:
            name: Name for the agent
            description: Optional agent description (max 500 chars)

        Returns:
            Dict containing agent_id, api_key, claim_code, etc.
        """
        payload: Dict[str, Any] = {"name": name}
        if description is not None:
            payload["description"] = description
        return self._request("POST", "/agents/register", json=payload)

    def claim_preview(self, claim_code: str) -> Dict[str, Any]:
        """
        Get agent information using a claim code.

        Args:
            claim_code: Claim code from agent registration

        Returns:
            Agent preview information
        """
        return self._request("GET", "/agents/claim", params={"claimCode": claim_code})

    def get_agent_by_claim_code(self, claim_code: str) -> Dict[str, Any]:
        """
        Get public agent info by claim code.

        Returns API key details and the associated agent profile.
        No authentication required.

        Args:
            claim_code: Claim code from agent registration

        Returns:
            Agent info including API key metadata and agent details
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
        Get current trading settings and risk control configuration.

        Returns trading limits, per-trade amount caps, position value
        constraints, and risk control parameters (take-profit, stop-loss).

        Returns:
            Dict with maxTradesPerDay, maxTradeAmount, tradeLimitEnabled,
            dailyTradeAmountLimit, maxPositionValue, riskControlEnabled,
            takeProfitPercent, stopLossPercent, updatedAt
        """
        return self._request("GET", "/agents/settings")

    def update_settings(
        self,
        max_trades_per_day: Optional[int] = None,
        max_trade_amount: Optional[float] = None,
        trade_limit_enabled: Optional[bool] = None,
        daily_trade_amount_limit: Optional[float] = None,
        max_position_value: Optional[float] = None,
        risk_control_enabled: Optional[bool] = None,
        take_profit_percent: Optional[int] = None,
        stop_loss_percent: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Update trading settings and risk control configuration.

        Only the fields you provide are updated; omitted fields keep
        their current values. Changes take effect immediately.

        Args:
            max_trades_per_day: Max trades per day (1-5000).
            max_trade_amount: Max amount per single trade in USD.
            trade_limit_enabled: Enable/disable trading limits.
            daily_trade_amount_limit: Daily total trading amount cap (USD).
            max_position_value: Max value per position (USD).
            risk_control_enabled: Enable/disable risk control (stop-loss/take-profit).
            take_profit_percent: Take-profit threshold (1-100%), None to clear.
            stop_loss_percent: Stop-loss threshold (1-100%), None to clear.

        Returns:
            Updated settings
        """
        payload: Dict[str, Any] = {}
        if max_trades_per_day is not None:
            payload["maxTradesPerDay"] = max_trades_per_day
        if max_trade_amount is not None:
            payload["maxTradeAmount"] = max_trade_amount
        if trade_limit_enabled is not None:
            payload["tradeLimitEnabled"] = trade_limit_enabled
        if daily_trade_amount_limit is not None:
            payload["dailyTradeAmountLimit"] = daily_trade_amount_limit
        if max_position_value is not None:
            payload["maxPositionValue"] = max_position_value
        if risk_control_enabled is not None:
            payload["riskControlEnabled"] = risk_control_enabled
        if take_profit_percent is not None:
            payload["takeProfitPercent"] = take_profit_percent
        if stop_loss_percent is not None:
            payload["stopLossPercent"] = stop_loss_percent
        return self._request("POST", "/agents/settings", json=payload)

    # ============================================================
    # Risk Settings (per-agent risk management)
    # ============================================================

    def get_risk_settings(self) -> Dict[str, Any]:
        """
        Get current risk settings for the authenticated agent.

        Returns defaults if no custom settings have been saved yet.

        Returns:
            Risk settings including trade limits, stop-loss, take-profit, etc.
        """
        return self._request("GET", "/markets/risk-settings")

    def set_risk_settings(
        self,
        trade_limit_enabled: Optional[bool] = None,
        max_trade_amount: Optional[float] = None,
        daily_trade_amount_limit: Optional[float] = None,
        max_position_value: Optional[float] = None,
        max_trades_per_day: Optional[int] = None,
        risk_control_enabled: Optional[bool] = None,
        take_profit_percent: Optional[int] = None,
        stop_loss_percent: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Set or update risk settings for the authenticated agent.

        Only the fields you provide are updated; omitted fields keep their current value.

        Args:
            trade_limit_enabled: Enable/disable trading limits.
            max_trade_amount: Maximum amount per single trade (USD).
            daily_trade_amount_limit: Daily total trading amount cap (USD).
            max_position_value: Maximum value per position (USD).
            max_trades_per_day: Maximum trades per day.
            risk_control_enabled: Enable/disable risk control (stop-loss/take-profit).
            take_profit_percent: Take-profit threshold (1-100%), None to clear.
            stop_loss_percent: Stop-loss threshold (1-100%), None to clear.

        Returns:
            Updated risk settings.
        """
        payload: Dict[str, Any] = {}
        if trade_limit_enabled is not None:
            payload["tradeLimitEnabled"] = trade_limit_enabled
        if max_trade_amount is not None:
            payload["maxTradeAmount"] = max_trade_amount
        if daily_trade_amount_limit is not None:
            payload["dailyTradeAmountLimit"] = daily_trade_amount_limit
        if max_position_value is not None:
            payload["maxPositionValue"] = max_position_value
        if max_trades_per_day is not None:
            payload["maxTradesPerDay"] = max_trades_per_day
        if risk_control_enabled is not None:
            payload["riskControlEnabled"] = risk_control_enabled
        if take_profit_percent is not None:
            payload["takeProfitPercent"] = take_profit_percent
        if stop_loss_percent is not None:
            payload["stopLossPercent"] = stop_loss_percent
        return self._request("POST", "/markets/risk-settings", json=payload)

    def delete_risk_settings(self) -> Dict[str, Any]:
        """
        Delete custom risk settings, resetting to defaults.

        Returns:
            Result with success status and message.
        """
        return self._request("DELETE", "/markets/risk-settings")

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

    def submit_skill(
        self,
        skill_name: str,
        description: str,
        version: Optional[str] = None,
        how_it_works: Optional[str] = None,
        clawhub_url: Optional[str] = None,
        github_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Submit a skill to the review queue.

        Args:
            skill_name: Skill display name (max 100 chars)
            description: Skill description (max 1000 chars)
            version: Skill version string (optional, max 50 chars)
            how_it_works: Explanation of how the skill works (optional)
            clawhub_url: ClawHub URL (optional, max 255 chars)
            github_url: GitHub URL (optional, max 255 chars)

        Returns:
            Submitted skill info with id, skillCode, createSource, reviewStatus, createdAt
        """
        payload: Dict[str, Any] = {
            "skillName": skill_name,
            "description": description,
        }
        if version is not None:
            payload["version"] = version
        if how_it_works is not None:
            payload["howItWorks"] = how_it_works
        if clawhub_url is not None:
            payload["clawhubUrl"] = clawhub_url
        if github_url is not None:
            payload["githubUrl"] = github_url
        return self._request("POST", "/agent/skill/submit-skill", json=payload)

    def my_skills(
        self,
        category: Optional[int] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """
        Get skills submitted by the current API key owner.

        Args:
            category: Optional skill category filter
            limit: Maximum results to return (default 20)
            offset: Pagination offset (default 0)

        Returns:
            List of the user's submitted skills
        """
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if category is not None:
            params["category"] = category
        return self._request("GET", "/agent/skill/my-skills", params=params)

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
        order: Optional[str] = None,
        ascending: Optional[bool] = None,
        closed: Optional[bool] = None,
    ) -> Any:
        """
        Search for prediction markets.

        Args:
            q: Search query string
            limit: Results per page (default 20)
            page: Page number for pagination (default 1)
            venue: Market venue (default "polymarket")
            events_status: Filter by event status (default "active")
            order: Sort field (optional)
            ascending: Sort ascending (optional, default false on backend)
            closed: Include closed markets (optional, default false on backend)

        Returns:
            List of matching markets
        """
        params: Dict[str, Any] = {
            "q": q,
            "limit": limit,
            "page": page,
            "venue": venue,
            "eventsStatus": events_status,
        }
        if order is not None:
            params["order"] = order
        if ascending is not None:
            params["ascending"] = ascending
        if closed is not None:
            params["closed"] = closed
        return self._request("GET", "/markets", params=params)

    def get_market(
        self,
        market_id: str,
        venue: str = "polymarket",
        include_tag: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Get details for a specific market.

        Args:
            market_id: Polymarket market ID
            venue: Market venue (default "polymarket")
            include_tag: Include tag data (optional)

        Returns:
            Market details
        """
        params: Dict[str, Any] = {"venue": venue}
        if include_tag is not None:
            params["includeTag"] = include_tag
        return self._request("GET", f"/markets/{market_id}", params=params)

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

    def get_closed_positions(
        self,
        user: str,
        venue: str = "polymarket",
        market: Optional[str] = None,
        title: Optional[str] = None,
        limit: int = 10,
        offset: int = 0,
        sort_by: Optional[str] = None,
        sort_direction: Optional[str] = None,
    ) -> Any:
        """Get closed positions for a wallet address."""
        params: Dict[str, Any] = {
            "user": user,
            "venue": venue,
            "limit": limit,
            "offset": offset,
        }
        if market:
            params["market"] = market
        if title:
            params["title"] = title
        if sort_by:
            params["sortBy"] = sort_by
        if sort_direction:
            params["sortDirection"] = sort_direction
        return self._request("GET", "/markets/closed-positions", params=params)

    def get_current_positions(
        self,
        user: str,
        venue: str = "polymarket",
        market: Optional[str] = None,
        title: Optional[str] = None,
        size_threshold: Optional[float] = None,
        redeemable: Optional[bool] = None,
        mergeable: Optional[bool] = None,
        limit: int = 100,
        offset: int = 0,
        sort_by: Optional[str] = None,
        sort_direction: Optional[str] = None,
    ) -> Any:
        """Get current positions for a wallet address.

        Venue behavior:
            - polymarket: keeps existing Polymarket query behavior.
            - kalshi: queries local Kalshi positions stored in mk_kalshi_position.

        Notes:
            - For kalshi, `user` should be the Solana wallet address.
            - Polymarket-only filters (e.g. mergeable/redeemable) are ignored by kalshi.
        """
        params: Dict[str, Any] = {
            "user": user,
            "venue": venue,
            "limit": limit,
            "offset": offset,
        }
        if market:
            params["market"] = market
        if title:
            params["title"] = title
        if size_threshold is not None:
            params["sizeThreshold"] = size_threshold
        if redeemable is not None:
            params["redeemable"] = redeemable
        if mergeable is not None:
            params["mergeable"] = mergeable
        if sort_by:
            params["sortBy"] = sort_by
        if sort_direction:
            params["sortDirection"] = sort_direction
        return self._request("GET", "/markets/current-positions", params=params)

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

    def wallet_link_challenge(self, address: str) -> Dict[str, Any]:
        """
        Request a challenge nonce for wallet linking.

        The user must sign this challenge message to prove wallet ownership.
        Challenge expires in 5 minutes and can only be used once.

        Args:
            address: Wallet address to link (0x-prefixed EVM address)

        Returns:
            Challenge data with nonce, message, expires_at, and address
        """
        return self._request(
            "GET",
            "/wallet/link/challenge",
            params={"address": address},
        )

    def wallet_link(
        self,
        address: str,
        signature: str,
        nonce: str,
        signature_type: int = 0,
    ) -> Dict[str, Any]:
        """
        Link an external wallet after proving ownership.

        Submit the signed challenge message to link the wallet to your account.

        Args:
            address: Wallet address being linked
            signature: Signature of the challenge message
            nonce: Challenge nonce from wallet_link_challenge()
            signature_type: 0=EOA, 1=Polymarket proxy, 2=Gnosis Safe (default: 0)

        Returns:
            Link result with success, wallet_address, wallet_ownership, message, error
        """
        return self._request(
            "POST",
            "/wallet/link",
            json={
                "address": address,
                "signature": signature,
                "nonce": nonce,
                "signature_type": signature_type,
            },
        )

    def wallet_unlink(self) -> Dict[str, Any]:
        """
        Revert from self-custody back to managed wallet.

        Restores the managed wallet mode. Users can switch back and forth
        freely between managed and self-custody.

        Returns:
            Unlink result with success, message, error
        """
        return self._request("POST", "/wallet/unlink")

    def update_agent_sol_address(self, sol_address: str) -> Dict[str, Any]:
        """
        Update the agent solAddress used for Kalshi market flows.

        Backend logic:
            1. Parse api_key_code from Authorization Bearer header.
            2. Resolve mk_ai_agent_api_key.user_id and mk_ai_agent_api_key.id.
            3. Update mk_ai_agent.sol_address by (user_id, api_key_id).

        Args:
            sol_address: Solana address to be stored in mk_ai_agent.sol_address.

        Returns:
            Dict containing success flag and updated solAddress.
        """
        return self._request(
            "POST",
            "/wallet/update-sol-address",
            json={"solAddress": sol_address},
        )

    # ============================================================
    # Trading Operations
    # ============================================================

    def trade(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a market trade order on Polymarket.

        Order versions
        --------------
        - **V2 (recommended)** — settles in **pUSD** (Polymarket's ERC-20
          collateral token, 6 decimals, backed 1:1 by USDC.e). The signed order
          must carry ``signatureType=3`` and a non-zero ``timestamp``
          (unix seconds). ``metadata`` / ``builder`` default to ``bytes32(0)``
          server-side. Wallets must hold pUSD; wrap USDC.e → pUSD via the
          ``CollateralOnramp`` contract first (see Polymarket docs).
        - **V1 (legacy)** — settled directly in **USDC.e**
          (``0x2791bca1...``). Kept for backward compatibility only; new agents
          should use V2.

        The SDK auto-detects the version from the order payload (presence of
        ``timestamp``/``metadata``/``builder`` or ``signatureType==3`` => V2;
        otherwise V1 is assumed and ``nonce`` / ``feeRateBps`` become required).

        Args:
            payload: Trade order payload.

        Returns:
            Trade execution result with order ID and status
        """
        required_top_fields = [
            "marketConditionId",
            "marketQuestion",
            "orderSize",
            "price",
            "outcome",
            "order",
        ]
        missing_top = [k for k in required_top_fields if k not in payload]
        if missing_top:
            raise ValueError(
                "trade payload missing required fields: "
                + ", ".join(sorted(missing_top))
            )

        order_payload = payload.get("order")
        if not isinstance(order_payload, dict):
            raise ValueError("trade payload field 'order' must be a dict")

        required_order_fields_base = [
            "maker",
            "signer",
            "taker",
            "tokenId",
            "makerAmount",
            "takerAmount",
            "side",
            "expiration",
            "signature",
            "salt",
            "signatureType",
        ]
        missing_base = [k for k in required_order_fields_base if k not in order_payload]
        if missing_base:
            raise ValueError(
                "trade.order missing required fields: "
                + ", ".join(sorted(missing_base))
            )

        is_v2_order = (
            "timestamp" in order_payload
            or "metadata" in order_payload
            or "builder" in order_payload
            or order_payload.get("signatureType") == 3
        )

        if is_v2_order:
            # Polymarket V2 settles in pUSD (ERC-20 wrapper of USDC.e). Orders
            # require `timestamp` (seconds since epoch) and `signatureType=3`.
            # `metadata` / `builder` default to bytes32(0) server-side.
            if "timestamp" not in order_payload or str(
                order_payload.get("timestamp") or ""
            ).strip() in {"", "0"}:
                raise ValueError(
                    "V2 trade.order requires a non-zero 'timestamp' "
                    "(unix seconds when the order was signed)"
                )
        else:
            required_v1_fields = ["nonce", "feeRateBps"]
            missing_v1 = [k for k in required_v1_fields if k not in order_payload]
            if missing_v1:
                raise ValueError(
                    "V1 trade.order missing required fields: "
                    + ", ".join(sorted(missing_v1))
                )

        normalized_payload = self._normalize_trade_payload(payload)
        return self._request("POST", "/markets/trade", json=normalized_payload)

    def batch_trade(self, orders: list) -> Dict[str, Any]:
        """
        Execute multiple trade orders in a single batch request.

        Each order uses the same schema as the single trade() method.
        Orders are executed sequentially on the server side.
        Maximum 20 orders per batch.

        Args:
            orders: List of trade order payloads (same format as trade()).

        Returns:
            Batch result with total, succeeded, failed counts and per-order results.
        """
        if not orders:
            raise ValueError("batch_trade requires at least one order")
        if len(orders) > 20:
            raise ValueError("batch_trade supports a maximum of 20 orders")

        normalized_orders = [self._normalize_trade_payload(o) for o in orders]
        return self._request(
            "POST", "/markets/batch-trade", json={"orders": normalized_orders}
        )

    def kalshi_quote(
        self,
        market_ticker: str,
        side: str,
        action: str,
        amount: Optional[float] = None,
        shares: Optional[float] = None,
        user_public_key: Optional[str] = None,
        destination_wallet: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a Kalshi quote via DFlow and return unsigned transaction payload."""
        payload: Dict[str, Any] = {
            "marketTicker": market_ticker,
            "side": str(side).upper(),
            "action": str(action).upper(),
        }
        if amount is not None:
            payload["amount"] = amount
        if shares is not None:
            payload["shares"] = shares
        if user_public_key:
            payload["userPublicKey"] = user_public_key
        if destination_wallet:
            payload["destinationWallet"] = destination_wallet
        return self._request("POST", "/kalshi/agent/quote", json=payload)

    def kalshi_submit(
        self,
        market_ticker: str,
        side: str,
        action: str,
        signed_transaction: str,
        quote_id: str,
        user_public_key: str,
        amount: Optional[float] = None,
        shares: Optional[float] = None,
        destination_wallet: Optional[str] = None,
        in_amount: Optional[str] = None,
        out_amount: Optional[str] = None,
        min_out_amount: Optional[str] = None,
        skill_slug: Optional[str] = None,
        source: Optional[str] = None,
        reasoning: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Submit a signed Kalshi transaction generated from kalshi_quote().

        Args:
            market_ticker: Kalshi market ticker.
            side: 'YES' or 'NO'.
            action: 'BUY' or 'SELL'.
            signed_transaction: Base64-encoded signed Solana transaction.
            quote_id: Quote ID from kalshi_quote() response.
            user_public_key: Signing Solana wallet address.
            amount: BUY amount in USDC (required for BUY).
            shares: SELL shares (required for SELL).
            destination_wallet: Destination wallet for received tokens.
            in_amount: DFlow input amount from quote response (scaled integer string).
            out_amount: DFlow output amount from quote response (scaled integer string).
            min_out_amount: DFlow minimum output from quote response (scaled integer string).
            skill_slug: Skill identifier for strategy logging.
            source: Source tag for strategy logging.
            reasoning: Strategy reasoning text.

        Returns:
            Order confirmation with orderId, txSignature, and orderStatus.
        """
        payload: Dict[str, Any] = {
            "marketTicker": market_ticker,
            "side": str(side).upper(),
            "action": str(action).upper(),
            "signedTransaction": signed_transaction,
            "quoteId": quote_id,
            "userPublicKey": user_public_key,
        }
        if amount is not None:
            payload["amount"] = amount
        if shares is not None:
            payload["shares"] = shares
        if destination_wallet:
            payload["destinationWallet"] = destination_wallet
        if in_amount:
            payload["inAmount"] = in_amount
        if out_amount:
            payload["outAmount"] = out_amount
        if min_out_amount:
            payload["minOutAmount"] = min_out_amount
        if skill_slug:
            payload["skillSlug"] = skill_slug
        if source:
            payload["source"] = source
        if reasoning:
            payload["reasoning"] = reasoning
        return self._request("POST", "/kalshi/agent/submit", json=payload)

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
        return self._request("POST", "/markets/orders/cancel-market", json=payload)

    def cancel_all_user_orders(self) -> Dict[str, Any]:
        """
        Cancel all open orders across all wallets for the authenticated user.

        Returns:
            Results with per-wallet cancellation details, totalCanceled, syncedCount
        """
        return self._request("DELETE", "/markets/orders")

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

    def get_wallet_positions(
        self,
        wallet_address: str,
    ) -> Dict[str, Any]:
        """
        Fetch Polymarket positions for a wallet address.

        Rate limited: 60 requests/minute per API key.
        Cached for 30s per wallet address on the server.

        Args:
            wallet_address: EVM wallet address (0x-prefixed, 40 hex chars)

        Returns:
            Dict with wallet_address, position_count, positions list, and total_value
        """
        return self._request(
            "GET",
            f"/markets/wallet/{wallet_address}/positions",
        )

    # ============================================================
    # Trades & Leaderboard
    # ============================================================

    def get_trades(
        self,
        venue: str = "all",
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """
        Get trade history for the authenticated agent.

        Returns trades from the agent's strategy log.
        venue='all' (default): merged trades across polymarket + kalshi.
        venue='polymarket': Polymarket trades only.
        venue='kalshi': Kalshi trades only.

        Args:
            venue: Venue filter — 'all', 'polymarket', or 'kalshi'
            limit: Max trades to return (1–200, default 50)
            offset: Offset for pagination (default 0)

        Returns:
            Dict with total, limit, offset, venue, and trades list
        """
        params: Dict[str, Any] = {
            "venue": venue,
            "limit": limit,
            "offset": offset,
        }
        return self._request("GET", "/agents/trades", params=params)

    def get_leaderboard(
        self,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """
        Get SDK agent leaderboard ranked by total P&L.

        Returns agents sorted by total_pnl descending with their
        performance metrics, trade counts, and win rates.

        Args:
            limit: Max entries to return (1–100, default 50)

        Returns:
            Dict with entries list and total_agents count
        """
        params: Dict[str, Any] = {"limit": limit}
        return self._request("GET", "/agents/leaderboard", params=params)

    # ============================================================
    # Utilities
    # ============================================================

    def health(self) -> Dict[str, Any]:
        """
        Lightweight health check — no auth, no DB, no external calls.

        Useful for monitoring, heartbeat detection, load balancer probes,
        or confirming the API is reachable before your agent starts.

        Returns:
            Dict with status ('ok') and ISO 8601 timestamp
        """
        return self._request("GET", "/agents/health")

    # ============================================================
    # Positions & Portfolio
    # ============================================================

    def get_positions_expiring(
        self,
        hours: int = 24,
        venue: str = "all",
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """
        Get positions in markets that are active within a time window.

        Merges data from Polymarket and Kalshi, sorted by soonest
        activity. Useful for pre-resolution position review and exit
        planning.

        Args:
            hours: Time window in hours (1-168, default 24)
            venue: Venue filter — 'all', 'polymarket', or 'kalshi'
            limit: Max results to return (1-200, default 50)
            offset: Pagination offset (default 0)

        Returns:
            Dict with positions list, total, hours, and venue
        """
        params: Dict[str, Any] = {
            "hours": hours,
            "venue": venue,
            "limit": limit,
            "offset": offset,
        }
        return self._request("GET", "/markets/positions/expiring", params=params)

    def get_portfolio(
        self,
        venue: str = "all",
    ) -> Dict[str, Any]:
        """
        Get portfolio summary with exposure and concentration metrics.

        Returns per-venue buckets (polymarket, kalshi) with balance,
        pnl, positions_count, and total_exposure, plus totals,
        concentration metrics, and risk warnings.

        Args:
            venue: Venue filter — 'all', 'polymarket', or 'kalshi'

        Returns:
            Dict with per-venue buckets, total, concentration, warnings
        """
        params: Dict[str, Any] = {"venue": venue}
        return self._request("GET", "/markets/portfolio", params=params)
