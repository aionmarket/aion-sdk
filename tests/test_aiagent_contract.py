from __future__ import annotations

import inspect
import json
import re
from pathlib import Path
from typing import Any, Dict, Set
from unittest.mock import patch

import pytest

from aion_sdk.client import AionMarketClient


# ────────────────────────────────────────────
# Helpers: workspace & TS source parsing
# ────────────────────────────────────────────


def _workspace_root() -> Path:
    # aion-sdk/tests -> aion-sdk -> bv-market-front
    return Path(__file__).resolve().parents[2]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_class_block(source: str, class_name: str) -> str:
    marker = f"export class {class_name}"
    start = source.find(marker)
    if start < 0:
        raise AssertionError(f"Class not found: {class_name}")

    brace_start = source.find("{", start)
    if brace_start < 0:
        raise AssertionError(f"Class block not found: {class_name}")

    depth = 0
    for idx in range(brace_start, len(source)):
        ch = source[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[brace_start + 1 : idx]

    raise AssertionError(f"Unclosed class block: {class_name}")


def _parse_ts_fields(class_block: str) -> dict[str, bool]:
    """Return field -> is_optional from TS class property definitions."""
    field_re = re.compile(
        r"^\s*(?!@)([A-Za-z_][A-Za-z0-9_]*)\s*(\?)?\s*!?\s*:\s*"
        r"[A-Za-z_][A-Za-z0-9_<>,\[\]\s|.]*\s*;",
        re.M,
    )
    parsed: dict[str, bool] = {}
    for match in field_re.finditer(class_block):
        field_name = match.group(1)
        optional = bool(match.group(2))
        parsed[field_name] = optional
    if not parsed:
        raise AssertionError("No TS fields parsed from class block")
    return parsed


# ────────────────────────────────────────────
# Helpers: DTO source path loaders
# ────────────────────────────────────────────

_DTO_ROOT = (
    Path("apps") / "api" / "src" / "modules" / "aiagent" / "dto"
)
_KALSHI_DTO_ROOT = (
    Path("apps") / "api" / "src" / "modules" / "kalshi" / "dto"
)


def _dto_path(filename: str) -> Path:
    return _workspace_root() / _DTO_ROOT / filename


def _kalshi_dto_path(filename: str) -> Path:
    return _workspace_root() / _KALSHI_DTO_ROOT / filename


def _get_dto_fields(filename: str, class_name: str) -> dict[str, bool]:
    """Extract fields from a DTO class in the given file."""
    source = _read(_dto_path(filename))
    block = _extract_class_block(source, class_name)
    return _parse_ts_fields(block)


def _get_kalshi_dto_fields(filename: str, class_name: str) -> dict[str, bool]:
    source = _read(_kalshi_dto_path(filename))
    block = _extract_class_block(source, class_name)
    return _parse_ts_fields(block)


# ────────────────────────────────────────────
# Helpers: SDK method parameter inspection
# ────────────────────────────────────────────


def _sdk_method_params(method_name: str) -> Set[str]:
    """Return the set of parameter names for an SDK method (excluding 'self')."""
    method = getattr(AionMarketClient, method_name)
    sig = inspect.signature(method)
    return {
        name
        for name, param in sig.parameters.items()
        if name != "self"
    }


def _snake_to_camel(name: str) -> str:
    """Convert snake_case to camelCase."""
    parts = name.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


# ────────────────────────────────────────────
# Helpers: HTTP capture for verifying actual payloads
# ────────────────────────────────────────────


class _CapturedRequest:
    """Stores the captured HTTP request details."""

    def __init__(self) -> None:
        self.url: str = ""
        self.method: str = ""
        self.body: Dict[str, Any] | None = None
        self.query_params: Dict[str, str] = {}

    def read(self) -> bytes:
        return json.dumps({"code": 0, "data": {}}).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _capture_request(client_method, *args, **kwargs) -> _CapturedRequest:
    """Call an SDK method and capture the HTTP request it generates."""
    captured = _CapturedRequest()
    client = AionMarketClient(api_key="test-key", base_url="https://test.local")

    def mock_urlopen(req, timeout=None):
        from urllib.parse import parse_qs, urlparse

        captured.url = req.full_url
        captured.method = req.method
        parsed = urlparse(req.full_url)
        captured.query_params = {
            k: v[0] if len(v) == 1 else v
            for k, v in parse_qs(parsed.query).items()
        }
        if req.data:
            captured.body = json.loads(req.data.decode("utf-8"))
        return captured

    method = getattr(client, client_method)
    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        method(*args, **kwargs)

    return captured


# ======================================================================
# Section 1: Controller-level route verification
# ======================================================================


def test_agents_claim_endpoint_contract_matches_sdk_shape() -> None:
    controller_path = (
        _workspace_root()
        / "apps"
        / "api"
        / "src"
        / "modules"
        / "aiagent"
        / "aiagent.controller.ts"
    )
    source = _read(controller_path)
    assert "@Get('claim')" in source
    assert "@Query('claimCode') claimCode: string" in source


def test_agents_controller_has_all_sdk_routes() -> None:
    """Verify the agent controller has routes matching SDK methods."""
    controller_path = (
        _workspace_root()
        / "apps"
        / "api"
        / "src"
        / "modules"
        / "aiagent"
        / "aiagent.controller.ts"
    )
    source = _read(controller_path)

    expected_routes = [
        ("@Post('register')", "register_agent"),
        ("@Get('claim')", "claim_preview"),
        ("@Get('claim/:claimCode')", "get_agent_by_claim_code"),
        ("@Get('me')", "get_me"),
        ("@Get('settings')", "get_settings"),
        ("@Post('settings')", "update_settings"),
        ("@Get('skills')", "get_skills"),
        ("@Get('trades')", "get_trades"),
        ("@Get('leaderboard')", "get_leaderboard"),
        ("@Get('health')", "health"),
    ]
    for route_marker, sdk_method in expected_routes:
        assert route_marker in source, (
            f"Route {route_marker} not found in controller (SDK method: {sdk_method})"
        )


def test_market_controller_has_all_sdk_routes() -> None:
    """Verify the market controller has routes matching SDK methods."""
    controller_path = (
        _workspace_root()
        / "apps"
        / "api"
        / "src"
        / "modules"
        / "aiagent"
        / "aiagent.market.controller.ts"
    )
    source = _read(controller_path)

    expected_routes = [
        "listMarkets",
        "getBriefing",
        "getPricesHistory",
        "checkMarketExists",
        "getClosedPositions",
        "getCurrentPositions",
        "getWalletPositions",
        "getMarketContext",
        "getOpenOrders",
        "getOrderHistory",
        "getOrderDetail",
        "cancelOrder",
        "cancelAllOrders",
        "cancelAllUserOrders",
        "trade",
        "batchTrade",
        "redeem",
        "getRiskSettings",
        "setRiskSettings",
        "deleteRiskSettings",
        "getPositionsExpiring",
        "getPortfolio",
        "getMarketById",
    ]
    for method_name in expected_routes:
        assert re.search(
            rf"\b{method_name}\s*\(", source
        ), f"Controller method {method_name} not found"


def test_wallet_controller_has_all_sdk_routes() -> None:
    """Verify the wallet controller has routes matching SDK methods."""
    controller_path = (
        _workspace_root()
        / "apps"
        / "api"
        / "src"
        / "modules"
        / "aiagent"
        / "aiagent.wallet.controller.ts"
    )
    source = _read(controller_path)

    expected = [
        ("@Get('credentials/check')", "check_wallet_credentials"),
        ("@Post('credentials')", "register_wallet_credentials"),
        ("@Get('link/challenge')", "wallet_link_challenge"),
        ("@Post('link')", "wallet_link"),
        ("@Post('unlink')", "wallet_unlink"),
    ]
    for route_marker, sdk_method in expected:
        assert route_marker in source, (
            f"Route {route_marker} not found (SDK method: {sdk_method})"
        )


def test_kalshi_controller_has_all_sdk_routes() -> None:
    """Verify the Kalshi agent controller has routes matching SDK methods."""
    controller_path = (
        _workspace_root()
        / "apps"
        / "api"
        / "src"
        / "modules"
        / "aiagent"
        / "aiagent.kalshi.controller.ts"
    )
    source = _read(controller_path)

    assert "@Post('quote')" in source, "Kalshi quote route not found"
    assert "@Post('submit')" in source, "Kalshi submit route not found"


def test_skill_controller_has_all_sdk_routes() -> None:
    """Verify the skill controller has routes matching SDK methods."""
    controller_path = (
        _workspace_root()
        / "apps"
        / "api"
        / "src"
        / "modules"
        / "aiagent"
        / "aiagent.skill.controller.ts"
    )
    source = _read(controller_path)

    assert "@Post('submit-skill')" in source, "submit-skill route not found"
    assert "@Get('my-skills')" in source, "my-skills route not found"


# ======================================================================
# Section 2: DTO field coverage — every DTO field must be in the SDK
# ======================================================================


class TestRegisterAgentDto:
    """POST /agents/register — RegisterAgentDto"""

    def test_all_dto_fields_are_sdk_parameters(self) -> None:
        fields = _get_dto_fields("aiagent-request.dto.ts", "RegisterAgentDto")
        params = _sdk_method_params("register_agent")
        for field in fields:
            snake = re.sub(r"([a-z])([A-Z])", r"\1_\2", field).lower()
            assert snake in params, (
                f"RegisterAgentDto.{field} has no corresponding "
                f"SDK param (expected '{snake}' in {params})"
            )

    def test_captured_request_includes_all_optional_fields(self) -> None:
        cap = _capture_request("register_agent", "test-agent", description="A desc")
        assert cap.body["name"] == "test-agent"
        assert cap.body["description"] == "A desc"

    def test_description_omitted_when_none(self) -> None:
        cap = _capture_request("register_agent", "test-agent")
        assert "description" not in cap.body


class TestUpdateAiAgentSettingsDto:
    """POST /agents/settings — UpdateAiAgentSettingsDto"""

    def test_all_dto_fields_are_sdk_parameters(self) -> None:
        fields = _get_dto_fields("aiagent-request.dto.ts", "UpdateAiAgentSettingsDto")
        params = _sdk_method_params("update_settings")
        for field in fields:
            snake = re.sub(r"([a-z])([A-Z])", r"\1_\2", field).lower()
            assert snake in params, (
                f"UpdateAiAgentSettingsDto.{field} missing from SDK.update_settings()"
            )

    def test_captured_request_includes_all_fields(self) -> None:
        cap = _capture_request(
            "update_settings",
            max_trades_per_day=10,
            max_trade_amount=100.0,
            trade_limit_enabled=True,
            daily_trade_amount_limit=500.0,
            max_position_value=200.0,
            risk_control_enabled=True,
            take_profit_percent=50,
            stop_loss_percent=20,
        )
        assert cap.body == {
            "maxTradesPerDay": 10,
            "maxTradeAmount": 100.0,
            "tradeLimitEnabled": True,
            "dailyTradeAmountLimit": 500.0,
            "maxPositionValue": 200.0,
            "riskControlEnabled": True,
            "takeProfitPercent": 50,
            "stopLossPercent": 20,
        }


class TestAiAgentSkillsQueryDto:
    """GET /agents/skills — AiAgentSkillsQueryDto"""

    def test_all_dto_fields_are_sdk_parameters(self) -> None:
        fields = _get_dto_fields("aiagent-request.dto.ts", "AiAgentSkillsQueryDto")
        params = _sdk_method_params("get_skills")
        for field in fields:
            snake = re.sub(r"([a-z])([A-Z])", r"\1_\2", field).lower()
            assert snake in params, (
                f"AiAgentSkillsQueryDto.{field} missing from SDK.get_skills()"
            )

    def test_captured_request_includes_all_params(self) -> None:
        cap = _capture_request("get_skills", category=2, limit=10, offset=5)
        assert cap.query_params["category"] == "2"
        assert cap.query_params["limit"] == "10"
        assert cap.query_params["offset"] == "5"


class TestGetTradesQueryDto:
    """GET /agents/trades — GetTradesQueryDto"""

    def test_all_dto_fields_are_sdk_parameters(self) -> None:
        fields = _get_dto_fields("aiagent-request.dto.ts", "GetTradesQueryDto")
        params = _sdk_method_params("get_trades")
        for field in fields:
            snake = re.sub(r"([a-z])([A-Z])", r"\1_\2", field).lower()
            assert snake in params, (
                f"GetTradesQueryDto.{field} missing from SDK.get_trades()"
            )

    def test_captured_request_includes_all_params(self) -> None:
        cap = _capture_request("get_trades", venue="kalshi", limit=100, offset=10)
        assert cap.query_params["venue"] == "kalshi"
        assert cap.query_params["limit"] == "100"
        assert cap.query_params["offset"] == "10"


class TestLeaderboardQueryDto:
    """GET /agents/leaderboard — LeaderboardQueryDto"""

    def test_all_dto_fields_are_sdk_parameters(self) -> None:
        fields = _get_dto_fields("aiagent-request.dto.ts", "LeaderboardQueryDto")
        params = _sdk_method_params("get_leaderboard")
        for field in fields:
            snake = re.sub(r"([a-z])([A-Z])", r"\1_\2", field).lower()
            assert snake in params, (
                f"LeaderboardQueryDto.{field} missing from SDK.get_leaderboard()"
            )


class TestSubmitAiAgentSkillDto:
    """POST /agent/skill/submit-skill — SubmitAiAgentSkillDto"""

    def test_all_dto_fields_are_sdk_parameters(self) -> None:
        fields = _get_dto_fields("aiagent-request.dto.ts", "SubmitAiAgentSkillDto")
        params = _sdk_method_params("submit_skill")
        for field in fields:
            snake = re.sub(r"([a-z])([A-Z])", r"\1_\2", field).lower()
            assert snake in params, (
                f"SubmitAiAgentSkillDto.{field} missing from SDK.submit_skill()"
            )

    def test_captured_request_includes_all_fields(self) -> None:
        cap = _capture_request(
            "submit_skill",
            skill_name="My Skill",
            description="A skill",
            version="1.0.0",
            how_it_works="It works well",
            clawhub_url="https://clawhub.ai/s/my",
            github_url="https://github.com/my/skill",
        )
        assert cap.body["skillName"] == "My Skill"
        assert cap.body["description"] == "A skill"
        assert cap.body["version"] == "1.0.0"
        assert cap.body["howItWorks"] == "It works well"
        assert cap.body["clawhubUrl"] == "https://clawhub.ai/s/my"
        assert cap.body["githubUrl"] == "https://github.com/my/skill"


class TestSkillListQueryDto:
    """GET /agent/skill/my-skills — SkillListQueryDto"""

    def test_all_dto_fields_are_sdk_parameters(self) -> None:
        fields = _get_dto_fields("aiagent-request.dto.ts", "SkillListQueryDto")
        params = _sdk_method_params("my_skills")
        for field in fields:
            snake = re.sub(r"([a-z])([A-Z])", r"\1_\2", field).lower()
            assert snake in params, (
                f"SkillListQueryDto.{field} missing from SDK.my_skills()"
            )


class TestListMarketsQueryDto:
    """GET /markets — ListMarketsQueryDto"""

    def test_all_dto_fields_are_sdk_parameters(self) -> None:
        fields = _get_dto_fields(
            "aiagent-market-request.dto.ts", "ListMarketsQueryDto"
        )
        params = _sdk_method_params("get_markets")
        for field in fields:
            snake = re.sub(r"([a-z])([A-Z])", r"\1_\2", field).lower()
            assert snake in params, (
                f"ListMarketsQueryDto.{field} missing from SDK.get_markets() "
                f"(expected '{snake}' in {params})"
            )

    def test_captured_request_includes_order_ascending_closed(self) -> None:
        cap = _capture_request(
            "get_markets",
            q="bitcoin",
            order="volume",
            ascending=True,
            closed=True,
        )
        assert cap.query_params["order"] == "volume"
        assert cap.query_params["ascending"] == "true"
        assert cap.query_params["closed"] == "true"


class TestGetMarketByIdQueryDto:
    """GET /markets/:id — GetMarketByIdQueryDto"""

    def test_all_dto_fields_are_sdk_parameters(self) -> None:
        fields = _get_dto_fields(
            "aiagent-market-request.dto.ts", "GetMarketByIdQueryDto"
        )
        params = _sdk_method_params("get_market")
        # market_id comes from the URL path param, not the query DTO
        for field in fields:
            snake = re.sub(r"([a-z])([A-Z])", r"\1_\2", field).lower()
            assert snake in params, (
                f"GetMarketByIdQueryDto.{field} missing from SDK.get_market() "
                f"(expected '{snake}' in {params})"
            )

    def test_captured_request_includes_include_tag(self) -> None:
        cap = _capture_request("get_market", "abc123", include_tag=True)
        assert cap.query_params["includeTag"] == "true"
        assert cap.query_params["venue"] == "polymarket"


class TestGetPricesHistoryQueryDto:
    """GET /markets/prices-history — GetPricesHistoryQueryDto"""

    def test_all_dto_fields_are_sdk_parameters(self) -> None:
        fields = _get_dto_fields(
            "aiagent-market-request.dto.ts", "GetPricesHistoryQueryDto"
        )
        params = _sdk_method_params("get_prices_history")
        # 'market' in DTO maps to 'token_id' in SDK
        field_map = {"market": "token_id"}
        for field in fields:
            mapped = field_map.get(field)
            if mapped:
                assert mapped in params, (
                    f"GetPricesHistoryQueryDto.{field} (mapped to '{mapped}') "
                    f"missing from SDK.get_prices_history()"
                )
            else:
                snake = re.sub(r"([a-z])([A-Z])", r"\1_\2", field).lower()
                assert snake in params, (
                    f"GetPricesHistoryQueryDto.{field} missing from "
                    f"SDK.get_prices_history()"
                )


class TestCheckMarketExistsQueryDto:
    """GET /markets/check — CheckMarketExistsQueryDto"""

    def test_all_dto_fields_are_sdk_parameters(self) -> None:
        fields = _get_dto_fields(
            "aiagent-market-request.dto.ts", "CheckMarketExistsQueryDto"
        )
        params = _sdk_method_params("check_market_exists")
        field_map = {"marketId": "market_id"}
        for field in fields:
            mapped = field_map.get(field)
            if mapped:
                assert mapped in params
            else:
                snake = re.sub(r"([a-z])([A-Z])", r"\1_\2", field).lower()
                assert snake in params


class TestClosedPositionsQueryDto:
    """GET /markets/closed-positions — ClosedPositionsQueryDto"""

    def test_all_dto_fields_are_sdk_parameters(self) -> None:
        fields = _get_dto_fields(
            "aiagent-market-request.dto.ts", "ClosedPositionsQueryDto"
        )
        params = _sdk_method_params("get_closed_positions")
        for field in fields:
            snake = re.sub(r"([a-z])([A-Z])", r"\1_\2", field).lower()
            assert snake in params, (
                f"ClosedPositionsQueryDto.{field} missing from "
                f"SDK.get_closed_positions()"
            )

    def test_captured_request_includes_all_params(self) -> None:
        cap = _capture_request(
            "get_closed_positions",
            user="0x1234567890abcdef1234567890abcdef12345678",
            venue="polymarket",
            market="cond123",
            title="Bitcoin",
            limit=20,
            offset=5,
            sort_by="REALIZEDPNL",
            sort_direction="ASC",
        )
        assert cap.query_params["user"] == "0x1234567890abcdef1234567890abcdef12345678"
        assert cap.query_params["market"] == "cond123"
        assert cap.query_params["sortBy"] == "REALIZEDPNL"
        assert cap.query_params["sortDirection"] == "ASC"


class TestCurrentPositionsQueryDto:
    """GET /markets/current-positions — CurrentPositionsQueryDto"""

    def test_all_dto_fields_are_sdk_parameters(self) -> None:
        fields = _get_dto_fields(
            "aiagent-market-request.dto.ts", "CurrentPositionsQueryDto"
        )
        params = _sdk_method_params("get_current_positions")
        for field in fields:
            snake = re.sub(r"([a-z])([A-Z])", r"\1_\2", field).lower()
            assert snake in params, (
                f"CurrentPositionsQueryDto.{field} missing from "
                f"SDK.get_current_positions()"
            )

    def test_captured_request_includes_all_params(self) -> None:
        cap = _capture_request(
            "get_current_positions",
            user="0x1234567890abcdef1234567890abcdef12345678",
            venue="kalshi",
            market="cond1",
            title="Eth",
            size_threshold=0.5,
            redeemable=True,
            mergeable=False,
            limit=50,
            offset=10,
            sort_by="TOKENS",
            sort_direction="DESC",
        )
        assert cap.query_params["user"] == "0x1234567890abcdef1234567890abcdef12345678"
        assert cap.query_params["venue"] == "kalshi"
        assert cap.query_params["sizeThreshold"] == "0.5"
        assert cap.query_params["redeemable"] == "true"
        assert cap.query_params["mergeable"] == "false"


class TestAiAgentMarketContextQueryDto:
    """GET /markets/context/:marketId — AiAgentMarketContextQueryDto"""

    def test_all_dto_fields_are_sdk_parameters(self) -> None:
        fields = _get_dto_fields(
            "aiagent-market-request.dto.ts", "AiAgentMarketContextQueryDto"
        )
        params = _sdk_method_params("get_market_context")
        for field in fields:
            snake = re.sub(r"([a-z])([A-Z])", r"\1_\2", field).lower()
            assert snake in params, (
                f"AiAgentMarketContextQueryDto.{field} missing from "
                f"SDK.get_market_context()"
            )

    def test_captured_request_includes_user_and_my_probability(self) -> None:
        cap = _capture_request(
            "get_market_context",
            "mkt123",
            user="0xabc",
            my_probability=0.58,
        )
        assert cap.query_params["user"] == "0xabc"
        assert cap.query_params["myProbability"] == "0.58"


class TestAiAgentBriefingQueryDto:
    """GET /markets/briefing — AiAgentBriefingQueryDto"""

    def test_all_dto_fields_are_sdk_parameters(self) -> None:
        fields = _get_dto_fields(
            "aiagent-market-request.dto.ts", "AiAgentBriefingQueryDto"
        )
        params = _sdk_method_params("get_briefing")
        field_map = {"includeMarkets": "include_markets"}
        for field in fields:
            mapped = field_map.get(field)
            if mapped:
                assert mapped in params
            else:
                snake = re.sub(r"([a-z])([A-Z])", r"\1_\2", field).lower()
                assert snake in params, (
                    f"AiAgentBriefingQueryDto.{field} missing from SDK.get_briefing()"
                )

    def test_include_markets_field_exists(self) -> None:
        fields = _get_dto_fields(
            "aiagent-market-request.dto.ts", "AiAgentBriefingQueryDto"
        )
        assert "includeMarkets" in fields
        assert fields["includeMarkets"] is True


class TestPositionsExpiringQueryDto:
    """GET /markets/positions/expiring — PositionsExpiringQueryDto"""

    def test_all_dto_fields_are_sdk_parameters(self) -> None:
        fields = _get_dto_fields(
            "aiagent-market-request.dto.ts", "PositionsExpiringQueryDto"
        )
        params = _sdk_method_params("get_positions_expiring")
        for field in fields:
            snake = re.sub(r"([a-z])([A-Z])", r"\1_\2", field).lower()
            assert snake in params, (
                f"PositionsExpiringQueryDto.{field} missing from "
                f"SDK.get_positions_expiring()"
            )

    def test_captured_request_includes_all_params(self) -> None:
        cap = _capture_request(
            "get_positions_expiring",
            hours=48,
            venue="kalshi",
            limit=100,
            offset=5,
        )
        assert cap.query_params["hours"] == "48"
        assert cap.query_params["venue"] == "kalshi"


class TestPortfolioQueryDto:
    """GET /markets/portfolio — PortfolioQueryDto"""

    def test_all_dto_fields_are_sdk_parameters(self) -> None:
        fields = _get_dto_fields(
            "aiagent-market-request.dto.ts", "PortfolioQueryDto"
        )
        params = _sdk_method_params("get_portfolio")
        for field in fields:
            snake = re.sub(r"([a-z])([A-Z])", r"\1_\2", field).lower()
            assert snake in params, (
                f"PortfolioQueryDto.{field} missing from SDK.get_portfolio()"
            )


class TestAiAgentOpenOrdersQueryDto:
    """GET /markets/orders/open — AiAgentOpenOrdersQueryDto"""

    def test_all_dto_fields_are_sdk_parameters(self) -> None:
        fields = _get_dto_fields(
            "aiagent-order-request.dto.ts", "AiAgentOpenOrdersQueryDto"
        )
        params = _sdk_method_params("get_open_orders")
        for field in fields:
            snake = re.sub(r"([a-z])([A-Z])", r"\1_\2", field).lower()
            assert snake in params, (
                f"AiAgentOpenOrdersQueryDto.{field} missing from SDK.get_open_orders()"
            )


class TestAiAgentOrderHistoryQueryDto:
    """GET /markets/orders — AiAgentOrderHistoryQueryDto"""

    def test_all_dto_fields_are_sdk_parameters(self) -> None:
        fields = _get_dto_fields(
            "aiagent-order-request.dto.ts", "AiAgentOrderHistoryQueryDto"
        )
        params = _sdk_method_params("get_order_history")
        for field in fields:
            snake = re.sub(r"([a-z])([A-Z])", r"\1_\2", field).lower()
            assert snake in params, (
                f"AiAgentOrderHistoryQueryDto.{field} missing from "
                f"SDK.get_order_history()"
            )


class TestAiAgentOrderDetailQueryDto:
    """GET /markets/orders/:orderId — AiAgentOrderDetailQueryDto"""

    def test_all_dto_fields_are_sdk_parameters(self) -> None:
        fields = _get_dto_fields(
            "aiagent-order-request.dto.ts", "AiAgentOrderDetailQueryDto"
        )
        params = _sdk_method_params("get_order_detail")
        for field in fields:
            snake = re.sub(r"([a-z])([A-Z])", r"\1_\2", field).lower()
            assert snake in params, (
                f"AiAgentOrderDetailQueryDto.{field} missing from "
                f"SDK.get_order_detail()"
            )


class TestAiAgentCancelOrderDto:
    """POST /markets/orders/cancel — AiAgentCancelOrderDto"""

    def test_all_dto_fields_are_sdk_parameters(self) -> None:
        fields = _get_dto_fields(
            "aiagent-order-request.dto.ts", "AiAgentCancelOrderDto"
        )
        params = _sdk_method_params("cancel_order")
        field_map = {"orderId": "order_id"}
        for field in fields:
            mapped = field_map.get(field)
            if mapped:
                assert mapped in params
            else:
                snake = re.sub(r"([a-z])([A-Z])", r"\1_\2", field).lower()
                assert snake in params, (
                    f"AiAgentCancelOrderDto.{field} missing from SDK.cancel_order()"
                )

    def test_captured_request_body(self) -> None:
        cap = _capture_request(
            "cancel_order",
            "order-123",
            venue="polymarket",
            wallet_address="0x1234567890abcdef1234567890abcdef12345678",
        )
        assert cap.body["orderId"] == "order-123"
        assert cap.body["venue"] == "polymarket"
        assert cap.body["walletAddress"] == "0x1234567890abcdef1234567890abcdef12345678"


class TestAiAgentCancelAllOrdersDto:
    """POST /markets/orders/cancel-market — AiAgentCancelAllOrdersDto"""

    def test_all_dto_fields_are_sdk_parameters(self) -> None:
        fields = _get_dto_fields(
            "aiagent-order-request.dto.ts", "AiAgentCancelAllOrdersDto"
        )
        params = _sdk_method_params("cancel_all_orders")
        for field in fields:
            snake = re.sub(r"([a-z])([A-Z])", r"\1_\2", field).lower()
            assert snake in params, (
                f"AiAgentCancelAllOrdersDto.{field} missing from "
                f"SDK.cancel_all_orders()"
            )


class TestAiAgentRedeemDto:
    """POST /markets/redeem — AiAgentRedeemDto"""

    def test_all_dto_fields_are_sdk_parameters(self) -> None:
        fields = _get_dto_fields(
            "aiagent-order-request.dto.ts", "AiAgentRedeemDto"
        )
        params = _sdk_method_params("redeem")
        field_map = {"marketId": "market_id"}
        for field in fields:
            mapped = field_map.get(field)
            if mapped:
                assert mapped in params
            else:
                snake = re.sub(r"([a-z])([A-Z])", r"\1_\2", field).lower()
                assert snake in params, (
                    f"AiAgentRedeemDto.{field} missing from SDK.redeem()"
                )


class TestAiAgentTradeRequestDto:
    """POST /markets/trade — AiAgentTradeRequestDto"""

    def test_dto_required_fields_match_sdk_validation(self) -> None:
        fields = _get_dto_fields(
            "aiagent-trade-request.dto.ts", "AiAgentTradeRequestDto"
        )
        dto_required = {
            name for name, is_optional in fields.items() if not is_optional
        }
        sdk_required = {
            "marketConditionId",
            "marketQuestion",
            "orderSize",
            "price",
            "outcome",
            "order",
        }
        assert dto_required == sdk_required

    def test_all_dto_fields_accepted_by_trade_payload(self) -> None:
        """SDK trade() passes an opaque dict; ensure all DTO fields are documented."""
        fields = _get_dto_fields(
            "aiagent-trade-request.dto.ts", "AiAgentTradeRequestDto"
        )
        sdk_known_fields = {
            "venue",
            "isLimitOrder",
            "walletAddress",
            "order",
            "owner",
            "orderType",
            "deferExec",
            "postOnly",
            "marketConditionId",
            "marketQuestion",
            "orderSize",
            "price",
            "outcome",
            "feeAmount",
            "reasoning",
            "source",
            "skillSlug",
            "expirationTime",
            "orderVersion",
            "tickSize",
            "negRisk",
            "funderAddress",
        }
        assert sdk_known_fields == set(fields.keys()), (
            f"Mismatch: SDK knows {sdk_known_fields.symmetric_difference(set(fields.keys()))}"
        )


class TestAiAgentTradeOrderPayloadDto:
    """Nested order in AiAgentTradeRequestDto"""

    def test_all_dto_fields_known(self) -> None:
        fields = _get_dto_fields(
            "aiagent-trade-request.dto.ts", "AiAgentTradeOrderPayloadDto"
        )
        sdk_known_order_fields = {
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
            "signature",
            "salt",
            "signatureType",
            "timestamp",
            "metadata",
            "builder",
        }
        assert sdk_known_order_fields == set(fields.keys()), (
            f"Order DTO field mismatch: "
            f"{sdk_known_order_fields.symmetric_difference(set(fields.keys()))}"
        )


class TestAiAgentBatchTradeRequestDto:
    """POST /markets/batch-trade — AiAgentBatchTradeRequestDto"""

    def test_dto_has_orders_array(self) -> None:
        fields = _get_dto_fields(
            "aiagent-trade-request.dto.ts", "AiAgentBatchTradeRequestDto"
        )
        assert "orders" in fields
        assert fields["orders"] is False  # required


# ======================================================================
# Section 3: Wallet DTO coverage
# ======================================================================


class TestCheckWalletCredentialsQueryDto:
    """GET /wallet/credentials/check — CheckWalletCredentialsQueryDto"""

    def test_all_dto_fields_are_sdk_parameters(self) -> None:
        fields = _get_dto_fields(
            "aiagent-wallet-request.dto.ts", "CheckWalletCredentialsQueryDto"
        )
        params = _sdk_method_params("check_wallet_credentials")
        for field in fields:
            snake = re.sub(r"([a-z])([A-Z])", r"\1_\2", field).lower()
            assert snake in params, (
                f"CheckWalletCredentialsQueryDto.{field} missing from "
                f"SDK.check_wallet_credentials()"
            )


class TestRegisterWalletCredentialsDto:
    """POST /wallet/credentials — RegisterWalletCredentialsDto"""

    def test_all_dto_fields_are_sdk_parameters(self) -> None:
        fields = _get_dto_fields(
            "aiagent-wallet-request.dto.ts", "RegisterWalletCredentialsDto"
        )
        params = _sdk_method_params("register_wallet_credentials")
        for field in fields:
            snake = re.sub(r"([a-z])([A-Z])", r"\1_\2", field).lower()
            assert snake in params, (
                f"RegisterWalletCredentialsDto.{field} missing from "
                f"SDK.register_wallet_credentials()"
            )

    def test_captured_request_body(self) -> None:
        cap = _capture_request(
            "register_wallet_credentials",
            wallet_address="0x1234567890abcdef1234567890abcdef12345678",
            api_key="ak",
            api_secret="as",
            api_passphrase="ap",
        )
        assert cap.body == {
            "walletAddress": "0x1234567890abcdef1234567890abcdef12345678",
            "apiKey": "ak",
            "apiSecret": "as",
            "apiPassphrase": "ap",
        }


class TestWalletLinkDto:
    """POST /wallet/link — WalletLinkDto"""

    def test_all_dto_fields_are_sdk_parameters(self) -> None:
        fields = _get_dto_fields("aiagent-wallet-link.dto.ts", "WalletLinkDto")
        params = _sdk_method_params("wallet_link")
        for field in fields:
            snake = re.sub(r"([a-z])([A-Z])", r"\1_\2", field).lower()
            assert snake in params, (
                f"WalletLinkDto.{field} missing from SDK.wallet_link()"
            )

    def test_captured_request_body(self) -> None:
        cap = _capture_request(
            "wallet_link",
            address="0xabc",
            signature="0xsig",
            nonce="n1",
            signature_type=1,
        )
        assert cap.body == {
            "address": "0xabc",
            "signature": "0xsig",
            "nonce": "n1",
            "signature_type": 1,
        }


# ======================================================================
# Section 4: Kalshi DTO coverage
# ======================================================================


class TestKalshiQuoteRequestDto:
    """POST /kalshi/agent/quote — KalshiQuoteRequestDto"""

    def test_all_dto_fields_are_sdk_parameters(self) -> None:
        fields = _get_kalshi_dto_fields(
            "kalshi-request.dto.ts", "KalshiQuoteRequestDto"
        )
        params = _sdk_method_params("kalshi_quote")
        field_map = {
            "marketTicker": "market_ticker",
            "userPublicKey": "user_public_key",
            "destinationWallet": "destination_wallet",
        }
        for field in fields:
            mapped = field_map.get(field)
            if mapped:
                assert mapped in params, (
                    f"KalshiQuoteRequestDto.{field} (mapped to '{mapped}') "
                    f"missing from SDK.kalshi_quote()"
                )
            else:
                snake = re.sub(r"([a-z])([A-Z])", r"\1_\2", field).lower()
                assert snake in params, (
                    f"KalshiQuoteRequestDto.{field} missing from SDK.kalshi_quote()"
                )


class TestKalshiSubmitOrderRequestDto:
    """POST /kalshi/agent/submit — KalshiSubmitOrderRequestDto"""

    def test_all_dto_fields_are_sdk_parameters(self) -> None:
        fields = _get_kalshi_dto_fields(
            "kalshi-request.dto.ts", "KalshiSubmitOrderRequestDto"
        )
        params = _sdk_method_params("kalshi_submit")
        field_map = {
            "marketTicker": "market_ticker",
            "signedTransaction": "signed_transaction",
            "quoteId": "quote_id",
            "userPublicKey": "user_public_key",
            "destinationWallet": "destination_wallet",
            "inAmount": "in_amount",
            "outAmount": "out_amount",
            "minOutAmount": "min_out_amount",
            "skillSlug": "skill_slug",
        }
        for field in fields:
            mapped = field_map.get(field)
            if mapped:
                assert mapped in params, (
                    f"KalshiSubmitOrderRequestDto.{field} (mapped to '{mapped}') "
                    f"missing from SDK.kalshi_submit()"
                )
            else:
                snake = re.sub(r"([a-z])([A-Z])", r"\1_\2", field).lower()
                assert snake in params, (
                    f"KalshiSubmitOrderRequestDto.{field} missing from "
                    f"SDK.kalshi_submit()"
                )


# ======================================================================
# Section 5: Service-level contract checks
# ======================================================================


def test_trade_service_uses_dynamic_order_type_not_hardcoded_fak() -> None:
    service_path = (
        _workspace_root()
        / "apps"
        / "api"
        / "src"
        / "modules"
        / "aiagent"
        / "aiagent.market.service.ts"
    )
    source = _read(service_path)
    assert "const resolvedOrderType =" in source
    assert "orderType: resolvedOrderType" in source


def test_trade_service_has_market_buy_precision_guardrail() -> None:
    service_path = (
        _workspace_root()
        / "apps"
        / "api"
        / "src"
        / "modules"
        / "aiagent"
        / "aiagent.market.service.ts"
    )
    source = _read(service_path)
    assert "validateMarketBuyAmountPrecision" in source
    assert "makerAmount % 10000n" in source
    assert "takerAmount % 100n" in source


def test_skill_examples_match_briefing_and_context_response_shapes() -> None:
    skill_path = (
        _workspace_root()
        / "skills"
        / "aionmarket-trading"
        / "skill.md"
    )
    source = _read(skill_path)
    assert "context.get(" in source
    assert "briefing.get(" in source
    assert "get_current_positions" in source


# ======================================================================
# Section 6: SDK method HTTP correctness
# ======================================================================


class TestSdkHttpPaths:
    """Verify each SDK method calls the correct HTTP method and path."""

    @pytest.mark.parametrize(
        "sdk_method,args,kwargs,expected_method,expected_path_substr",
        [
            ("register_agent", ["bot"], {}, "POST", "/agents/register"),
            ("claim_preview", ["CODE"], {}, "GET", "/agents/claim"),
            ("get_agent_by_claim_code", ["CODE"], {}, "GET", "/agents/claim/CODE"),
            ("get_me", [], {}, "GET", "/agents/me"),
            ("get_settings", [], {}, "GET", "/agents/settings"),
            ("update_settings", [], {"max_trades_per_day": 5}, "POST", "/agents/settings"),
            ("get_skills", [], {}, "GET", "/agents/skills"),
            ("submit_skill", ["My Skill", "desc"], {}, "POST", "/agent/skill/submit-skill"),
            ("my_skills", [], {}, "GET", "/agent/skill/my-skills"),
            ("get_markets", ["bitcoin"], {}, "GET", "/markets?"),
            ("get_market", ["mid1"], {}, "GET", "/markets/mid1"),
            ("check_market_exists", ["m1"], {}, "GET", "/markets/check"),
            ("get_prices_history", ["tok1"], {}, "GET", "/markets/prices-history"),
            ("get_briefing", [], {}, "GET", "/markets/briefing"),
            ("get_market_context", ["mkt1"], {}, "GET", "/markets/context/mkt1"),
            ("get_closed_positions", ["0xabc"], {}, "GET", "/markets/closed-positions"),
            ("get_current_positions", ["0xabc"], {}, "GET", "/markets/current-positions"),
            ("get_wallet_positions", ["0xabc"], {}, "GET", "/markets/wallet/0xabc/positions"),
            ("get_open_orders", [], {}, "GET", "/markets/orders/open"),
            ("get_order_history", [], {}, "GET", "/markets/orders?"),
            ("get_order_detail", ["oid1"], {}, "GET", "/markets/orders/oid1"),
            ("cancel_order", ["oid1"], {}, "POST", "/markets/orders/cancel"),
            ("cancel_all_orders", [], {}, "POST", "/markets/orders/cancel-market"),
            ("cancel_all_user_orders", [], {}, "DELETE", "/markets/orders"),
            ("redeem", ["m1", "YES"], {}, "POST", "/markets/redeem"),
            ("get_risk_settings", [], {}, "GET", "/markets/risk-settings"),
            ("set_risk_settings", [], {}, "POST", "/markets/risk-settings"),
            ("delete_risk_settings", [], {}, "DELETE", "/markets/risk-settings"),
            ("get_positions_expiring", [], {}, "GET", "/markets/positions/expiring"),
            ("get_portfolio", [], {}, "GET", "/markets/portfolio"),
            ("get_trades", [], {}, "GET", "/agents/trades"),
            ("get_leaderboard", [], {}, "GET", "/agents/leaderboard"),
            ("health", [], {}, "GET", "/agents/health"),
            ("check_wallet_credentials", ["0xabc"], {}, "GET", "/wallet/credentials/check"),
            (
                "register_wallet_credentials",
                ["0xabc", "k", "s", "p"],
                {},
                "POST",
                "/wallet/credentials",
            ),
            ("wallet_link_challenge", ["0xabc"], {}, "GET", "/wallet/link/challenge"),
            (
                "wallet_link",
                ["0xabc", "sig", "nonce"],
                {},
                "POST",
                "/wallet/link",
            ),
            ("wallet_unlink", [], {}, "POST", "/wallet/unlink"),
            (
                "kalshi_quote",
                ["TICKER", "YES", "BUY"],
                {},
                "POST",
                "/kalshi/agent/quote",
            ),
            (
                "kalshi_submit",
                ["TICKER", "YES", "BUY", "sig", "qid", "pk"],
                {},
                "POST",
                "/kalshi/agent/submit",
            ),
        ],
    )
    def test_http_method_and_path(
        self,
        sdk_method: str,
        args: list,
        kwargs: dict,
        expected_method: str,
        expected_path_substr: str,
    ) -> None:
        cap = _capture_request(sdk_method, *args, **kwargs)
        assert cap.method == expected_method, (
            f"{sdk_method}: expected HTTP {expected_method}, got {cap.method}"
        )
        assert expected_path_substr in cap.url, (
            f"{sdk_method}: expected '{expected_path_substr}' in URL '{cap.url}'"
        )


# ======================================================================
# Section 7: Risk-settings DTO re-use verification
# ======================================================================


class TestRiskSettingsDto:
    """POST /markets/risk-settings reuses UpdateAiAgentSettingsDto."""

    def test_set_risk_settings_covers_all_dto_fields(self) -> None:
        fields = _get_dto_fields(
            "aiagent-request.dto.ts", "UpdateAiAgentSettingsDto"
        )
        params = _sdk_method_params("set_risk_settings")
        for field in fields:
            snake = re.sub(r"([a-z])([A-Z])", r"\1_\2", field).lower()
            assert snake in params, (
                f"UpdateAiAgentSettingsDto.{field} missing from "
                f"SDK.set_risk_settings()"
            )

    def test_captured_request_includes_all_fields(self) -> None:
        cap = _capture_request(
            "set_risk_settings",
            trade_limit_enabled=True,
            max_trade_amount=50.0,
            daily_trade_amount_limit=300.0,
            max_position_value=150.0,
            max_trades_per_day=25,
            risk_control_enabled=True,
            take_profit_percent=70,
            stop_loss_percent=10,
        )
        assert cap.body == {
            "tradeLimitEnabled": True,
            "maxTradeAmount": 50.0,
            "dailyTradeAmountLimit": 300.0,
            "maxPositionValue": 150.0,
            "maxTradesPerDay": 25,
            "riskControlEnabled": True,
            "takeProfitPercent": 70,
            "stopLossPercent": 10,
        }
