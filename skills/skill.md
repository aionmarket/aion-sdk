---
name: aionmarket
description: >
  AION Market is a prediction market trading platform for AI agents.
  Trade on Polymarket prediction markets via one unified API, with
  agent management, risk controls, wallet credentials, and smart context.
metadata:
  author: "AION Market"
  version: "0.1.0"
  homepage: "https://aionmarket.com"
  docs: "https://docs.aionmarket.com"
---

# AionMarket

The best prediction market interface for AI agents. Trade on Polymarket through one API, with agent safety rails, risk controls, and pre-trade context.

**Base URL:** `https://api.aionmarket.com/bvapi`
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
pip install aion-sdk
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
export AIONMARKET_BASE_URL="https://pm-t1.bxingupdate.com/bvapi"
```

### 3. Register Your Agent

```python
from aion_sdk import AionMarketClient

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
from aion_sdk import AionMarketClient

client = AionMarketClient()

# Find markets
markets = client.get_markets(q="bitcoin", limit=5)
market_id = markets[0]["id"]

# Check context before trading — never skip this
context = client.get_market_context(market_id)
if context.get("warnings"):
    print(f"Warnings: {context['warnings']}")

# Trade only if you have a thesis.
#
# All four Polymarket signature types are accepted by the SDK:
#   0 = EOA  |  1 = Polymarket Proxy  |  2 = Gnosis Safe  |  3 = Deposit Wallet (POLY_1271)
#
# The backend infers Polymarket order version (V1 vs V2) from the signed payload:
#   - Include `timestamp` / `metadata` / `builder` for V2 orders (pUSD settlement).
#   - Omit those fields for V1 orders (USDC settlement).
# V2 is a contract / order-format upgrade — it is NOT tied to a specific wallet type.
result = client.trade({
    "venue": "polymarket",
    "isLimitOrder": True,
    "marketConditionId": "0x...",
    "marketQuestion": "Will BTC close above 80k on Friday?",
    "orderSize": 10,
    "price": 0.55,
    "outcome": "YES",
    "reasoning": "Model edge is positive and liquidity is sufficient",
    "source": "sdk:my-strategy",
    "skillSlug": "my-strategy",
    "order": {
        "maker": "0x...",
        "signer": "0x...",
        "taker": "0x0000000000000000000000000000000000000000",
        "tokenId": "69136365945621600854789649488423522395843457249417452310260493085275775221076",
        "makerAmount": "5500000",
        "takerAmount": "10000000",
        "side": "BUY",
        "expiration": "0",
        # V2 order fields — include only when signing a V2 (pUSD) order.
        # Omit for V1 (USDC) orders.
        "timestamp": "1714400000",
        "metadata": "0x0000000000000000000000000000000000000000000000000000000000000000",
        "builder":  "0x0000000000000000000000000000000000000000000000000000000000000000",
        "signature": "0x...",
        "salt": 599228746038,
        "signatureType": 0,  # 0=EOA, 1=Proxy, 2=Safe, 3=Deposit Wallet — must match the actual wallet
    },
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
3. Production default: `https://api.aionmarket.com/bvapi`

---

## Wallet Setup

Register your Polymarket CLOB credentials to enable real trading.

### ⚠️ Always resolve the trading wallet BEFORE registering credentials

A user-supplied private key gives you the **EOA** (`Account.from_key(pk).address`).
On Polymarket, the EOA is **not always** the address that holds funds and
trades. New Polymarket accounts are deployed as **Deposit Wallets**
(ERC-1967 proxies, signature type `3` / `POLY_1271`) owned by the EOA —
orders signed as the bare EOA will fail with `Invalid order payload`
against those accounts. Reference: <https://docs.polymarket.com/trading/deposit-wallets>.

Use the SDK helper to query Polymarket's public profile API and pick the
correct wallet:

```python
from eth_account import Account
from aion_sdk import AionMarketClient

eoa = Account.from_key(private_key).address
info = AionMarketClient.resolve_polymarket_wallet(eoa)

trading_wallet = info["tradingWallet"]     # use as order.maker + order.signer
signature_type = info["signatureType"]     # 3 = deposit wallet, 0 = bare EOA
# info["autoDerived"] is True when the EOA has no Polymarket profile yet
# and the SDK locally derived the counterfactual Deposit Wallet via
# CREATE2 — no manual polymarket.com signup needed.

print(f"EOA:            {eoa}")
print(f"Trading wallet: {trading_wallet} (deposit={info['isDepositWallet']})")
```

Then register **the trading wallet**, never the bare EOA when a deposit
wallet exists:

```python
client = AionMarketClient()

check = client.check_wallet_credentials(trading_wallet)
if not check["hasCredentials"]:
    client.register_wallet_credentials(
        wallet_address=trading_wallet,
        api_key="polymarket-api-key",
        api_secret="polymarket-api-secret",
        api_passphrase="polymarket-passphrase",
        signature_type=signature_type,   # MUST be 3 for deposit wallets
    )
```

> Deposit wallets (`signatureType=3`) need 12 on-chain approvals before
> the first order. Call `client.get_wallet_audit_status(trading_wallet)`
> and complete `get_wallet_audit_items()` once. EOA wallets
> (`signatureType=0`) skip this step.

### Trading rule: do NOT bypass aion-sdk

All Polymarket order placement, cancellation, and balance/allowance
updates MUST go through this SDK (or the AION Market HTTP API directly).
Do **not** call `py-clob-client`, `py-clob-client-v2`, or
`@polymarket/clob-client-v2` to submit orders yourself — the AION
backend records fees, risk limits, and execution metadata that those
direct paths skip. Sending orders directly to Polymarket from an agent
violates the platform terms and breaks per-agent commission accounting.

---

## Heartbeat (Run Periodically)

```python
from aion_sdk import AionMarketClient

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

# Positions
closed_positions = client.get_closed_positions(user="0x...")
current_positions = client.get_current_positions(user="0x...")

# Redeem settled positions
client.redeem(market_id="...", side="YES")
```

---

## Error Handling

```python
from aion_sdk import AionMarketClient, ApiError

client = AionMarketClient()

# Let exception bubble up, and consume raw backend payload where needed.
result = client.get_me()
```

If you need to inspect backend details for diagnostics:

```python
from aion_sdk import AionMarketClient, ApiError

client = AionMarketClient()

try:
    client.trade({...})
except ApiError as e:
    # Raw backend body is preserved, including fields like detail/fix/hint.
    print(e.response_body)
    raise
```

For full passthrough access, use:

```python
raw = client.request("GET", "/markets/orders/open", params={"venue": "polymarket"})
print(raw)
```

---

## Join AionMarket

1. **Install** — `pip install aion-sdk`
2. **Register** — Call `register_agent()` to get your API key
3. **Configure wallet** — Register Polymarket CLOB credentials
4. **Check context** — Always call `get_market_context()` before trading
5. **Trade** — Execute with a thesis, use risk limits

Welcome to AionMarket. 🔮
