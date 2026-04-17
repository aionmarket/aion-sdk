from __future__ import annotations

import io
import json
from typing import Any
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

from aionmarket_sdk.client import AionMarketClient, ApiError


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
