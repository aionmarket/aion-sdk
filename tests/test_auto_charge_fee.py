"""Tests for auto-charge fee behavior in trade() and kalshi_submit()."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

from aion_sdk import AionMarketClient


class _MockResponse:
    """Mimic urllib's response with .read() / context manager."""

    def __init__(self, payload: Any, status: int = 200) -> None:
        self._body = json.dumps(payload).encode("utf-8")
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_MockResponse":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


def _make_v2_trade_payload(**overrides: Any) -> dict:
    payload: dict = {
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
            "signature": "0xabc",
            "salt": 1,
            "signatureType": 3,
            "timestamp": "1713000000",
        },
    }
    for k, v in overrides.items():
        if k.startswith("order."):
            payload["order"][k.split(".", 1)[1]] = v
        else:
            payload[k] = v
    return payload


# ============================================================
# Auto-charge for Polymarket — Safe / Proxy / Deposit (sigType != 0)
# ============================================================


def test_trade_auto_charges_polymarket_fee_on_success() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")
    payload = _make_v2_trade_payload()
    payload["orderSize"] = 10
    payload["price"] = 0.55
    payload["walletAddress"] = "0x1111111111111111111111111111111111111111"

    captured: list = []

    def _handle(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        body = json.loads(req.data.decode("utf-8")) if req.data else {}
        captured.append({"url": url, "body": body})
        if url.endswith("/markets/trade"):
            return _MockResponse(
                {
                    "success": True,
                    "orderId": "oid-1",
                    "walletAddress": payload["walletAddress"],
                }
            )
        if url.endswith("/aiagent/charge-fee/polymarket/trade-fee"):
            return _MockResponse({"data": {"id": "fb-1", "status": "SUBMITTED"}})
        raise AssertionError(f"unexpected url {url}")

    with patch("urllib.request.urlopen", side_effect=_handle):
        out = client.trade(payload)

    assert out["success"] is True
    assert out["feeCharge"]["status"] == "ok"
    assert out["feeCharge"]["amount"] == "0.055000"
    assert out["feeCharge"]["safeAddress"] == payload["walletAddress"]
    assert out["feeCharge"]["signatureType"] == 3
    trade_body = next(c["body"] for c in captured if c["url"].endswith("/markets/trade"))
    assert trade_body["feeAmount"] == 0.055
    fee_body = next(
        c["body"]
        for c in captured
        if c["url"].endswith("/aiagent/charge-fee/polymarket/trade-fee")
    )
    assert fee_body == {"amount": "0.055000", "safeAddress": payload["walletAddress"]}


def test_trade_auto_charge_can_be_disabled() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")
    payload = _make_v2_trade_payload()

    seen_urls: list = []

    def _handle(req, timeout=None):
        seen_urls.append(req.full_url if hasattr(req, "full_url") else req.get_full_url())
        return _MockResponse({"success": True, "orderId": "oid"})

    with patch("urllib.request.urlopen", side_effect=_handle):
        out = client.trade(payload, auto_charge_fee=False)

    assert out["success"] is True
    assert "feeCharge" not in out
    assert seen_urls == ["https://api.example.com/markets/trade"]


def test_trade_auto_charge_skipped_when_trade_fails() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")
    payload = _make_v2_trade_payload()

    seen_urls: list = []

    def _handle(req, timeout=None):
        seen_urls.append(req.full_url if hasattr(req, "full_url") else req.get_full_url())
        return _MockResponse({"success": False, "errorMsg": "rejected"})

    with patch("urllib.request.urlopen", side_effect=_handle):
        out = client.trade(payload)

    assert out["success"] is False
    assert out["feeCharge"]["status"] == "skipped"
    assert seen_urls == ["https://api.example.com/markets/trade"]


def test_trade_auto_charge_marks_failed_but_returns_trade_response() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")
    payload = _make_v2_trade_payload()

    def _handle(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/markets/trade"):
            return _MockResponse({"success": True, "orderId": "oid"})
        if url.endswith("/aiagent/charge-fee/polymarket/trade-fee"):
            raise RuntimeError("fireblocks down")
        raise AssertionError(f"unexpected url {url}")

    with patch("urllib.request.urlopen", side_effect=_handle):
        out = client.trade(payload)

    assert out["success"] is True
    assert out["feeCharge"]["status"] == "failed"
    assert "fireblocks down" in out["feeCharge"]["error"]


# ============================================================
# EOA fee path (signatureType=0 -> /eoa-trade-fee)
# ============================================================


def test_trade_auto_charge_routes_to_eoa_endpoint_for_signature_type_0() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")
    payload = _make_v2_trade_payload(**{"order.signatureType": 0})
    payload["orderSize"] = 4
    payload["price"] = 0.5
    payload["walletAddress"] = "0x2222222222222222222222222222222222222222"

    captured: list = []

    def _handle(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        body = json.loads(req.data.decode("utf-8")) if req.data else {}
        captured.append({"url": url, "body": body})
        if url.endswith("/markets/trade"):
            return _MockResponse(
                {
                    "success": True,
                    "orderId": "oid-eoa",
                    "walletAddress": payload["walletAddress"],
                }
            )
        if url.endswith("/aiagent/charge-fee/polymarket/eoa-trade-fee"):
            return _MockResponse({"data": {"id": "fb-eoa", "status": "SUBMITTED"}})
        raise AssertionError(f"unexpected url {url}")

    with patch("urllib.request.urlopen", side_effect=_handle):
        out = client.trade(payload)

    assert out["feeCharge"]["status"] == "ok"
    assert out["feeCharge"]["signatureType"] == 0
    assert out["feeCharge"]["amount"] == "0.020000"
    assert out["feeCharge"]["eoaAddress"] == payload["walletAddress"]
    fee_call = next(
        c
        for c in captured
        if c["url"].endswith("/aiagent/charge-fee/polymarket/eoa-trade-fee")
    )
    assert fee_call["body"] == {
        "amount": "0.020000",
        "eoaAddress": payload["walletAddress"],
    }


def test_charge_polymarket_eoa_fee_explicit_call() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    def _handle(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        body = json.loads(req.data.decode("utf-8"))
        assert url == (
            "https://api.example.com/aiagent/charge-fee/polymarket/eoa-trade-fee"
        )
        assert body == {"amount": "0.10", "eoaAddress": "0xabc"}
        return _MockResponse({"data": {"id": "fb-1", "status": "OK"}})

    with patch("urllib.request.urlopen", side_effect=_handle):
        out = client.charge_polymarket_eoa_fee(amount="0.10", eoa_address="0xabc")

    assert out == {"data": {"id": "fb-1", "status": "OK"}}


def test_get_polymarket_eoa_spender() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    def _handle(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        assert url == (
            "https://api.example.com/aiagent/charge-fee/polymarket/spender"
        )
        return _MockResponse(
            {
                "spender": "0xVAULT",
                "platformFeeAddress": "0xFEE",
                "tokenAddress": "0xPUSD",
                "decimals": 6,
                "chainId": 137,
            }
        )

    with patch("urllib.request.urlopen", side_effect=_handle):
        out = client.get_polymarket_eoa_spender()

    assert out["spender"] == "0xVAULT"
    assert out["chainId"] == 137


# ============================================================
# Kalshi auto-charge
# ============================================================


def test_kalshi_submit_auto_charges_fee_on_buy_success() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    def _handle(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/kalshi/agent/submit"):
            return _MockResponse(
                {"orderId": "kid-1", "txSignature": "sig-1", "orderStatus": "OK"}
            )
        if url.endswith("/aiagent/charge-fee/kalshi/trade-fee"):
            return _MockResponse({"data": {"txSignature": "fb-sig", "status": "OK"}})
        raise AssertionError(f"unexpected url {url}")

    with patch("urllib.request.urlopen", side_effect=_handle):
        out = client.kalshi_submit(
            market_ticker="KX",
            side="YES",
            action="BUY",
            signed_transaction="signed",
            quote_id="q1",
            user_public_key="solwallet",
            amount=10,
        )

    assert out["txSignature"] == "sig-1"
    assert out["feeCharge"]["status"] == "ok"
    assert out["feeCharge"]["amount"] == "0.100000"
    assert out["feeCharge"]["fromAddress"] == "solwallet"


def test_kalshi_submit_does_not_auto_charge_for_sell() -> None:
    client = AionMarketClient(api_key="k", base_url="https://api.example.com")

    seen_urls: list = []

    def _handle(req, timeout=None):
        seen_urls.append(req.full_url if hasattr(req, "full_url") else req.get_full_url())
        return _MockResponse(
            {"orderId": "kid-2", "txSignature": "sig-2", "orderStatus": "OK"}
        )

    with patch("urllib.request.urlopen", side_effect=_handle):
        out = client.kalshi_submit(
            market_ticker="KX",
            side="YES",
            action="SELL",
            signed_transaction="signed",
            quote_id="q1",
            user_public_key="solwallet",
            shares=5,
        )

    assert out["feeCharge"]["status"] == "skipped"
    assert seen_urls == ["https://api.example.com/kalshi/agent/submit"]
