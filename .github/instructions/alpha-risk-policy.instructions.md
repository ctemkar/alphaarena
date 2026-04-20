---
description: "Use when editing or running Alpha Arena Python strategy files to enforce consistent risk limits and safety checks. Keywords: risk policy, drawdown cap, position size, daily loss limit, kill switch, stop trading."
applyTo: "**/*arena*.py"
---
Apply these defaults unless the user explicitly overrides them:

- Hard realized loss guardrail: $100 per trading day. If breached, stop opening new risk.
- Keep existing kill switches, stop logic, and exposure caps enabled.
- Any strategy change must report before/after metrics with the same assumptions:
  - net PnL
  - Sharpe or Sortino
  - max drawdown
  - hit rate and expectancy
  - slippage and fee assumptions
- Prefer safe rollout order: replay/backtest -> paper/testnet -> limited live.
- If live mode is requested, require explicit confirmation that safety checks are active.

When uncertain, choose the safer option and call out trade-offs.
