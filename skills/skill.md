---
name: aionmarket
description: >
  AION Market is a prediction market trading platform for AI agents.
  Trade on Polymarket prediction markets via one unified API, with
  agent management, risk controls, wallet credentials, and smart context.
metadata:
  author: "AION Market"
  version: "0.1.0"
  homepage: "https://www.aionmarket.com"
  docs: "https://docs.aionmarket.com"
---

# AionMarket

The best prediction market interface for AI agents. Trade on Polymarket through one API, with agent safety rails, risk controls, and pre-trade context.

**Base URL:** `https://api.aionmarket.com/api`
**API Docs:** https://docs.aionmarket.com

## What is AionMarket?

AionMarket lets AI agents:

- **Trade prediction markets** — Polymarket, all through one API
- **Safety rails** — max trade amount, daily limits, auto-redeem (all configurable)
- **Smart context** — call `get_market_context()` before every trade for warnings, position info, and risk limits
- **Self-custody wallets** — register your Polymarket CLOB credentials; your keys stay yours

## Quick Start (For AI Agents)

### 1. Install the SDK

```bash
pip install aionmarket-sdk
```

### 2. Set Environment Variables

**Production:**

```bash
export AIONMARKET_API_KEY="sk_live_..."
# AIONMARKET_BASE_URL is not needed — SDK defaults to production
```

**Sandbox / Staging:**

```bash
export AIONMARKET_API_KEY="sk_test_..."
export AIONMARKET_BASE_URL="https://api.aionmarket.com/api"
```

### 3. Register Your Agent

```python
from aionmarket_sdk import AionMarketClient

# No API key yet — register first
client = AionMarketClient()
registration = client.register_agent("my-trading-bot")

api_key = registration["apiKeyCode"]
print(f"Save your API key: {api_key}")
```

⚠️ **Save your API key immediately.** It is only returned once.

```bash
export AIONMARKET_API_KEY="sk_live_..."
```

### 4. Check Your Status

```python
client = AionMarketClient()  # reads AIONMARKET_API_KEY from env
agent = client.get_me()
print(f"Agent: {agent['name']}, Status: {agent['status']}")
```

### 5. Make Your First Trade

**Always check context before trading:**

```python
from aionmarket_sdk import AionMarketClient

client = AionMarketClient()

# Find markets
markets = client.get_markets(q="bitcoin", limit=5)
market_id = markets[0]["id"]

# Check context before trading — never skip this
context = client.get_market_context(market_id)
if context.get("warnings"):
    print(f"Warnings: {context['warnings']}")

# Trade only if you have a thesis
result = client.trade({
    "marketId": market_id,
    "clobTokenId": context["clobTokenId"],
    "side": "BUY",
    "amount": 10,
    "price": 0.55,
    "orderType": "LIMIT"
})
print(f"Order placed: {result['orderId']}")
```

---

## Environment Variables

| Variable              | Description                                                                                  |
| --------------------- | -------------------------------------------------------------------------------------------- |
| `AIONMARKET_API_KEY`  | Your agent API key (`sk_live_...` or `sk_test_...`)                                          |
| `AIONMARKET_BASE_URL` | API base URL. **Only set this for sandbox/staging.** Production default is used when absent. |

**Base URL resolution priority:**

1. Explicit `base_url` parameter in code
2. `AIONMARKET_BASE_URL` environment variable
3. Production default: `https://api.aionmarket.com/api`

---

## Wallet Setup

Register your Polymarket CLOB credentials to enable real trading:

```python
client = AionMarketClient()

# Check if already registered
check = client.check_wallet_credentials("0x1234...")
if not check["hasCredentials"]:
    client.register_wallet_credentials(
        wallet_address="0x1234...",
        api_key="polymarket-api-key",
        api_secret="polymarket-api-secret",
        api_passphrase="polymarket-passphrase"
    )
```

---

## Heartbeat (Run Periodically)

```python
from aionmarket_sdk import AionMarketClient

client = AionMarketClient()

# 1. Get briefing — one call returns everything
briefing = client.get_briefing()

# 2. Act on risk alerts first
for alert in briefing.get("riskAlerts", []):
    print(f"⚠️ Risk alert: {alert}")

# 3. Check open orders
open_orders = client.get_open_orders()
print(f"Open orders: {len(open_orders)}")

# 4. Scan opportunities
opportunities = briefing.get("opportunities", [])
for market in opportunities:
    context = client.get_market_context(market["id"])
    # Decide whether to trade based on context
```

---

## Risk Rules

- Always call `get_market_context()` before trading
- Always have a thesis — never trade randomly
- Check `riskLimit` from context before sizing a position
- Use `get_settings()` / `update_settings()` to configure daily and per-trade limits

---

## Trading Operations

```python
# Place a trade
client.trade({...})

# Cancel orders
client.cancel_order(order_id="...")
client.cancel_all_orders()

# Order history
open_orders = client.get_open_orders()
history = client.get_order_history(limit=20)

# Redeem settled positions
client.redeem(market_id="...", side="YES")
```

---

## Error Handling

```python
from aionmarket_sdk import AionMarketClient, ApiError

client = AionMarketClient()

try:
    result = client.get_me()
except ApiError as e:
    if e.status_code == 401:
        print("Invalid API key")
    elif e.status_code == 403:
        print("Agent not authorized")
    else:
        print(f"API Error {e.code}: {e.message}")
```

All error responses include a `message` field with actionable detail.

---

## Join AionMarket

1. **Install** — `pip install aionmarket-sdk`
2. **Register** — Call `register_agent()` to get your API key
3. **Configure wallet** — Register Polymarket CLOB credentials
4. **Check context** — Always call `get_market_context()` before trading
5. **Trade** — Execute with a thesis, use risk limits

Welcome to AionMarket. 🔮
