# QuantPlot AI System Rebuild Guide (Current State + US Stocks Extension)

## 1) Current Live Diagnosis (As Of This Snapshot)

The backend is reachable, but trading appears dead because execution is blocked by exchange auth, not because desks are empty.

Observed state:
- mode: LIVE_BLOCKED
- feed: LIVE
- live_blocked: true
- live_blocked_reason: Binance order auth failure: Binance HTTP 401 code -2015 (invalid API key, IP, or permissions)
- order_usd: 50.0
- auto_select_enabled: false
- selected BTC desk model: Mistral
- selected Basket desk model: DeepSeek-R1

Implication:
- Signals continue to compute and HOLD/LONG/SHORT logs appear.
- New live orders do not execute until Binance auth/permissions/IP whitelist are corrected.

---

## 2) What This System Is

QuantPlot AI is a dual-desk live trading server + dashboard.

Core behavior:
- Desk A: BTC-only strategy desk.
- Desk B: Basket strategy desk (BTC/ETH/SOL/BNB universe).
- AI models emit directional signals (LONG/SHORT/HOLD).
- Execution path routes to Binance USDT-M futures.
- Auto-select can rank/swap models by ongoing desk performance.
- Dashboard shows desk PnL, total PnL, model chips, logs, and control buttons.

Primary backend file:
- quantplot_ai_server.py

Primary dashboard file:
- quantplot_ai.html

Execution planner:
- execution_core.py

---

## 3) Important Runtime Rules Currently In Place

Selection and desk logic:
- Cross-desk duplicate selection has been hardened so the same model is not allowed to occupy both desks in the IDLE race window.
- HOLD replacement threshold can be configured (currently tuned via ALPHA_HOLD_REPLACE_STREAK).

Order/execution logic:
- Runtime order size can be changed with POST /api/order-size.
- Executability checks enforce notional floors and free-balance checks before queueing orders.
- Cooldowns prevent duplicate side spam and symbol over-trading.

UI/PnL visibility:
- Small PnL values are shown with adaptive precision in the dashboard so tiny non-zero values do not look like zero.

Env precedence:
- .env now acts as defaults for ALPHA_* values.
- Explicit exported env vars at launch override .env.

SSL fallback:
- Optional ALPHA_INSECURE_SSL=1 fallback exists for environments with self-signed corporate TLS interception.
- This is recovery-only and should be replaced by proper trust chain setup.

---

## 4) Critical Config Variables

Connectivity and mode:
- ALPHA_LIVE_TRADING
- ALPHA_PAPER_MODE
- ALPHA_USE_FUTURES
- ALPHA_REQUIRE_LIVE_FEED
- ALPHA_INSECURE_SSL

Credentials used by backend:
- BINANCE_KEY / BINANCE_SECRET (or EXCH_BINANCE_API_KEY / EXCH_BINANCE_API_SECRET)
- OPENROUTER_API_KEY

Sizing and pace:
- ALPHA_LIVE_ORDER_USD
- ALPHA_MAX_ORDER_USD
- ALPHA_MIN_FREE_USDT_BUFFER
- ALPHA_BASE_SIGNAL_CHANCE
- ALPHA_MIN_TRADE_MOVE_PCT
- ALPHA_MOMENTUM_OVERRIDE_THRESHOLD_PCT

Cooldowns and anti-churn:
- ALPHA_LIVE_DUPLICATE_COOLDOWN_SECONDS
- ALPHA_LIVE_SYMBOL_COOLDOWN_SECONDS
- ALPHA_HOLD_REPLACE_STREAK
- ALPHA_SKIP_SELECTED_HOLD_ON_SIGNAL

Auto-select:
- ALPHA_AUTO_SELECT_ENABLED
- ALPHA_AUTO_SELECT_TOP_N
- ALPHA_AUTO_SELECT_INTERVAL_TICKS
- ALPHA_BLOCK_CROSS_DESK_SELECT_ON_HOLD

---

## 5) Recreate Exact System State (Behaviorally)

1. Start server from repo root with python3 quantplot_ai_server.py.
2. Ensure dashboard at http://127.0.0.1:8000 loads.
3. Confirm API health at /api/state.
4. Apply runtime profile close to current operating state:
   - order_usd around 50
   - auto_select disabled for pinned mode, or enabled when doing model rotation tests
   - duplicate/symbol cooldowns active
5. Pin desks to:
   - BTC: Mistral
   - Basket: DeepSeek-R1
6. Validate logs show signal activity.
7. If mode enters LIVE_BLOCKED with 401/-2015, fix exchange credentials/permissions/IP whitelist.

---

## 6) Why Desks Can Look Dead

Most common causes in this system:
1. LIVE_BLOCKED due to exchange auth failure (current issue).
2. Paper mode accidentally enabled after reboot (ALPHA_PAPER_MODE=1 or ALPHA_LIVE_TRADING=0).
3. Free margin below required notional after sizing increase.
4. Over-tight thresholds/cooldowns suppressing entries.
5. Auto-select disabled with no models selected on one or both desks.

---

## 7) Operational Troubleshooting Checklist

Quick checks:
- GET /api/state
  - mode, feed, live_blocked_reason
  - selected models per desk
  - order_usd, cooldowns
- Inspect top logs for:
  - LIVE SKIPPED
  - LIVE FEED LOST
  - Binance HTTP 401/-2015
  - model selected/rotating events

Git ignore note:
- If runtime JSON files still get committed after adding to .gitignore, they are already tracked.
- Run git rm --cached <file> once to untrack them, then ignore works.

---

## 8) US Stocks Day-Trading Extension (Design Blueprint)

Goal:
- Build a parallel dual-desk system for US equities with market-hours constraints.

Market-hours model:
- Only open new positions during regular session window (09:30 to 16:00 ET).
- Optional pre/post market policy flags.
- Mandatory end-of-day policy:
  - flat-all by configurable cutoff (for pure day-trading)
  - no overnight holds unless explicitly enabled.

Required components:
1. Broker adapter layer
   - Replace Binance adapter with stock broker API abstraction.
   - Support order placement, account buying power, position snapshots.

2. Session manager
   - US/Eastern calendar with holiday support.
   - Entry gating by session state.
   - Forced flatten before close.

3. Universe and risk model
   - Equity universe selection (liquid symbols, spread filters).
   - Per-symbol and per-desk notional caps.
   - Daily loss guardrail and kill switch preserved.

4. Signal engine reuse
   - Keep AI model scoring/desk framework.
   - Adapt prompt context from crypto basket to stock watchlist and intraday features.

5. Execution constraints
   - Equity-specific order types and TIF semantics.
   - Slippage and spread-aware sizing.

6. Dashboard updates
   - Session status badge (open/closed/holiday).
   - Time-to-close indicator.
   - Day-trade compliance panel and flatten countdown.

7. Backtest and paper rollout
   - Intraday replay with same strategy assumptions.
   - Paper-trading burn-in.
   - Limited live deployment once hit-rate/expectancy and drawdown are acceptable.

---

## 9) Minimum Acceptance Criteria For "Profitable" Direction

Track these continuously:
- Net PnL
- Win rate
- Expectancy per trade
- Max drawdown
- Fee + slippage burden
- Skip rate and blocked-order rate

A/B protocol:
- Compare pinned 2-model mode vs auto-select mode in equal windows and same market regime.
- Use identical sizing/cooldowns during comparison.

---

## 10) Immediate Next Steps

1. Fix Binance credentials and IP/permissions to clear LIVE_BLOCKED.
2. Re-run 10-20 minute live observation with current pinned pair and order_usd 50.
3. Only then tune cadence or model rotation policy based on measured expectancy.
4. Start US-stock extension in paper mode with strict session gating first.
