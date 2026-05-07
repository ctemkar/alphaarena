#!/usr/bin/env bash
# Watchdog for run_v2_sweep.py — run via crontab every 5 min.
# Crontab entry (run: crontab -e):
#   */5 * * * * /Users/chetantemkar/development/alphaarena/watchdog_sweep.sh

SCRIPT_DIR="/Users/chetantemkar/development/alphaarena"
PID_FILE="/tmp/v2sweep.pid"
LOG_FILE="/tmp/v2sweep_run.log"
WATCHDOG_LOG="/tmp/v2sweep_watchdog.log"

is_alive() {
    [[ -f "$PID_FILE" ]] || return 1
    local pid
    pid=$(cat "$PID_FILE")
    kill -0 "$pid" 2>/dev/null
}

# If a result file already exists the sweep finished normally — don't restart.
if [[ -f "/tmp/v2sweep_latest.json" ]]; then
    echo "$(date '+%H:%M:%S') sweep already completed — watchdog idle." >> "$WATCHDOG_LOG"
    exit 0
fi

if is_alive; then
    echo "$(date '+%H:%M:%S') sweep running (pid=$(cat $PID_FILE)) — OK" >> "$WATCHDOG_LOG"
    exit 0
fi

echo "$(date '+%H:%M:%S') sweep not running — restarting..." >> "$WATCHDOG_LOG"
cd "$SCRIPT_DIR" || exit 1
nohup python3 -u run_v2_sweep.py >> "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"
echo "$(date '+%H:%M:%S') restarted with pid=$(cat $PID_FILE)" >> "$WATCHDOG_LOG"
