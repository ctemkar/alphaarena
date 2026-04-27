# Master Prompt: Rebuild Current QuantPlot System + US Stocks Day-Trading Variant

Use this prompt with a coding agent to recreate the current system behavior and extend it.

---

You are implementing a trading platform from an existing Python project.

## Objective A: Recreate Current Crypto System Exactly

Rebuild and run the existing dual-desk system with these characteristics:
- Backend server: quantplot_ai_server.py
- Dashboard: quantplot_ai.html
- Execution planner: execution_core.py
- API root: http://127.0.0.1:8000
- Desk model:
  - BTC desk (single selected model)
  - Basket desk (single selected model)
- Signal outputs: LONG, SHORT, HOLD
- Live futures execution against Binance USDT-M
- Runtime controls via API (select/deselect, pause, auto-select, order sizing)

Must preserve these behaviors:
1. Cross-desk duplicate prevention:
   - Same model must not occupy both desks concurrently, including IDLE-first-signal window.
2. HOLD replacement logic:
   - Configurable streak and interval behavior.
3. Executability precheck:
   - Enforce notional floors and free-balance requirements before queueing live orders.
4. Dashboard PnL rendering:
   - Show small values with adaptive precision so tiny non-zero values are visible.
5. Env precedence:
   - .env provides defaults; explicit exported env vars override ALPHA_* values.
6. Optional SSL fallback:
   - ALPHA_INSECURE_SSL=1 can enable insecure HTTPS context only when explicitly requested.

Runtime validation checklist:
- /api/state returns mode, feed, selected models, order_usd.
- Live logs show signal and order queue/fill events.
- Dashboard reflects desk PnL and model stats.

## Objective B: Add US Stocks Day-Trading Version

Create a parallel strategy mode for US equities with limited market hours.

Required capabilities:
1. Session gating:
   - Trade entries only during regular US session (09:30 to 16:00 ET).
   - Holidays and closed-session detection.
2. Day-trading flatten policy:
   - Auto-flatten all open positions before close cutoff.
   - Block new entries after cutoff window.
3. Broker abstraction:
   - Separate adapter from Binance-specific logic.
   - Support stock broker order APIs and buying power checks.
4. Risk controls:
   - Preserve daily loss limit and kill switch.
   - Add per-symbol notional and concentration caps.
5. Dashboard session panel:
   - Session open/closed badge, time to close, flatten status.
6. Rollout path:
   - Replay/backtest first, then paper mode, then limited live.

## Data and metrics requirements

For both crypto and stocks variants, output:
- net pnl
- win rate
- expectancy
- max drawdown
- skip/blocked rate
- fee and slippage assumptions

## Constraints and quality bar

- Keep existing public API routes stable where possible.
- Avoid regressing current dual-desk behavior.
- Add concise comments only where logic is non-obvious.
- Provide a runbook with exact start commands and troubleshooting steps.
- Include migration notes from current crypto-only mode to dual-market architecture.

## Deliverables

1. Updated code implementing both objectives.
2. Documentation file describing architecture and operating procedures.
3. A short test/validation script for smoke checks on mode, feed, desk selection, and order queue activity.
4. A tuning guide that explains how to balance speed vs profitability without causing churn.

Begin by scanning current files and summarizing concrete deltas before coding.
