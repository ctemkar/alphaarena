---
mode: ask
description: "Run a risk-first profitability review from Alpha Arena logs/state and output approval-gated recommendations."
---
Review Alpha Arena runtime behavior using logs and current state.

Requirements:
1. Analyze risk and profitability together, not raw PnL alone.
2. Include reconciliation checks:
- Binance total PnL vs app total PnL
- desk PnL split behavior
- chart-vs-text consistency
3. Use evidence from logs/state for each finding.
4. Produce a ranked recommendation list with IDs (C1, C2, C3...), impact, and implementation risk.
5. Do not implement changes.
6. End with an explicit approval prompt in this exact format:
- `APPROVED CHANGES: <comma-separated IDs>`

Output sections:
1. Observations
2. Root causes and confidence
3. Recommended changes (ranked with IDs)
4. Validation plan
5. Approval prompt
