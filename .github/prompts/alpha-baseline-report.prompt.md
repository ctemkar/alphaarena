---
mode: ask
description: "Generate a standardized Alpha Arena baseline performance report before optimization work."
---
Create a baseline report for the selected Alpha Arena script.

Include:
1. Entry point and run mode
2. Dataset/time window and market assumptions
3. Fee and slippage assumptions
4. Core metrics:
   - net PnL
   - Sharpe/Sortino
   - max drawdown
   - hit rate
   - avg trade expectancy
   - turnover
   - latency/error rate (if available)
5. Top 5 suspected inefficiencies (compute + strategy)
6. Ranked experiment backlog (highest impact, lowest risk first)

If required context is missing, ask only the minimum clarifying questions.
