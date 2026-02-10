# Current Trade Rules & Settings

## Overview
This document outlines all trading rules, limits, and safety parameters currently configured in the trading bot.

**Last Updated:** January 9, 2026
**Configuration Files:** `.env`, `config.py`, `trading_bot.py`, `pacifica_client.py`

---

## 🎯 Core Trading Parameters

### Position Limits
| Parameter | Value | Configurable | Location |
|-----------|-------|--------------|----------|
| **Max Open Positions** | **5** | Yes (`.env`) | `MAX_POSITIONS=5` |
| **Default Leverage** | **10x** | Yes (`.env`) | `DEFAULT_LEVERAGE=10` |
| **Leverage Safety Margin** | **90%** | No (hardcoded) | `trading_bot.py:166` |
| **Max Risk Per Trade** | **2%** | Yes (`.env`) | `MAX_RISK_PER_TRADE=0.02` |
| **Max Risk Cap** | **10% of balance** | No (hardcoded) | `trading_bot.py:178` |

**Calculation Examples:**
- With $10,000 balance and 10x leverage:
  - Max position value: $10,000 × 10 × 0.9 = **$90,000**
  - Max risk per trade: $10,000 × 0.02 = **$200**
  - Hard cap: $10,000 × 0.10 = **$1,000 per trade**

---

## 🛡️ Safety Features & Circuit Breakers

### Loss Protection
| Feature | Threshold | Action | Configurable |
|---------|-----------|--------|--------------|
| **Circuit Breaker** | **-$1,000** P&L | Auto-stop trading | Hardcoded in `trading_bot.py:47` |
| **Warning Alert** | **-$800** P&L | Log warning (80% of threshold) | Hardcoded |
| **Manual Override** | N/A | Required to restart after circuit breaker | - |

**How It Works:**
1. Bot monitors total unrealized P&L every 60 seconds
2. At -$800: Logs warning message
3. At -$1,000: Triggers circuit breaker, stops bot automatically
4. Bot cannot resume trading until manually restarted

**To Change Circuit Breaker:**
Edit `trading_bot.py` line 47:
```python
self._max_loss_threshold = -1000.0  # Change to your desired value
```

---

## 💰 Order Execution Rules

### Order Types
| Order Type | Supported | Default Settings |
|------------|-----------|------------------|
| **Market Orders** | ✅ Yes | Default for signals |
| **Limit Orders** | ✅ Yes | Available via API |
| **Stop Loss** | ❌ Not implemented | - |
| **Take Profit** | ❌ Not implemented | - |

### Market Order Settings
| Parameter | Value | Location | Configurable |
|-----------|-------|----------|--------------|
| **Slippage Tolerance** | **0.5%** | `pacifica_client.py:242` | No (hardcoded) |
| **Time in Force (Limit)** | **GTC** (Good Till Cancel) | `pacifica_client.py:238` | No (hardcoded) |

**To Change Slippage:**
Edit `pacifica_client.py` line 242:
```python
payload["slippage_percent"] = "0.5"  # Change to desired %
```

---

## 🤖 Trading Signals & Strategy

### Current Strategy
| Parameter | Setting | Status |
|-----------|---------|--------|
| **Strategy Type** | Random signals | ⚠️ **TEST MODE** |
| **Signal Probability** | 5% per market per check | Mock implementation |
| **Check Interval** | 60 seconds | Main loop frequency |
| **Markets Checked** | First 5 markets only | Hardcoded limit |

**Signal Generation Logic:**
```python
# trading_bot.py lines 111-114
if random.random() < 0.05:
    self._execute_signal(symbol, 'buy', 1.0)
if random.random() < 0.05:
    self._execute_signal(symbol, 'sell', 1.0)
```

⚠️ **WARNING:** This is a **placeholder strategy** for testing infrastructure only. Replace with real technical analysis before live trading.

**Recommended Next Steps:**
1. Implement technical indicators (RSI, MACD, MA crossovers)
2. Add stop-loss and take-profit logic
3. Implement proper position sizing based on volatility
4. Remove random signal generation

---

## 📊 Position Sizing Rules

### Size Validation Checks
The bot validates every order through multiple checks:

**1. Position Count Check**
- Max positions: 5 (configurable)
- Checked before placing new order
- Action: Skip signal if limit reached

**2. Leverage Validation**
- Formula: `position_value ≤ account_balance × leverage × 0.9`
- Example: $10,000 × 10 × 0.9 = $90,000 max
- Action: Reject order if exceeds limit

**3. Risk Validation**
- Per-trade risk: 2% of balance (configurable)
- Hard cap: 10% of balance (not configurable)
- Action: Reject order if exceeds either limit

**4. Price Data Check**
- Requires valid market price
- Action: Reject order if price unavailable

**Validation Flow:**
```
Signal Generated
    ↓
Circuit Breaker Check → [HALT if triggered]
    ↓
Position Count Check → [SKIP if max reached]
    ↓
Get Market Price → [SKIP if unavailable]
    ↓
Calculate Position Value
    ↓
Leverage Check → [REJECT if exceeds 90% of max]
    ↓
Risk Check → [REJECT if exceeds 10% cap]
    ↓
Execute Order
```

---

## 🌐 Network & API Settings

### Request Configuration
| Parameter | Value | Purpose | Location |
|-----------|-------|---------|----------|
| **API Timeout** | **30 seconds** | Prevents hanging | `pacifica_client.py:19` |
| **Retry Attempts** | **3 attempts** | Handle transient errors | `pacifica_client.py:82-86` |
| **Retry Backoff** | **2s → 4s → 8s** | Exponential backoff | Same |
| **Retry On** | Timeout, Connection errors | Selective retry | Same |
| **Rate Limit Handling** | Immediate fail (no retry) | Respect API limits | `pacifica_client.py:118` |

### Testnet Configuration
| Setting | Value |
|---------|-------|
| **Mode** | Testnet (enabled) |
| **API Base URL** | `https://test-api.pacifica.fi/api/v1` |
| **Mainnet URL** | `https://api.pacifica.fi/api/v1` (disabled) |

**To Switch to Mainnet:**
⚠️ **DANGER:** Only for production trading
```bash
# In .env file
TESTNET=false
```

---

## 💾 Data Management

### Account Balance Caching
| Parameter | Value | Purpose |
|-----------|-------|---------|
| **Cache Duration** | 60 seconds | Reduce API calls |
| **Fallback Balance** | $10,000 | If fetch fails |
| **Refresh Trigger** | Auto on timeout | Lazy refresh |

### Database Operations
| Operation | Frequency | Notes |
|-----------|-----------|-------|
| **Save Trades** | Immediate | After order execution |
| **Update Positions** | Every 60s | During main loop |
| **Risk Monitoring** | Every 60s | Check P&L for circuit breaker |
| **Balance Fetch** | Max every 60s | Cached |

---

## ⚙️ Feature Flags

### Current Settings
| Feature | Status | Location |
|---------|--------|----------|
| **Auto Trading** | **DISABLED** ✅ | `ENABLE_AUTO_TRADING=false` |
| **Risk Management** | ENABLED | `ENABLE_RISK_MANAGEMENT=true` |
| **Real-time Data** | ENABLED | `ENABLE_REAL_TIME_DATA=true` |
| **Backtesting** | DISABLED | `ENABLE_BACKTESTING=false` |

⚠️ **KEEP AUTO TRADING DISABLED** until:
1. Real trading strategy is implemented
2. Tested thoroughly on testnet
3. Manually verified order execution
4. Monitored for at least 24 hours

---

## 📝 Quick Configuration Guide

### To Adjust Risk (Conservative → Aggressive)

**Conservative (Low Risk):**
```bash
MAX_POSITIONS=3
DEFAULT_LEVERAGE=5
MAX_RISK_PER_TRADE=0.01  # 1%
```

**Moderate (Current Default):**
```bash
MAX_POSITIONS=5
DEFAULT_LEVERAGE=10
MAX_RISK_PER_TRADE=0.02  # 2%
```

**Aggressive (Higher Risk):**
```bash
MAX_POSITIONS=10
DEFAULT_LEVERAGE=20
MAX_RISK_PER_TRADE=0.05  # 5%
```

⚠️ **Note:** Also update circuit breaker threshold in code if increasing risk.

### To Modify Circuit Breaker

**Current:** -$1,000 (stops at 10% loss on $10K account)

**To Change:**
1. Edit `trading_bot.py` line 47
2. Recommended: 10-20% of account balance
3. Examples:
   - Conservative: `-500.0` (5% on $10K)
   - Moderate: `-1000.0` (10% on $10K, **current**)
   - Aggressive: `-2000.0` (20% on $10K)

---

## 🔍 Hidden/Hardcoded Rules

These rules are in the code but not easily configurable:

| Rule | Value | Location | Impact |
|------|-------|----------|--------|
| Leverage safety margin | 90% of max | `trading_bot.py:166` | Only uses 9x effective on 10x setting |
| Risk hard cap | 10% of balance | `trading_bot.py:178` | Overrides MAX_RISK_PER_TRADE if higher |
| Warning threshold | 80% of circuit breaker | `trading_bot.py:257` | Early warning before halt |
| Balance cache TTL | 60 seconds | `trading_bot.py:121` | Can't fetch more frequently |
| Signal check markets | First 5 only | `trading_bot.py:107` | Doesn't check all markets |
| Signal probability | 5% | `trading_bot.py:111, 113` | Test implementation |
| Main loop interval | 60 seconds | `trading_bot.py:77` | Update frequency |

---

## 📊 Summary Table

### Key Limits at a Glance
| Metric | Current Setting | Max Exposure |
|--------|----------------|--------------|
| **Positions** | 5 max | All capital |
| **Leverage** | 10x (9x effective) | 9x balance per position |
| **Per-Trade Risk** | 2% | $200 on $10K |
| **Circuit Breaker** | -$1,000 | 10% account loss |
| **Slippage** | 0.5% | Small impact per trade |

### Risk Assessment
**Current Configuration:** 🟡 **MODERATE RISK**
- Suitable for: Testnet trading, learning, initial testing
- Not suitable for: Production without real strategy

**To Reduce Risk:** ↓
- Decrease MAX_POSITIONS to 3
- Decrease DEFAULT_LEVERAGE to 5
- Decrease MAX_RISK_PER_TRADE to 0.01

**To Increase Risk:** ↑
- Increase MAX_POSITIONS to 10
- Increase DEFAULT_LEVERAGE to 20
- Increase MAX_RISK_PER_TRADE to 0.05
- **Also increase circuit breaker threshold proportionally**

---

## 🚨 Important Notes

1. **Random Trading Signals:** Current signals are **random (5% probability)** - NOT based on market analysis. This is for testing infrastructure only.

2. **Test Credentials:** The keys in `.env` are **placeholders**. Update with real testnet credentials to trade.

3. **No Stop Loss:** Orders don't have stop-loss or take-profit levels. All risk management is at the portfolio level via circuit breaker.

4. **First 5 Markets Only:** Bot only checks the first 5 markets returned by API, not all available markets.

5. **Fixed Quantity:** Test signals use fixed 1.0 quantity, not calculated based on balance or volatility.

---

## 📞 Need to Change Settings?

**Environment Variables** (`.env`):
- MAX_POSITIONS
- DEFAULT_LEVERAGE
- MAX_RISK_PER_TRADE
- ENABLE_AUTO_TRADING
- TESTNET

**Code Changes Required:**
- Circuit breaker threshold: `trading_bot.py:47`
- Slippage tolerance: `pacifica_client.py:242`
- Leverage safety margin: `trading_bot.py:166`
- Risk hard cap: `trading_bot.py:178`
- Trading strategy: Replace `trading_bot.py:103-116`

**After Changing:**
1. Restart the bot
2. Verify new settings: Check logs or API status endpoint
3. Test with small positions first

---

**Last Review:** January 9, 2026
**Status:** Safe for testnet with current conservative settings
**Action Required:** Replace random signals with real strategy before production
