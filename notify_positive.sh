#!/usr/bin/env bash
# Polls sweep progress every 5 min (via cron).
# Sends a macOS notification + plays a sound when cumulative net P&L goes positive.
# Also fires when the final sweep summary is written.

LOG="/tmp/v2sweep_run.log"
SUMMARY="/tmp/v2sweep_latest.json"
NOTIFIED_FLAG="/tmp/v2sweep_notified_positive"
MONITOR_LOG="/tmp/v2sweep_monitor.log"

notify() {
    local title="$1" msg="$2"
    osascript -e "display notification \"$msg\" with title \"$title\" sound name \"Glass\""
    echo "$(date '+%H:%M:%S') NOTIFIED: $title — $msg" >> "$MONITOR_LOG"
}

# Already notified this run — don't spam
[[ -f "$NOTIFIED_FLAG" ]] && exit 0

# ── Check final summary first ─────────────────────────────────────────────────
if [[ -f "$SUMMARY" ]]; then
    best_net=$(python3 -c "
import json, sys
try:
    data = json.load(open('$SUMMARY'))
    nets = [r['net'] for r in data]
    best = max(nets)
    print(f'{best:.2f}')
except Exception as e:
    print('0')
" 2>/dev/null)
    if python3 -c "import sys; sys.exit(0 if float('${best_net:-0}') > 0 else 1)" 2>/dev/null; then
        notify "Alpha Arena — Sweep Complete ✅" "Best variant net=+\$${best_net}. Ready to review for live trading."
        touch "$NOTIFIED_FLAG"
        exit 0
    fi
fi

# ── Check live log for current cumulative net ─────────────────────────────────
if [[ ! -f "$LOG" ]]; then
    echo "$(date '+%H:%M:%S') log not found" >> "$MONITOR_LOG"
    exit 0
fi

# Extract the last CUM net= line from the log
last_net=$(grep -oP "CUM net=\K[+-]?\d+\.\d+" "$LOG" 2>/dev/null | tail -1)
if [[ -z "$last_net" ]]; then
    echo "$(date '+%H:%M:%S') no net data yet" >> "$MONITOR_LOG"
    exit 0
fi

echo "$(date '+%H:%M:%S') current cumulative net=$last_net" >> "$MONITOR_LOG"

if python3 -c "import sys; sys.exit(0 if float('${last_net}') > 0 else 1)" 2>/dev/null; then
    notify "Alpha Arena — Paper Trading +ve 🟢" "Cumulative net=+\$${last_net} — sweep still running."
    # Don't set NOTIFIED_FLAG here so we re-alert if it stays positive at sweep end
fi
