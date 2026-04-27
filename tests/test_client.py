from __future__ import annotations

import io
import json
from typing import Any
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

from aion_sdk.client import AionMarketClient, ApiError


class _MockResponse:
    def __init__(self, body: Any):
        self._body = body

    def read(self) -> bytes:
        if isinstance(self._body, bytes):
            return self._body
        if isinstance(self._body, str):
            return self._body.encode("utf-8")
        return json.dumps(self._body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


def _http_error(url: str, status_code: int, body: Any) -> HTTPError:
    raw = body if isinstance(body, str) else json.dumps(body)
    fp = io.BytesIO(raw.encode("utf-8"))
    return HTTPError(url=url, code=status_code, msg="error", hdrs={}, fp=fp)


def test_request_returns_full_json_payload_without_unwrapping() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")
    response = {
        "success": True,
        "data": {"agent": "alpha"},
        "meta": {"trace": "xyz"},
    }

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)):
        out = client.get_me()

    assert out == response
    assert out["data"]["agent"] == "alpha"


def test_http_error_preserves_raw_error_body_and_context() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")
    error_body = {
        "detail": "Daily trade limit reached",
        "fix": "Increase limit or wait for reset",
        "hint": "Check /agents/settings",
    }

    with patch(
        "urllib.request.urlopen",
        side_effect=_http_error(
            url="https://api.example.com/markets/trade",
            status_code=429,
            body=error_body,
        ),
    ):
        with pytest.raises(ApiError) as exc:
            client.trade(
                {
                    "marketConditionId": "0x1",
                    "marketQuestion": "q",
                    "orderSize": 1,
                    "price": 0.5,
                    "outcome": "YES",
                    "order": {
                        "maker": "0x1",
                        "signer": "0x1",
                        "taker": "0x0000000000000000000000000000000000000000",
                        "tokenId": "1",
                        "makerAmount": "1",
                        "takerAmount": "1",
                        "side": "BUY",
                        "expiration": "0",
                        "nonce": "0",
                        "feeRateBps": "0",
                        "signature": "0xabc",
                        "salt": 1,
                        "signatureType": 0,
                    },
                }
            )

    err = exc.value
    assert err.status_code == 429
    assert err.response_body == error_body
    assert err.method == "POST"
    assert "/markets/trade" in err.url
    assert "Daily trade limit reached" in err.message


def test_query_bool_params_serialized_as_true_false_strings() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    def _assert_url(req, timeout=None):
        assert "includeMarkets=false" in req.full_url
        return _MockResponse({"ok": True})

    with patch("urllib.request.urlopen", side_effect=_assert_url):
        result = client.get_briefing(include_markets=False)

    assert result == {"ok": True}


def test_claim_preview_uses_query_param_endpoint_shape() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    def _assert_url(req, timeout=None):
        assert req.full_url.endswith("/agents/claim?claimCode=abc123")
        return _MockResponse({"agent": "demo"})

    with patch("urllib.request.urlopen", side_effect=_assert_url):
        out = client.claim_preview("abc123")

    assert out["agent"] == "demo"


def test_get_agent_by_claim_code_uses_path_param() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    def _assert_url(req, timeout=None):
        assert req.full_url.endswith("/agents/claim/abc123")
        return _MockResponse({"agentName": "demo-agent"})

    with patch("urllib.request.urlopen", side_effect=_assert_url):
        out = client.get_agent_by_claim_code("abc123")

    assert out["agentName"] == "demo-agent"


def test_get_market_uses_path_param() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    def _assert_url(req, timeout=None):
        assert "/markets/512357" in req.full_url
        assert "venue=polymarket" in req.full_url
        return _MockResponse({"id": "512357", "question": "Will BTC hit 100k?"})

    with patch("urllib.request.urlopen", side_effect=_assert_url):
        out = client.get_market("512357")

    assert out["id"] == "512357"


def test_batch_trade_sends_orders_array() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    def _assert_url(req, timeout=None):
        assert "/markets/batch-trade" in req.full_url
        import json as _json

        body = _json.loads(req.data)
        assert "orders" in body
        assert len(body["orders"]) == 1
        return _MockResponse({"total": 1, "succeeded": 1, "failed": 0, "results": []})

    order = {
        "marketConditionId": "0x1",
        "marketQuestion": "q",
        "orderSize": 1,
        "price": 0.5,
        "outcome": "YES",
        "order": {
            "maker": "0x1",
            "signer": "0x1",
            "taker": "0x0",
            "tokenId": "123",
            "makerAmount": "500000",
            "takerAmount": "1000000",
            "side": "BUY",
            "expiration": "0",
            "nonce": "0",
            "feeRateBps": "0",
            "signature": "0xabc",
            "salt": 123,
            "signatureType": 0,
        },
    }
    with patch("urllib.request.urlopen", side_effect=_assert_url):
        out = client.batch_trade([order])

    assert out["total"] == 1
    assert out["succeeded"] == 1


def test_batch_trade_rejects_empty_orders() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")
    with pytest.raises(ValueError, match="at least one order"):
        client.batch_trade([])


def test_batch_trade_rejects_too_many_orders() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")
    with pytest.raises(ValueError, match="maximum of 20"):
        client.batch_trade([{"fake": True}] * 21)


def test_trade_payload_validation_catches_missing_fields_early() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with pytest.raises(ValueError) as exc:
        client.trade({"marketConditionId": "0x1"})

    assert "trade payload missing required fields" in str(exc.value)


def test_trade_payload_validation_catches_missing_nested_order_fields() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with pytest.raises(ValueError) as exc:
        client.trade(
            {
                "marketConditionId": "0x1",
                "marketQuestion": "q",
                "orderSize": 1,
                "price": 0.5,
                "outcome": "YES",
                "order": {},
            }
        )

    assert "trade.order missing required fields" in str(exc.value)


def test_trade_normalizes_side_from_enum_like_value_and_signature_type() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    class _EnumLike:
        value = "buy"

    payload = {
        "marketConditionId": "0x1",
        "marketQuestion": "q",
        "orderSize": 1,
        "price": 0.5,
        "outcome": "YES",
        "order": {
            "maker": "0x1",
            "signer": "0x1",
            "taker": "0x0000000000000000000000000000000000000000",
            "tokenId": "1",
            "makerAmount": "1",
            "takerAmount": "1",
            "side": _EnumLike(),
            "expiration": "0",
            "nonce": "0",
            "feeRateBps": "0",
            "signature": "0xabc",
            "salt": 1,
            "signatureType": "0",
        },
    }

    def _assert_payload(req, timeout=None):
        body = json.loads(req.data.decode("utf-8"))
        assert body["order"]["side"] == "BUY"
        assert body["order"]["signatureType"] == 0
        return _MockResponse({"success": True, "orderId": "oid"})

    with patch("urllib.request.urlopen", side_effect=_assert_payload):
        out = client.trade(payload)

    assert out["success"] is True


def test_trade_rejects_invalid_side_before_request() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with pytest.raises(ValueError) as exc:
        client.trade(
            {
                "marketConditionId": "0x1",
                "marketQuestion": "q",
                "orderSize": 1,
                "price": 0.5,
                "outcome": "YES",
                "order": {
                    "maker": "0x1",
                    "signer": "0x1",
                    "taker": "0x0000000000000000000000000000000000000000",
                    "tokenId": "1",
                    "makerAmount": "1",
                    "takerAmount": "1",
                    "side": "hold",
                    "expiration": "0",
                    "nonce": "0",
                    "feeRateBps": "0",
                    "signature": "0xabc",
                    "salt": 1,
                    "signatureType": 0,
                },
            }
        )

    assert "trade.order.side must be BUY or SELL" in str(exc.value)


def test_trade_normalizes_order_type_to_uppercase() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")
    payload = {
        "marketConditionId": "0x1",
        "marketQuestion": "q",
        "orderSize": 1,
        "price": 0.5,
        "outcome": "YES",
        "orderType": "fak",
        "order": {
            "maker": "0x1",
            "signer": "0x1",
            "taker": "0x0000000000000000000000000000000000000000",
            "tokenId": "1",
            "makerAmount": "1",
            "takerAmount": "1",
            "side": "BUY",
            "expiration": "0",
            "nonce": "0",
            "feeRateBps": "0",
            "signature": "0xabc",
            "salt": 1,
            "signatureType": 0,
        },
    }

    def _assert_payload(req, timeout=None):
        body = json.loads(req.data.decode("utf-8"))
        assert body["orderType"] == "FAK"
        return _MockResponse({"success": True, "orderId": "oid"})

    with patch("urllib.request.urlopen", side_effect=_assert_payload):
        out = client.trade(payload)

    assert out["success"] is True


def test_trade_sets_default_order_type_from_is_limit_order() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")
    payload = {
        "marketConditionId": "0x1",
        "marketQuestion": "q",
        "orderSize": 1,
        "price": 0.5,
        "outcome": "YES",
        "isLimitOrder": False,
        "order": {
            "maker": "0x1",
            "signer": "0x1",
            "taker": "0x0000000000000000000000000000000000000000",
            "tokenId": "1",
            "makerAmount": "1",
            "takerAmount": "1",
            "side": "BUY",
            "expiration": "0",
            "nonce": "0",
            "feeRateBps": "0",
            "signature": "0xabc",
            "salt": 1,
            "signatureType": 0,
        },
    }

    def _assert_payload(req, timeout=None):
        body = json.loads(req.data.decode("utf-8"))
        assert body["orderType"] == "FAK"
        return _MockResponse({"success": True, "orderId": "oid"})

    with patch("urllib.request.urlopen", side_effect=_assert_payload):
        out = client.trade(payload)

    assert out["success"] is True


def test_trade_rejects_invalid_order_type_before_request() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with pytest.raises(ValueError) as exc:
        client.trade(
            {
                "marketConditionId": "0x1",
                "marketQuestion": "q",
                "orderSize": 1,
                "price": 0.5,
                "outcome": "YES",
                "orderType": "IOC",
                "order": {
                    "maker": "0x1",
                    "signer": "0x1",
                    "taker": "0x0000000000000000000000000000000000000000",
                    "tokenId": "1",
                    "makerAmount": "1",
                    "takerAmount": "1",
                    "side": "BUY",
                    "expiration": "0",
                    "nonce": "0",
                    "feeRateBps": "0",
                    "signature": "0xabc",
                    "salt": 1,
                    "signatureType": 0,
                },
            }
        )

    assert "trade.orderType must be one of: GTC, FOK, GTD, FAK" in str(exc.value)


# ================================================================
# Trades & Leaderboard
# ================================================================


def test_get_trades_default_params() -> None:
    response = {"code": 200, "data": {"total": 5, "limit": 50, "offset": 0, "venue": "all", "trades": []}}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        result = client.get_trades()

    req = mock_open.call_args[0][0]
    assert req.get_method() == "GET"
    assert "/agents/trades?" in req.full_url
    assert "venue=all" in req.full_url
    assert "limit=50" in req.full_url
    assert "offset=0" in req.full_url
    assert result["data"]["total"] == 5


def test_get_trades_with_venue_filter() -> None:
    response = {"code": 200, "data": {"total": 2, "limit": 10, "offset": 5, "venue": "polymarket", "trades": []}}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        result = client.get_trades(venue="polymarket", limit=10, offset=5)

    req = mock_open.call_args[0][0]
    assert "venue=polymarket" in req.full_url
    assert "limit=10" in req.full_url
    assert "offset=5" in req.full_url
    assert result["data"]["venue"] == "polymarket"


def test_get_leaderboard_default_limit() -> None:
    response = {"code": 200, "data": {"entries": [], "totalAgents": 0}}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        result = client.get_leaderboard()

    req = mock_open.call_args[0][0]
    assert req.get_method() == "GET"
    assert "/agents/leaderboard?" in req.full_url
    assert "limit=50" in req.full_url
    assert result["data"]["totalAgents"] == 0


def test_get_leaderboard_custom_limit() -> None:
    response = {
        "code": 200,
        "data": {
            "entries": [{"id": "1", "name": "agent-a", "totalPnl": "100.00"}],
            "totalAgents": 1,
        },
    }
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        result = client.get_leaderboard(limit=10)

    req = mock_open.call_args[0][0]
    assert "limit=10" in req.full_url
    assert len(result["data"]["entries"]) == 1
    assert result["data"]["entries"][0]["name"] == "agent-a"


# ================================================================
# Utilities
# ================================================================


def test_health_check() -> None:
    response = {"status": "ok", "timestamp": "2026-04-25T10:13:29.203191Z"}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        result = client.health()

    req = mock_open.call_args[0][0]
    assert req.get_method() == "GET"
    assert req.full_url.endswith("/agents/health")
    assert result["status"] == "ok"
    assert "timestamp" in result


# ================================================================
# Settings
# ================================================================


def test_get_settings() -> None:
    response = {
        "code": 200,
        "data": {
            "maxTradesPerDay": 500,
            "maxTradeAmount": "5.00000000",
            "tradeLimitEnabled": True,
            "dailyTradeAmountLimit": "500.00000000",
            "maxPositionValue": "100.00000000",
            "riskControlEnabled": False,
            "takeProfitPercent": None,
            "stopLossPercent": 50,
            "updatedAt": "1713000000000",
        },
    }
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        result = client.get_settings()

    req = mock_open.call_args[0][0]
    assert req.get_method() == "GET"
    assert req.full_url.endswith("/agents/settings")
    assert result["data"]["maxTradesPerDay"] == 500


def test_update_settings_partial() -> None:
    response = {
        "code": 200,
        "data": {
            "maxTradesPerDay": 300,
            "maxTradeAmount": "10.00000000",
            "tradeLimitEnabled": True,
            "dailyTradeAmountLimit": "500.00000000",
            "maxPositionValue": "100.00000000",
            "riskControlEnabled": True,
            "takeProfitPercent": None,
            "stopLossPercent": 30,
            "updatedAt": "1713100000000",
        },
    }
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        result = client.update_settings(
            max_trades_per_day=300,
            max_trade_amount=10,
            risk_control_enabled=True,
            stop_loss_percent=30,
        )

    req = mock_open.call_args[0][0]
    assert req.get_method() == "POST"
    assert req.full_url.endswith("/agents/settings")
    body = json.loads(req.data.decode("utf-8"))
    assert body["maxTradesPerDay"] == 300
    assert body["maxTradeAmount"] == 10
    assert body["riskControlEnabled"] is True
    assert body["stopLossPercent"] == 30
    assert "tradeLimitEnabled" not in body  # omitted fields not sent
    assert result["data"]["maxTradesPerDay"] == 300


# ============================================================
# Positions & Portfolio
# ============================================================


def test_get_positions_expiring_defaults() -> None:
    response = {
        "code": 200,
        "data": {
            "positions": [
                {
                    "id": "123",
                    "venue": "polymarket",
                    "title": "BTC > 100k?",
                    "side": "Yes",
                    "shares": 50,
                    "avgPrice": 0.62,
                    "currentPrice": 0.75,
                    "pnl": 6.5,
                }
            ],
            "total": 1,
            "hours": 24,
            "venue": "all",
        },
    }
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        result = client.get_positions_expiring()

    req = mock_open.call_args[0][0]
    assert req.get_method() == "GET"
    assert "/markets/positions/expiring" in req.full_url
    assert "hours=24" in req.full_url
    assert "venue=all" in req.full_url
    assert result["data"]["total"] == 1
    assert result["data"]["positions"][0]["venue"] == "polymarket"


def test_get_positions_expiring_custom_params() -> None:
    response = {
        "code": 200,
        "data": {"positions": [], "total": 0, "hours": 48, "venue": "kalshi"},
    }
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        result = client.get_positions_expiring(hours=48, venue="kalshi", limit=10, offset=5)

    req = mock_open.call_args[0][0]
    assert "hours=48" in req.full_url
    assert "venue=kalshi" in req.full_url
    assert "limit=10" in req.full_url
    assert "offset=5" in req.full_url
    assert result["data"]["hours"] == 48


def test_get_portfolio_defaults() -> None:
    response = {
        "code": 200,
        "data": {
            "balance_usdc": 523.5,
            "total_exposure": 350,
            "positions_count": 5,
            "redeemable_count": 1,
            "pnl_total": 45.2,
            "concentration": {"top_market_pct": 32.5, "top_3_markets_pct": 78.1},
            "warnings": [],
            "polymarket": {"balance": 400, "pnl": 35.5, "positions_count": 5, "total_exposure": 250},
            "kalshi": {"balance": 123.5, "pnl": 9.7, "positions_count": 3, "total_exposure": 100},
            "total": {"positions_count": 8, "total_exposure": 350},
        },
    }
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        result = client.get_portfolio()

    req = mock_open.call_args[0][0]
    assert req.get_method() == "GET"
    assert "/markets/portfolio" in req.full_url
    assert "venue=all" in req.full_url
    assert result["data"]["balance_usdc"] == 523.5
    assert result["data"]["polymarket"]["positions_count"] == 5
    assert result["data"]["total"]["positions_count"] == 8


def test_get_portfolio_venue_filter() -> None:
    response = {
        "code": 200,
        "data": {
            "balance_usdc": 123.5,
            "kalshi": {"balance": 123.5, "pnl": 9.7, "positions_count": 3, "total_exposure": 100},
            "total": {"positions_count": 3, "total_exposure": 100},
        },
    }
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        result = client.get_portfolio(venue="kalshi")

    req = mock_open.call_args[0][0]
    assert "venue=kalshi" in req.full_url
    assert result["data"]["kalshi"]["positions_count"] == 3


# ============================================================
# Constructor & Config
# ============================================================


def test_default_base_url_is_production() -> None:
    client = AionMarketClient(api_key="k")
    assert client.base_url == "https://api.aionmarket.com/bvapi"


def test_base_url_env_var_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIONMARKET_BASE_URL", "https://staging.example.com/bvapi")
    client = AionMarketClient(api_key="k")
    assert client.base_url == "https://staging.example.com/bvapi"


def test_explicit_base_url_takes_priority_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIONMARKET_BASE_URL", "https://staging.example.com/bvapi")
    client = AionMarketClient(api_key="k", base_url="https://custom.example.com")
    assert client.base_url == "https://custom.example.com"


def test_base_url_trailing_slash_stripped() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com/")
    assert client.base_url == "https://api.example.com"


def test_api_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIONMARKET_API_KEY", "env-key-123")
    client = AionMarketClient()
    assert client.api_key == "env-key-123"


def test_explicit_api_key_takes_priority_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIONMARKET_API_KEY", "env-key")
    client = AionMarketClient(api_key="explicit-key")
    assert client.api_key == "explicit-key"


def test_default_timeout() -> None:
    client = AionMarketClient(api_key="k")
    assert client.timeout == 20


def test_custom_timeout() -> None:
    client = AionMarketClient(api_key="k", timeout=60)
    assert client.timeout == 60


def test_set_api_key() -> None:
    client = AionMarketClient(api_key="old")
    client.set_api_key("new-key")
    assert client.api_key == "new-key"


# ============================================================
# Headers
# ============================================================


def test_headers_include_bearer_when_api_key_set() -> None:
    client = AionMarketClient(api_key="my-secret")
    headers = client._headers()
    assert headers["Authorization"] == "Bearer my-secret"
    assert headers["Content-Type"] == "application/json"


def test_headers_no_auth_when_no_api_key() -> None:
    client = AionMarketClient()
    headers = client._headers()
    assert "Authorization" not in headers
    assert headers["Content-Type"] == "application/json"


# ============================================================
# ApiError
# ============================================================


def test_api_error_str_representation() -> None:
    err = ApiError(
        message="Not found",
        code=404,
        status_code=404,
        url="https://api.example.com/test",
        method="GET",
    )
    s = str(err)
    assert "404" in s
    assert "Not found" in s
    assert "GET" in s
    assert "/test" in s


def test_api_error_is_exception() -> None:
    err = ApiError(message="fail")
    assert isinstance(err, Exception)


# ============================================================
# _request internals
# ============================================================


def test_url_error_raises_api_error() -> None:
    from urllib.error import URLError

    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", side_effect=URLError("DNS failed")):
        with pytest.raises(ApiError) as exc:
            client.get_me()

    err = exc.value
    assert err.status_code == 500
    assert "DNS failed" in err.message


def test_non_json_response_returned_as_text() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse("plain text response")):
        result = client.get_me()

    assert result == "plain text response"


def test_http_error_with_non_json_body() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch(
        "urllib.request.urlopen",
        side_effect=_http_error("https://api.example.com/agents/me", 500, "Internal Server Error"),
    ):
        with pytest.raises(ApiError) as exc:
            client.get_me()

    assert exc.value.status_code == 500
    assert exc.value.response_body == "Internal Server Error"


def test_http_error_extracts_message_from_error_key() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch(
        "urllib.request.urlopen",
        side_effect=_http_error("https://api.example.com/test", 400, {"error": "bad input"}),
    ):
        with pytest.raises(ApiError) as exc:
            client.get_me()

    assert "bad input" in exc.value.message


def test_http_error_uses_body_code_when_present() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch(
        "urllib.request.urlopen",
        side_effect=_http_error("https://api.example.com/test", 400, {"message": "limit exceeded", "code": 4001}),
    ):
        with pytest.raises(ApiError) as exc:
            client.get_me()

    assert exc.value.code == 4001
    assert exc.value.status_code == 400


def test_public_request_method_passthrough() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse({"ok": True})) as mock_open:
        result = client.request("GET", "/custom/path", params={"x": 1})

    req = mock_open.call_args[0][0]
    assert "/custom/path" in req.full_url
    assert "x=1" in req.full_url
    assert result == {"ok": True}


# ============================================================
# Agent Management (remaining)
# ============================================================


def test_register_agent() -> None:
    response = {"code": 200, "data": {"agentName": "bot-1", "apiKeyCode": "abc"}}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        result = client.register_agent("bot-1")

    req = mock_open.call_args[0][0]
    assert req.get_method() == "POST"
    assert req.full_url.endswith("/agents/register")
    body = json.loads(req.data.decode("utf-8"))
    assert body["name"] == "bot-1"
    assert result["data"]["agentName"] == "bot-1"


def test_get_me() -> None:
    response = {"code": 200, "data": {"agentName": "my-agent"}}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        result = client.get_me()

    req = mock_open.call_args[0][0]
    assert req.get_method() == "GET"
    assert req.full_url.endswith("/agents/me")
    assert result["data"]["agentName"] == "my-agent"


# ============================================================
# Risk Settings
# ============================================================


def test_get_risk_settings() -> None:
    response = {"code": 200, "data": {"tradeLimitEnabled": True, "maxTradeAmount": "5"}}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        result = client.get_risk_settings()

    req = mock_open.call_args[0][0]
    assert req.get_method() == "GET"
    assert req.full_url.endswith("/markets/risk-settings")
    assert result["data"]["tradeLimitEnabled"] is True


def test_set_risk_settings_partial() -> None:
    response = {"code": 200, "data": {"maxTradeAmount": "10", "stopLossPercent": 20}}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        result = client.set_risk_settings(max_trade_amount=10, stop_loss_percent=20)

    req = mock_open.call_args[0][0]
    assert req.get_method() == "POST"
    assert req.full_url.endswith("/markets/risk-settings")
    body = json.loads(req.data.decode("utf-8"))
    assert body["maxTradeAmount"] == 10
    assert body["stopLossPercent"] == 20
    assert "tradeLimitEnabled" not in body


def test_set_risk_settings_all_fields() -> None:
    response = {"code": 200, "data": {"success": True}}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        client.set_risk_settings(
            trade_limit_enabled=True,
            max_trade_amount=50,
            daily_trade_amount_limit=1000,
            max_position_value=500,
            max_trades_per_day=100,
            risk_control_enabled=True,
            take_profit_percent=40,
            stop_loss_percent=20,
        )

    req = mock_open.call_args[0][0]
    body = json.loads(req.data.decode("utf-8"))
    assert body["tradeLimitEnabled"] is True
    assert body["maxTradeAmount"] == 50
    assert body["dailyTradeAmountLimit"] == 1000
    assert body["maxPositionValue"] == 500
    assert body["maxTradesPerDay"] == 100
    assert body["riskControlEnabled"] is True
    assert body["takeProfitPercent"] == 40
    assert body["stopLossPercent"] == 20


def test_delete_risk_settings() -> None:
    response = {"code": 200, "data": {"success": True, "message": "Reset"}}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        result = client.delete_risk_settings()

    req = mock_open.call_args[0][0]
    assert req.get_method() == "DELETE"
    assert req.full_url.endswith("/markets/risk-settings")
    assert result["data"]["success"] is True


# ============================================================
# Skills
# ============================================================


def test_get_skills_defaults() -> None:
    response = {"code": 200, "data": {"skills": []}}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        result = client.get_skills()

    req = mock_open.call_args[0][0]
    assert req.get_method() == "GET"
    assert "/agents/skills" in req.full_url
    assert "limit=20" in req.full_url
    assert "offset=0" in req.full_url


def test_get_skills_with_category() -> None:
    response = {"code": 200, "data": {"skills": [{"name": "weather"}]}}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        result = client.get_skills(category=2, limit=5, offset=10)

    req = mock_open.call_args[0][0]
    assert "category=2" in req.full_url
    assert "limit=5" in req.full_url
    assert "offset=10" in req.full_url


# ============================================================
# Market Operations
# ============================================================


def test_get_markets_search() -> None:
    response = {"code": 200, "data": [{"question": "BTC > 100k?"}]}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        result = client.get_markets(q="bitcoin", limit=10, page=2, venue="kalshi")

    req = mock_open.call_args[0][0]
    assert req.get_method() == "GET"
    assert "q=bitcoin" in req.full_url
    assert "limit=10" in req.full_url
    assert "page=2" in req.full_url
    assert "venue=kalshi" in req.full_url


def test_check_market_exists() -> None:
    response = {"code": 200, "data": {"exists": True}}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        result = client.check_market_exists("512357")

    req = mock_open.call_args[0][0]
    assert "/markets/check" in req.full_url
    assert "marketId=512357" in req.full_url
    assert result["data"]["exists"] is True


def test_get_prices_history_minimal() -> None:
    response = {"code": 200, "data": {"history": []}}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        result = client.get_prices_history("token-abc")

    req = mock_open.call_args[0][0]
    assert "/markets/prices-history" in req.full_url
    assert "market=token-abc" in req.full_url


def test_get_prices_history_all_params() -> None:
    response = {"code": 200, "data": {"history": [{"t": 1000, "p": 0.5}]}}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        result = client.get_prices_history(
            "token-abc", start_ts=1000, end_ts=2000, interval="1h", fidelity=5, venue="kalshi"
        )

    req = mock_open.call_args[0][0]
    assert "startTs=1000" in req.full_url
    assert "endTs=2000" in req.full_url
    assert "interval=1h" in req.full_url
    assert "fidelity=5" in req.full_url
    assert "venue=kalshi" in req.full_url


def test_get_briefing_all_params() -> None:
    response = {"code": 200, "data": {"alerts": []}}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        result = client.get_briefing(
            venue="kalshi", since="2026-04-01T00:00:00Z", user="0xabc", include_markets=False
        )

    req = mock_open.call_args[0][0]
    assert "/markets/briefing" in req.full_url
    assert "venue=kalshi" in req.full_url
    assert "since=2026-04-01" in req.full_url
    assert "user=0xabc" in req.full_url
    assert "includeMarkets=false" in req.full_url


def test_get_market_context() -> None:
    response = {"code": 200, "data": {"market": {}, "positions": {}}}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        result = client.get_market_context("mkt-123", user="0xuser", my_probability=0.75)

    req = mock_open.call_args[0][0]
    assert "/markets/context/mkt-123" in req.full_url
    assert "user=0xuser" in req.full_url
    assert "myProbability=0.75" in req.full_url


def test_get_closed_positions() -> None:
    response = {"code": 200, "data": []}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        client.get_closed_positions(
            user="0xuser", market="0xcond", title="BTC", limit=5, offset=10,
            sort_by="REALIZEDPNL", sort_direction="ASC"
        )

    req = mock_open.call_args[0][0]
    assert "/markets/closed-positions" in req.full_url
    assert "user=0xuser" in req.full_url
    assert "market=0xcond" in req.full_url
    assert "title=BTC" in req.full_url
    assert "limit=5" in req.full_url
    assert "offset=10" in req.full_url
    assert "sortBy=REALIZEDPNL" in req.full_url
    assert "sortDirection=ASC" in req.full_url


def test_get_current_positions() -> None:
    response = {"code": 200, "data": []}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        client.get_current_positions(
            user="0xuser", venue="kalshi", size_threshold=1.5,
            redeemable=True, mergeable=False, sort_by="TOKENS"
        )

    req = mock_open.call_args[0][0]
    assert "/markets/current-positions" in req.full_url
    assert "venue=kalshi" in req.full_url
    assert "sizeThreshold=1.5" in req.full_url
    assert "redeemable=true" in req.full_url
    assert "mergeable=false" in req.full_url
    assert "sortBy=TOKENS" in req.full_url


# ============================================================
# Wallet Management
# ============================================================


def test_check_wallet_credentials() -> None:
    response = {"code": 200, "data": {"exists": True}}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        result = client.check_wallet_credentials("0xwallet")

    req = mock_open.call_args[0][0]
    assert req.get_method() == "GET"
    assert "/wallet/credentials/check" in req.full_url
    assert "walletAddress=0xwallet" in req.full_url


def test_register_wallet_credentials() -> None:
    response = {"code": 200, "data": {"success": True}}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        result = client.register_wallet_credentials("0xwallet", "key1", "secret1", "pass1")

    req = mock_open.call_args[0][0]
    assert req.get_method() == "POST"
    assert "/wallet/credentials" in req.full_url
    body = json.loads(req.data.decode("utf-8"))
    assert body["walletAddress"] == "0xwallet"
    assert body["apiKey"] == "key1"
    assert body["apiSecret"] == "secret1"
    assert body["apiPassphrase"] == "pass1"


def test_wallet_link_challenge() -> None:
    response = {"code": 200, "data": {"nonce": "abc", "message": "Sign this"}}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        result = client.wallet_link_challenge("0xaddr")

    req = mock_open.call_args[0][0]
    assert req.get_method() == "GET"
    assert "/wallet/link/challenge" in req.full_url
    assert "address=0xaddr" in req.full_url


def test_wallet_link() -> None:
    response = {"code": 200, "data": {"success": True}}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        result = client.wallet_link("0xaddr", "0xsig", "nonce123", signature_type=1)

    req = mock_open.call_args[0][0]
    assert req.get_method() == "POST"
    assert "/wallet/link" in req.full_url
    body = json.loads(req.data.decode("utf-8"))
    assert body["address"] == "0xaddr"
    assert body["signature"] == "0xsig"
    assert body["nonce"] == "nonce123"
    assert body["signature_type"] == 1


def test_wallet_unlink() -> None:
    response = {"code": 200, "data": {"success": True}}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        result = client.wallet_unlink()

    req = mock_open.call_args[0][0]
    assert req.get_method() == "POST"
    assert req.full_url.endswith("/wallet/unlink")


def test_get_wallet_positions() -> None:
    response = {"code": 200, "data": {"positions": [], "total_value": "0"}}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        result = client.get_wallet_positions("0xwallet123")

    req = mock_open.call_args[0][0]
    assert req.get_method() == "GET"
    assert "/markets/wallet/0xwallet123/positions" in req.full_url


# ============================================================
# Order Management
# ============================================================


def test_get_open_orders() -> None:
    response = {"code": 200, "data": []}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        client.get_open_orders(venue="kalshi", market_condition_id="0xcond", limit=5)

    req = mock_open.call_args[0][0]
    assert req.get_method() == "GET"
    assert "/markets/orders/open" in req.full_url
    assert "venue=kalshi" in req.full_url
    assert "marketConditionId=0xcond" in req.full_url
    assert "limit=5" in req.full_url


def test_get_order_history() -> None:
    response = {"code": 200, "data": []}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        client.get_order_history(
            market_condition_id="0xcond", order_status=2, limit=10, offset=5
        )

    req = mock_open.call_args[0][0]
    assert "/markets/orders?" in req.full_url or "/markets/orders" in req.full_url
    assert "marketConditionId=0xcond" in req.full_url
    assert "orderStatus=2" in req.full_url
    assert "limit=10" in req.full_url
    assert "offset=5" in req.full_url


def test_get_order_detail() -> None:
    response = {"code": 200, "data": {"orderId": "abc", "status": "LIVE"}}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        result = client.get_order_detail("abc123", wallet_address="0xwallet")

    req = mock_open.call_args[0][0]
    assert "/markets/orders/abc123" in req.full_url
    assert "walletAddress=0xwallet" in req.full_url


def test_cancel_order() -> None:
    response = {"code": 200, "data": {"success": True}}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        result = client.cancel_order("order-1", wallet_address="0xwallet")

    req = mock_open.call_args[0][0]
    assert req.get_method() == "POST"
    assert "/markets/orders/cancel" in req.full_url
    body = json.loads(req.data.decode("utf-8"))
    assert body["orderId"] == "order-1"
    assert body["walletAddress"] == "0xwallet"


def test_cancel_all_orders() -> None:
    response = {"code": 200, "data": {"success": True}}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        client.cancel_all_orders(venue="kalshi", wallet_address="0xwallet")

    req = mock_open.call_args[0][0]
    assert req.get_method() == "POST"
    assert "/markets/orders/cancel-market" in req.full_url
    body = json.loads(req.data.decode("utf-8"))
    assert body["venue"] == "kalshi"
    assert body["walletAddress"] == "0xwallet"


def test_cancel_all_user_orders() -> None:
    response = {"code": 200, "data": {"totalCanceled": 3}}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        result = client.cancel_all_user_orders()

    req = mock_open.call_args[0][0]
    assert req.get_method() == "DELETE"
    assert req.full_url.endswith("/markets/orders")
    assert result["data"]["totalCanceled"] == 3


def test_redeem() -> None:
    response = {"code": 200, "data": {"txHash": "0xabc"}}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        result = client.redeem("mkt-1", "YES", wallet_address="0xwallet")

    req = mock_open.call_args[0][0]
    assert req.get_method() == "POST"
    assert "/markets/redeem" in req.full_url
    body = json.loads(req.data.decode("utf-8"))
    assert body["marketId"] == "mkt-1"
    assert body["side"] == "YES"
    assert body["walletAddress"] == "0xwallet"


# ============================================================
# Kalshi
# ============================================================


def test_kalshi_quote() -> None:
    response = {"code": 200, "data": {"quoteId": "q1", "unsignedTx": "base64..."}}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        result = client.kalshi_quote(
            market_ticker="KXBTC", side="yes", action="buy",
            amount=10.0, user_public_key="sol-pubkey", destination_wallet="sol-dest"
        )

    req = mock_open.call_args[0][0]
    assert req.get_method() == "POST"
    assert "/kalshi/agent/quote" in req.full_url
    body = json.loads(req.data.decode("utf-8"))
    assert body["marketTicker"] == "KXBTC"
    assert body["side"] == "YES"
    assert body["action"] == "BUY"
    assert body["amount"] == 10.0
    assert body["userPublicKey"] == "sol-pubkey"
    assert body["destinationWallet"] == "sol-dest"


def test_kalshi_submit() -> None:
    response = {"code": 200, "data": {"orderId": "k-1", "txSignature": "sig123"}}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        result = client.kalshi_submit(
            market_ticker="KXBTC", side="no", action="sell",
            signed_transaction="base64tx", quote_id="q1",
            user_public_key="sol-pubkey", shares=5.0,
            skill_slug="my-skill", source="sdk", reasoning="test reason"
        )

    req = mock_open.call_args[0][0]
    assert req.get_method() == "POST"
    assert "/kalshi/agent/submit" in req.full_url
    body = json.loads(req.data.decode("utf-8"))
    assert body["marketTicker"] == "KXBTC"
    assert body["side"] == "NO"
    assert body["action"] == "SELL"
    assert body["signedTransaction"] == "base64tx"
    assert body["quoteId"] == "q1"
    assert body["shares"] == 5.0
    assert body["skillSlug"] == "my-skill"
    assert body["reasoning"] == "test reason"


def test_kalshi_submit_optional_dflow_fields() -> None:
    response = {"code": 200, "data": {"orderId": "k-2"}}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        client.kalshi_submit(
            market_ticker="KXBTC", side="yes", action="buy",
            signed_transaction="tx", quote_id="q2",
            user_public_key="pk", amount=10,
            in_amount="10000000", out_amount="9500000", min_out_amount="9000000"
        )

    req = mock_open.call_args[0][0]
    body = json.loads(req.data.decode("utf-8"))
    assert body["inAmount"] == "10000000"
    assert body["outAmount"] == "9500000"
    assert body["minOutAmount"] == "9000000"


# ============================================================
# Trade normalization edge cases
# ============================================================


def _make_v1_trade_payload(**overrides: Any) -> dict:
    """Helper to create a valid V1 trade payload with optional overrides."""
    payload = {
        "marketConditionId": "0x1",
        "marketQuestion": "q",
        "orderSize": 1,
        "price": 0.5,
        "outcome": "YES",
        "order": {
            "maker": "0x1",
            "signer": "0x1",
            "taker": "0x0000000000000000000000000000000000000000",
            "tokenId": "1",
            "makerAmount": "1",
            "takerAmount": "1",
            "side": "BUY",
            "expiration": "0",
            "nonce": "0",
            "feeRateBps": "0",
            "signature": "0xabc",
            "salt": 1,
            "signatureType": 0,
        },
    }
    for k, v in overrides.items():
        if k.startswith("order."):
            payload["order"][k.split(".", 1)[1]] = v
        else:
            payload[k] = v
    return payload


def test_trade_v2_order_detected_by_timestamp() -> None:
    """V2 orders (with 'timestamp') don't require nonce/feeRateBps."""
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")
    payload = {
        "marketConditionId": "0x1",
        "marketQuestion": "q",
        "orderSize": 1,
        "price": 0.5,
        "outcome": "YES",
        "order": {
            "maker": "0x1",
            "signer": "0x1",
            "taker": "0x0",
            "tokenId": "1",
            "makerAmount": "1",
            "takerAmount": "1",
            "side": "SELL",
            "expiration": "0",
            "signature": "0xabc",
            "salt": 1,
            "signatureType": 3,
            "timestamp": "1713000000",
        },
    }

    with patch("urllib.request.urlopen", return_value=_MockResponse({"success": True})):
        result = client.trade(payload)

    assert result["success"] is True


def test_trade_v2_order_detected_by_builder() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")
    payload = {
        "marketConditionId": "0x1",
        "marketQuestion": "q",
        "orderSize": 1,
        "price": 0.5,
        "outcome": "YES",
        "order": {
            "maker": "0x1",
            "signer": "0x1",
            "taker": "0x0",
            "tokenId": "1",
            "makerAmount": "1",
            "takerAmount": "1",
            "side": "BUY",
            "expiration": "0",
            "signature": "0xabc",
            "salt": 1,
            "signatureType": 0,
            "builder": "0xbuilder",
        },
    }

    with patch("urllib.request.urlopen", return_value=_MockResponse({"success": True})):
        result = client.trade(payload)

    assert result["success"] is True


def test_trade_side_from_bytes() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")
    payload = _make_v1_trade_payload()
    payload["order"]["side"] = b"sell"

    with patch("urllib.request.urlopen", return_value=_MockResponse({"ok": True})) as mock_open:
        client.trade(payload)

    body = json.loads(mock_open.call_args[0][0].data.decode("utf-8"))
    assert body["order"]["side"] == "SELL"


def test_trade_side_non_string_raises() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")
    payload = _make_v1_trade_payload()
    payload["order"]["side"] = 123

    with pytest.raises(ValueError, match="must be a string compatible with BUY/SELL"):
        client.trade(payload)


def test_trade_signature_type_non_integer_raises() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")
    payload = _make_v1_trade_payload()
    payload["order"]["signatureType"] = "not-a-number"

    with pytest.raises(ValueError, match="signatureType must be an integer"):
        client.trade(payload)


def test_trade_order_not_dict_raises() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")
    payload = {
        "marketConditionId": "0x1",
        "marketQuestion": "q",
        "orderSize": 1,
        "price": 0.5,
        "outcome": "YES",
        "order": "not-a-dict",
    }
    with pytest.raises(ValueError, match="'order' must be a dict"):
        client.trade(payload)


def test_trade_default_order_type_gtc_when_no_is_limit_order() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")
    payload = _make_v1_trade_payload()
    # no orderType, no isLimitOrder — should default to GTC

    with patch("urllib.request.urlopen", return_value=_MockResponse({"ok": True})) as mock_open:
        client.trade(payload)

    body = json.loads(mock_open.call_args[0][0].data.decode("utf-8"))
    assert body["orderType"] == "GTC"


def test_trade_does_not_mutate_original_payload() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")
    payload = _make_v1_trade_payload()
    payload["order"]["side"] = "buy"
    original_side = payload["order"]["side"]

    with patch("urllib.request.urlopen", return_value=_MockResponse({"ok": True})):
        client.trade(payload)

    # Original payload should not be modified due to deepcopy
    assert payload["order"]["side"] == original_side


# ============================================================
# Query param normalization
# ============================================================


def test_normalize_query_params_list_with_booleans() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")
    result = client._normalize_query_params({"tags": [True, False, "other"]})
    assert result["tags"] == ["true", "false", "other"]


def test_normalize_query_params_non_bool_passthrough() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")
    result = client._normalize_query_params({"count": 42, "name": "test"})
    assert result == {"count": 42, "name": "test"}


# ============================================================
# Remaining coverage gaps
# ============================================================


def test_trade_side_dotted_enum_string() -> None:
    """Side value like 'Side.BUY' stripped to 'BUY' via dot-split."""
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")
    payload = _make_v1_trade_payload()
    payload["order"]["side"] = "Side.SELL"

    with patch("urllib.request.urlopen", return_value=_MockResponse({"ok": True})) as mock_open:
        client.trade(payload)

    body = json.loads(mock_open.call_args[0][0].data.decode("utf-8"))
    assert body["order"]["side"] == "SELL"


def test_trade_v1_missing_nonce_raises() -> None:
    """V1 order missing nonce/feeRateBps should raise."""
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")
    payload = {
        "marketConditionId": "0x1",
        "marketQuestion": "q",
        "orderSize": 1,
        "price": 0.5,
        "outcome": "YES",
        "order": {
            "maker": "0x1",
            "signer": "0x1",
            "taker": "0x0",
            "tokenId": "1",
            "makerAmount": "1",
            "takerAmount": "1",
            "side": "BUY",
            "expiration": "0",
            "signature": "0xabc",
            "salt": 1,
            "signatureType": 0,
            # missing nonce and feeRateBps
        },
    }

    with pytest.raises(ValueError, match="V1 trade.order missing required fields"):
        client.trade(payload)


def test_update_settings_all_fields() -> None:
    response = {"code": 200, "data": {"success": True}}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        client.update_settings(
            max_trades_per_day=100,
            max_trade_amount=50,
            trade_limit_enabled=True,
            daily_trade_amount_limit=1000,
            max_position_value=500,
            risk_control_enabled=True,
            take_profit_percent=40,
            stop_loss_percent=20,
        )

    req = mock_open.call_args[0][0]
    body = json.loads(req.data.decode("utf-8"))
    assert body["maxTradesPerDay"] == 100
    assert body["maxTradeAmount"] == 50
    assert body["tradeLimitEnabled"] is True
    assert body["dailyTradeAmountLimit"] == 1000
    assert body["maxPositionValue"] == 500
    assert body["riskControlEnabled"] is True
    assert body["takeProfitPercent"] == 40
    assert body["stopLossPercent"] == 20


def test_get_current_positions_all_optional_params() -> None:
    """Cover the market, title, sort_direction branches."""
    response = {"code": 200, "data": []}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        client.get_current_positions(
            user="0xu", market="0xcond", title="ETH",
            sort_by="CASHPNL", sort_direction="DESC"
        )

    req = mock_open.call_args[0][0]
    assert "market=0xcond" in req.full_url
    assert "title=ETH" in req.full_url
    assert "sortDirection=DESC" in req.full_url


def test_kalshi_quote_with_shares() -> None:
    response = {"code": 200, "data": {"quoteId": "q1"}}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        client.kalshi_quote("KXBTC", side="yes", action="sell", shares=5.0)

    body = json.loads(mock_open.call_args[0][0].data.decode("utf-8"))
    assert body["shares"] == 5.0
    assert "amount" not in body


def test_kalshi_submit_with_destination_wallet() -> None:
    response = {"code": 200, "data": {"orderId": "k-3"}}
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with patch("urllib.request.urlopen", return_value=_MockResponse(response)) as mock_open:
        client.kalshi_submit(
            market_ticker="KXBTC", side="yes", action="buy",
            signed_transaction="tx", quote_id="q1",
            user_public_key="pk", amount=10,
            destination_wallet="sol-dest-wallet"
        )

    body = json.loads(mock_open.call_args[0][0].data.decode("utf-8"))
    assert body["destinationWallet"] == "sol-dest-wallet"


def test_batch_trade_order_not_dict_raises() -> None:
    """batch_trade passes orders to _normalize_trade_payload which checks order is dict."""
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    with pytest.raises(ValueError, match="'order' must be a dict"):
        client.batch_trade([{"order": "not-a-dict"}])
