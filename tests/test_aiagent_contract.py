from __future__ import annotations

import re
from pathlib import Path


def _workspace_root() -> Path:
    # aionmarket-sdk/tests -> aionmarket-sdk -> bv-market-front
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
    # Supports both required fields `foo!: string;` and optional fields `bar?: string;`.
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


def test_briefing_query_contract_contains_include_markets() -> None:
    dto_path = (
        _workspace_root()
        / "apps"
        / "api"
        / "src"
        / "modules"
        / "aiagent"
        / "dto"
        / "aiagent-market-request.dto.ts"
    )
    source = _read(dto_path)

    cls = _extract_class_block(source, "AiAgentBriefingQueryDto")
    fields = _parse_ts_fields(cls)

    assert "includeMarkets" in fields
    assert fields["includeMarkets"] is True


def test_trade_request_dto_required_fields_match_sdk_trade_contract() -> None:
    dto_path = (
        _workspace_root()
        / "apps"
        / "api"
        / "src"
        / "modules"
        / "aiagent"
        / "dto"
        / "aiagent-trade-request.dto.ts"
    )
    source = _read(dto_path)

    cls = _extract_class_block(source, "AiAgentTradeRequestDto")
    fields = _parse_ts_fields(cls)

    dto_required = {name for name, is_optional in fields.items() if not is_optional}
    sdk_required = {
        "marketConditionId",
        "marketQuestion",
        "orderSize",
        "price",
        "outcome",
        "order",
    }

    assert dto_required == sdk_required


def test_trade_request_dto_field_names_cover_sdk_payload_fields() -> None:
    dto_path = (
        _workspace_root()
        / "apps"
        / "api"
        / "src"
        / "modules"
        / "aiagent"
        / "dto"
        / "aiagent-trade-request.dto.ts"
    )
    source = _read(dto_path)

    cls = _extract_class_block(source, "AiAgentTradeRequestDto")
    fields = _parse_ts_fields(cls)

    sdk_payload_fields = {
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
    }

    assert sdk_payload_fields.issubset(set(fields.keys()))


def test_trade_order_dto_required_fields_match_sdk_nested_order_contract() -> None:
    dto_path = (
        _workspace_root()
        / "apps"
        / "api"
        / "src"
        / "modules"
        / "aiagent"
        / "dto"
        / "aiagent-trade-request.dto.ts"
    )
    source = _read(dto_path)

    cls = _extract_class_block(source, "AiAgentTradeOrderPayloadDto")
    fields = _parse_ts_fields(cls)

    dto_required = {name for name, is_optional in fields.items() if not is_optional}
    sdk_required = {
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
    }

    assert dto_required == sdk_required


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

    assert "context.get(\"market\", {})" in source
    assert "context.get(\"positions\", {})" in source
    assert "context.get(\"safeguards\", {})" in source
    assert "briefing.get(\"opportunities\", {}).get(\"newMarkets\", [])" in source
