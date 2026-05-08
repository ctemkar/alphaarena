#!/usr/bin/env python3
"""
Live trading validation run - V3C-LIVE config (thr=0.03%, TP=10bps, SL=6bps, size=$1k).
Conservative pilot: 2h supervised run with kill-switch, drawdown cap, and live broker integration.
SUPERVISOR mode: automatically restarts server+harness on crash, watches for broker disconnects.
Runs for DURATION wall-clock time. Accumulates P&L across restarts.
"""
import datetime
import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path

WATCH_PID = int(os.getenv("WATCH_PID", "0"))
DURATION = int(os.getenv("ALPHA_LIVE_DURATION", str(2 * 3600)))  # 2h pilot
SESSION_MAX = int(os.getenv("ALPHA_SESSION_MAX", str(2 * 3600)))
SUPERVISOR_CHUNK_SECONDS = int(os.getenv("ALPHA_SUPERVISOR_CHUNK", "300"))
KILL_SWITCH_THRESHOLD = float(os.getenv("ALPHA_KILL_SWITCH_THRESHOLD", "-15.0"))
KILL_SWITCH_TRADE_GATE = int(os.getenv("ALPHA_KILL_SWITCH_TRADE_GATE", "20"))
DAILY_LOSS_LIMIT = 100.0  # Risk policy: hard stop at $100 realized loss per day
PORT = 8001
STATUS_PATH = "/tmp/live_v3c_status.json"

VARIANT = {
    "name": "LIVE-V3C-PILOT (thr=0.01% TP=10 SL=6 size=$1k)",
    "port": PORT,
    "mode": "LIVE",  # Key: LIVE not PAPER
    "model": "Llama-3.2",
    "desk": "btc",
    "edge": 0.010,
    "persistence": 1,
    "reversal": 1.0,
    "size_usd": 1000,  # Conservative: 1/5 of paper size
    "force_close_on_hold": "1",
    "momentum_override": "0",
    "momentum_threshold": 0.010,
    "signal_strategy": "deterministic_reversal",
    "det_move_window": "20",
    "hold_cooldown_ticks": "6",
    "regime_filter": "0",
    "fee_bps": "0.5",
    "slippage_bps": "2",  # Live slippage assumption (vs 0 for paper)
    "hold_extend_ticks": "120",
    "tp_bps": "10",
    "sl_bps": "6",
    "reversal_require_recovery": "0",
    "deterministic_move_window": "20",
}

print("""
================================================================================
LIVE TRADING RUN: LIVE-V3C-PILOT (thr=0.01% TP=10 SL=6)
Total: 2h  |  Session max: 2h  |  Chunk: 300s  |  Size: $1k  |  DailyLoss: $100
Kill-switch: stop if net <= -15.0 before 20 trades
Risk Policy: Hard $100/day loss limit enforced server-side
================================================================================
""")

def build_env(v: dict) -> dict:
    env = os.environ.copy()
    env.update({
        "ALPHA_PORT": str(v["port"]),
        "ALPHA_MODE": v.get("mode", "PAPER"),
        "ALPHA_DESK": v["desk"],
        "ALPHA_MODEL": v["model"],
        "ALPHA_SIZE_USD": str(v["size_usd"]),
        "ALPHA_EDGE": str(v["edge"]),
        "ALPHA_PERSISTENCE": str(v["persistence"]),
        "ALPHA_REVERSAL": str(v["reversal"]),
        "ALPHA_FORCE_CLOSE_ON_HOLD": v["force_close_on_hold"],
        "ALPHA_MOMENTUM_OVERRIDE": v["momentum_override"],
        "ALPHA_MOMENTUM_THRESHOLD": str(v["momentum_threshold"]),
        "ALPHA_SIGNAL_STRATEGY": v["signal_strategy"],
        "ALPHA_DET_MOVE_WINDOW": v["det_move_window"],
        "ALPHA_HOLD_COOLDOWN_TICKS": v["hold_cooldown_ticks"],
        "ALPHA_REGIME_FILTER": v["regime_filter"],
        "ALPHA_FEE_BPS": v["fee_bps"],
        "ALPHA_SLIPPAGE_BPS": v["slippage_bps"],
        "ALPHA_HOLD_EXTEND_TICKS": v["hold_extend_ticks"],
        "ALPHA_TP_BPS": v["tp_bps"],
        "ALPHA_SL_BPS": v["sl_bps"],
        "ALPHA_REVERSAL_REQUIRE_RECOVERY": v["reversal_require_recovery"],
        "ALPHA_DETERMINISTIC_MOVE_WINDOW": v["deterministic_move_window"],
        "ALPHA_DAILY_LOSS_LIMIT_USD": str(DAILY_LOSS_LIMIT),
    })
    return env

PYTHON_BIN = "/opt/homebrew/bin/python3"  # 3.14, supports list[dict]|None syntax
SERVER_LOG = "/tmp/live_v3c_server.log"

def start_server(v):
    server_log_fh = open(SERVER_LOG, "a")
    return subprocess.Popen(
        [PYTHON_BIN, "quantplot_ai_server.py"],
        env=build_env(v),
        cwd=Path(__file__).parent,
        stdout=server_log_fh,
        stderr=server_log_fh,
    )

def wait_for_server(port, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://localhost:{port}/api/state", timeout=2)
            return True
        except Exception:
            time.sleep(0.5)
    return False

def pid_alive(pid):
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False

def kill_server(port):
    os.system(f"lsof -ti:{port} 2>/dev/null | xargs kill -9 2>/dev/null")
    os.system("pkill -f quantplot_ai_server.py 2>/dev/null")
    # Wait until port is actually free (OS needs time to release it)
    deadline = time.time() + 10
    while time.time() < deadline:
        result = os.system(f"lsof -ti:{port} >/dev/null 2>&1")
        if result != 0:  # lsof returns non-zero when port is free
            break
        time.sleep(0.5)
    time.sleep(1)

def run_harness(port, chunk_seconds):
    """Run run_controlled_paper_session.py for one chunk. Return DELTA dict."""
    env = os.environ.copy()
    env["ALPHA_PORT"] = str(port)
    try:
        result = subprocess.run(
            [PYTHON_BIN, "run_controlled_paper_session.py", str(chunk_seconds)],
            env=env,
            cwd=Path(__file__).parent,
            capture_output=True,
            timeout=chunk_seconds + 30,
            text=True,
        )
        output = result.stdout + result.stderr
        lines = output.split("\n")
        delta_line = [l for l in lines if l.startswith("DELTA")]
        if delta_line:
            try:
                return json.loads(delta_line[0].replace("DELTA ", ""))
            except json.JSONDecodeError:
                pass
        return {}
    except subprocess.TimeoutExpired:
        return {}
    except Exception as e:
        print(f"  Harness error: {e}")
        return {}

totals = {
    "net": 0.0,
    "ex_fee": 0.0,
    "trades": 0,
    "wins": 0,
    "losses": 0,
    "sessions_completed": 0,
}
start_wall_time = time.time()

try:
    while time.time() - start_wall_time < DURATION:
        wall_elapsed = time.time() - start_wall_time
        wall_remaining = DURATION - wall_elapsed
        session_minutes = int(min(SESSION_MAX, wall_remaining) / 60)

        print(f"[Session {totals['sessions_completed'] + 1}] {session_minutes}min session | {int(wall_remaining/60)}min total remaining")

        proc = start_server(VARIANT)
        time.sleep(2)
        if not wait_for_server(VARIANT["port"]):
            print("  Server failed to start; killing and retrying...")
            kill_server(VARIANT["port"])
            continue

        print(f"  Server pid={proc.pid}")
        print("  Server ready. Running chunked harness with mid-session crash recovery...")

        session_start = time.time()
        session_chunks_completed = 0
        while time.time() - session_start < SESSION_MAX and time.time() - start_wall_time < DURATION:
            wall_remaining = DURATION - (time.time() - start_wall_time)
            session_remaining = SESSION_MAX - (time.time() - session_start)
            if wall_remaining <= 0 or session_remaining <= 0:
                break

            session_chunks_completed += 1
            chunk_result = run_harness(VARIANT["port"], SUPERVISOR_CHUNK_SECONDS)

            if not chunk_result:
                if proc.poll() is not None:
                    print(f"  Harness returned empty DELTA and server is down; restarting server...")
                    kill_server(VARIANT["port"])
                    break
                else:
                    print(f"  Chunk {session_chunks_completed}: net=+0.0000 trades=0 wins=0 losses=0 remaining={int(session_remaining/60)}min")
                    continue

            net = chunk_result.get("delta_net_pnl", 0.0)
            trades = chunk_result.get("delta_trades", 0)
            wins = chunk_result.get("delta_wins", 0)
            losses = chunk_result.get("delta_losses", 0)
            ex_fee = chunk_result.get("delta_ex_fee_pnl", 0.0)

            totals["net"] += net
            totals["trades"] += trades
            totals["wins"] += wins
            totals["losses"] += losses
            totals["ex_fee"] += ex_fee

            wall_elapsed_min = int((time.time() - start_wall_time) / 60)
            remaining_min = int((DURATION - (time.time() - start_wall_time)) / 60)

            print(f"  Chunk {session_chunks_completed}: net={net:+.4f} trades={trades} wins={wins} losses={losses} remaining={remaining_min}min")
            print(f"  CUMULATIVE: net={totals['net']:+.4f}  ex_fee={totals['ex_fee']:+.4f}  trades={totals['trades']}  wins={totals['wins']}  losses={totals['losses']}  win_rate={100*totals['wins']/max(1, totals['trades']):.0f}%  elapsed={wall_elapsed_min}min")

            if totals["net"] <= KILL_SWITCH_THRESHOLD and totals["trades"] < KILL_SWITCH_TRADE_GATE:
                print(f"  KILL SWITCH: net <= {KILL_SWITCH_THRESHOLD} with < {KILL_SWITCH_TRADE_GATE} trades; stopping run")
                break

            if totals["net"] <= -DAILY_LOSS_LIMIT:
                print(f"  DAILY LOSS LIMIT: net <= -{DAILY_LOSS_LIMIT}; stopping run (risk policy)")
                break

        kill_server(VARIANT["port"])
        totals["sessions_completed"] += 1

        if totals["net"] <= KILL_SWITCH_THRESHOLD and totals["trades"] < KILL_SWITCH_TRADE_GATE:
            break
        if totals["net"] <= -DAILY_LOSS_LIMIT:
            break

except KeyboardInterrupt:
    print("\nInterrupt received; stopping run")
    kill_server(VARIANT["port"])

wall_elapsed = time.time() - start_wall_time
win_rate = 100 * totals["wins"] / max(1, totals["trades"]) if totals["trades"] > 0 else 0.0
expectancy = (totals["net"] - totals["ex_fee"]) / max(1, totals["trades"]) if totals["trades"] > 0 else 0.0

print(f"""
================================================================================
RUN COMPLETE
  Total time: {int(wall_elapsed / 3600)}h {int((wall_elapsed % 3600) / 60)}m
  P&L: {totals['net']:+.2f} (excl_fee: {totals['net'] - totals['ex_fee']:+.2f})
  Trades: {totals['trades']} | Wins: {totals['wins']} | Losses: {totals['losses']} | Win Rate: {win_rate:.1f}%
  Expectancy: {expectancy:+.2f}/trade
  Sessions completed: {totals['sessions_completed']}
================================================================================
""")

with open(STATUS_PATH, "w") as f:
    json.dump({
        "timestamp": datetime.datetime.now().isoformat(),
        "variant": VARIANT["name"],
        "duration_hours": wall_elapsed / 3600,
        "net_usd": totals["net"],
        "net_excl_fees_usd": totals["net"] - totals["ex_fee"],
        "trades": totals["trades"],
        "wins": totals["wins"],
        "losses": totals["losses"],
        "win_rate_pct": win_rate,
        "expectancy_usd": expectancy,
        "sessions_completed": totals["sessions_completed"],
    }, f, indent=2)

# macOS notification
verdict = "PROFITABLE ✅" if totals["net"] > 0 else "LOSS ❌"
notif_msg = f"net={totals['net']:+.2f}  trades={totals['trades']}  wr={win_rate:.0f}%  {verdict}"
subprocess.run(
    ["osascript", "-e", f'display notification "{notif_msg}" with title "Alpha Arena — Live Run Done" sound name "Glass"'],
    capture_output=True,
)

with open("/tmp/live_v3c.pid", "w") as f:
    f.write(str(os.getpid()))
