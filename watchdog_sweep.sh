#!/usr/bin/env bash
# Watchdog for run_v2_sweep.py — run via crontab every 5 min.
# Crontab entry (run: crontab -e):
#   */5 * * * * /Users/chetantemkar/development/alphaarena/watchdog_sweep.sh

SCRIPT_DIR="/Users/chetantemkar/development/alphaarena"
PID_FILE="/tmp/overnight_v3c.pid"
LOG_FILE="/tmp/overnight_v3c_run.log"
WATCHDOG_LOG="/tmp/overnight_v3c_watchdog.log"

is_alive() {
    [[ -f "$PID_FILE" ]] || return 1
    local pid
    pid=$(cat "$PID_FILE")
    kill -0 "$pid" 2>/dev/null
}

# If result file exists the run finished normally — don't restart.
if [[ -f "/tmp/overnight_latest.json" ]]; then
    ts=$(python3 -c "import json; d=json.load(open('/tmp/overnight_latest.json')); print(d.get('timestamp',''))" 2>/dev/null)
    today=$(date '+%Y%m%d')
    if [[ "$ts" == "$today"* ]]; then
        echo "$(date '+%H:%M:%S') overnight run completed today ($ts) — watchdog idle." >> "$WATCHDOG_LOG"
        exit 0
    fi
fi

if is_alive; then
    echo "$(date '+%H:%M:%S') sweep running (pid=$(cat $PID_FILE)) — OK" >> "$WATCHDOG_LOG"
    exit 0
fi

echo "$(date '+%H:%M:%S') overnight not running — restarting..." >> "$WATCHDOG_LOG"
cd "$SCRIPT_DIR" || exit 1
nohup python3 -u run_overnight_v2.py >> "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"
echo "$(date '+%H:%M:%S') restarted with pid=$(cat $PID_FILE)" >> "$WATCHDOG_LOG"
