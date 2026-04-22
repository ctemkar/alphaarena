#!/bin/zsh
set -euo pipefail

BASE_DIR="/Users/chetantemkar/development/alphaarena"
LOG_DIR="$BASE_DIR/logs"
LOG_FILE="$LOG_DIR/quantplot_ai_server.log"
PID_FILE="$LOG_DIR/quantplot_ai_server.pid"

mkdir -p "$LOG_DIR"
cd "$BASE_DIR"

# If something is already listening on port 8000, do not start a duplicate process.
if lsof -ti:8000 >/dev/null 2>&1; then
  exit 0
fi

nohup /opt/homebrew/bin/python3 quantplot_ai_server.py >> "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"
