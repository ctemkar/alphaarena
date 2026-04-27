# Master Prompt: Global Day Trading System (Standalone, No Crypto)

Use this prompt with any coding agent to build a professional day-trading platform for traditional financial markets. This prompt is intentionally asset-agnostic and contains no crypto-specific requirements.

---

You are building a complete day-trading platform for global markets.

## Scope

Design and implement a production-ready day-trading system that supports equities, ETFs, index products, and futures (where broker/API access allows), with strict intraday risk controls and no overnight exposure by default.

The platform must be geographically portable and usable from any country, with exchange/session behavior controlled by configuration.

## Core Objectives

1. Build a reliable signal-to-execution pipeline for intraday trading.
2. Enforce strong risk management at strategy, symbol, and account levels.
3. Provide clear operator visibility through a real-time dashboard.
4. Support paper trading, simulation, and controlled live rollout.
5. Keep broker and exchange dependencies abstract so the system is portable.

## Functional Requirements

### A) Market Session Engine (Global)

Implement a configurable market-session module:
- Support multiple exchanges and time zones.
- Handle open, close, pre-open, lunch breaks (where relevant), and holidays.
- Gate entries based on session state.
- Provide a configurable intraday flatten rule before session close.
- Block new entries after configurable cutoff time.
- Permit optional overnight mode only when explicitly enabled.

### B) Broker Abstraction Layer

Create a broker interface with adapters behind it:
- Account summary, buying power, and margin checks.
- Positions and open orders retrieval.
- Place, modify, and cancel orders.
- Order status tracking and reconciliation.
- Common order model independent of broker-specific payloads.

### C) Signal and Strategy Framework

Design a modular strategy engine:
- Multi-strategy support (momentum, mean reversion, breakout, volatility regimes).
- Configurable symbol universe and liquidity filters.
- Feature computation pipeline (price/volume/volatility/microstructure features as available).
- Optional model-routing layer where multiple models vote or rank opportunities.
- Deterministic fallback behavior when model output is delayed or missing.

### D) Risk and Compliance Controls

Implement hard risk controls:
- Max daily loss limit.
- Max per-trade risk and max per-symbol exposure.
- Max portfolio concentration.
- Max concurrent positions.
- Dynamic position sizing based on volatility and account risk budget.
- Kill switch for immediate halt of new entries.
- Circuit breakers for repeated execution failures or data outages.

### E) Execution Quality

Execution requirements:
- Slippage-aware order selection logic.
- Spread and liquidity checks before entry.
- Time-in-force policies by venue/instrument.
- Retry with bounded policy and idempotency protection.
- Post-trade attribution: expected vs actual fill quality.

### F) Real-Time Dashboard

Build an operator dashboard with:
- Session status for each active exchange.
- Account status and risk utilization.
- Open positions and pending orders.
- Intraday PnL (realized/unrealized/fees/slippage).
- Strategy health and signal latency metrics.
- Alert feed for rejects, risk blocks, and data feed degradation.

### G) Data and Reliability

Data pipeline must include:
- Primary + fallback market data feeds (if available).
- Freshness checks and stale-data protection.
- Heartbeats and health endpoints.
- Structured logs and event timelines.
- Recovery workflow after process restart.

## Non-Functional Requirements

1. Maintainability:
- Clean module boundaries.
- Typed config models and validation.
- Testable interfaces.

2. Safety:
- Sensitive secrets via environment or secret manager.
- No hardcoded credentials.
- Explicit live-trading confirmation gate.

3. Portability:
- No hardcoded country assumptions.
- Time zone/exchange calendar fully configurable.
- Broker adapter swap with minimal upstream changes.

## Metrics and Success Criteria

Track and report at minimum:
- Net intraday PnL.
- Win rate.
- Expectancy per trade.
- Max intraday drawdown.
- Fill quality and slippage.
- Rejection/skip rates with reason breakdown.
- Signal-to-order latency.

Define acceptance thresholds in config so deployments can fail fast if quality drops below target.

## Deliverables

1. Source code for the full trading platform.
2. Configuration templates for multi-region operation.
3. Operator runbook with start/stop/recovery steps.
4. Risk policy document.
5. Smoke test suite for session gating, risk blocks, order lifecycle, and data failover.
6. Backtest/paper-trade evaluation report template.

## Rollout Plan

Implement and validate in this order:
1. Offline simulation and replay testing.
2. Paper trading in real-time market hours.
3. Limited live rollout with tight risk caps.
4. Scale exposure only after stable expectancy and drawdown compliance.

## Important Constraints

- Do not include digital-asset-specific assumptions.
- Do not rely on one exchange or one country.
- Keep the architecture suitable for institutional-style operational controls.

Start by generating a short architecture proposal, then implement modules incrementally with tests and runtime validation checkpoints.