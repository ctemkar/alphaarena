#!/usr/bin/env python3
"""
Overnight validation run of wide-SL V2 config (thr=0.03%, TP=10bps, SL=6bps).
This is the config that went +$5.13 in the May 4 wide-SL sweep.
Runs for 12h to accumulate enough trades for statistical confidence (target 20+).
Waits for PID 39020 (wide-SL sweep) to finish first.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

WATCH_PID = int(os.getenv("WATCH_PID", "39020"))
DURATION = int(os.getenv("ALPHA_OVERNIGHT_DURATION", str(12 * 3600)))  # 12h default
PORT = 8001

VARIANT = {
    "name": "OVERNIGHT-V2 (thr=0.03% TP=10 SL=6)",
    "port": PORT,
    "model": "Llama-3.2",
    "desk": "btc",
    "edge": 0.030,
    "persistence": 1,
    "reversal": 1.0,
    "size_usd": 5000,
    "force_close_on_hold": "1",
    "signal_chance": 1.0,
    "momentum_override": "0",
    "momentum_threshold": 0.030,
    "signal_strategy": "deterministic_reversal",
    "det_move_window": "20",
    "hold_cooldown_ticks": "6",
    "regime_filter": "0",
    "fee_bps": "0.5",
    "slippage_bps": "0",
    "hold_extend_ticks": "120",
    "tp_bps": "10",
    "sl_bps": "6",
    "reversal_require_recovery": "0",
}


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def wait_for_sweep(pid: int) -> None:
    print(f"Waiting for sweep PID {pid} to finish...")
    while pid_alive(pid):
        time.sleep(15)
    print("Sweep done. Starting overnight run.")


def build_env(v: dict) -> dict:
    env = os.environ.copy()
    env.update({
        "ALPHA_PORT": str(v["port"]),
        "ALPHA_MODEL_TAG": v["model"],
        "ALPHA_PAPER_MODE": "1",
        "ALPHA_AUTO_SELECT_ENABLED": "0",
        "ALPHA_MIN_TRADE_MOVE_PCT": "0.0",
        "ALPHA_MOMENTUM_OVERRIDE_THRESHOLD_PCT_BASKET": str(v["momentum_threshold"]),
        "ALPHA_MOMENTUM_OVERRIDE_THRESHOLD_PCT_BTC": str(v["momentum_threshold"]),
        "ALPHA_DIRECTIONAL_PERSISTENCE_MIN_STREAK_BASKET": str(v["persistence"]),
        "ALPHA_DIRECTIONAL_PERSISTENCE_MIN_STREAK_BTC": str(v["persistence"]),
        "ALPHA_REVERSAL_EDGE_MULTIPLIER_BASKET": str(v["reversal"]),
        "ALPHA_REVERSAL_EDGE_MULTIPLIER_BTC": str(v["reversal"]),
        "ALPHA_BASKET_ORDER_USD": str(v["size_usd"]),
        "ALPHA_BTC_ORDER_USD": str(v["size_usd"]),
        "ALPHA_LIVE_ORDER_USD": str(v["size_usd"]),
        "ALPHA_MAX_ORDER_USD": str(v["size_usd"]),
        "ALPHA_HARD_MAX_ORDER_USD": str(v["size_usd"]),
        "ALPHA_PAPER_HOLD_EXTEND_TICKS": str(v["hold_extend_ticks"]),
        "ALPHA_PAPER_TP_BPS": str(v["tp_bps"]),
        "ALPHA_PAPER_SL_BPS": str(v["sl_bps"]),
        "ALPHA_REVERSAL_REQUIRE_RECOVERY": str(v["reversal_require_recovery"]),
        "ALPHA_HOLD_STREAK_MOMENTUM_OVERRIDE_ENABLED": v["momentum_override"],
        "ALPHA_HOLD_STREAK_MOMENTUM_OVERRIDE_MIN_STREAK": "1",
        "ALPHA_PAPER_FORCE_CLOSE_ON_HOLD": v["force_close_on_hold"],
        "ALPHA_SIGNAL_STRATEGY": v["signal_strategy"],
        "ALPHA_DETERMINISTIC_MOMENTUM_MIN_MOVE_PCT": str(v["momentum_threshold"]),
        "ALPHA_DETERMINISTIC_REVERSAL_MIN_MOVE_PCT": str(v["momentum_threshold"]),
        "ALPHA_ANALYTICS_FEE_BPS": str(v["fee_bps"]),
        "ALPHA_ANALYTICS_SLIPPAGE_BPS": str(v["slippage_bps"]),
        "ALPHA_SIGNAL_REGIME_FILTER_ENABLED": str(v["regime_filter"]),
        "ALPHA_DET_MOVE_WINDOW": str(v["det_move_window"]),
        "ALPHA_HOLD_COOLDOWN_TICKS": str(v["hold_cooldown_ticks"]),
    })
    return env


def wait_for_server(port: int, timeout: int = 30) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://localhost:{port}/api/state", timeout=2)
            return True
        except Exception:
            time.sleep(1)
    return False


def main():
    import datetime

    # Wait for current sweep to finish
    if pid_alive(WATCH_PID):
        wait_for_sweep(WATCH_PID)
    else:
        print(f"PID {WATCH_PID} already done. Starting immediately.")

    # Kill any leftover servers
    os.system("pkill -f quantplot_ai_server.py 2>/dev/null; sleep 2")
    os.system(f"lsof -ti:{PORT} 2>/dev/null | xargs kill -9 2>/dev/null; sleep 1")

    v = VARIANT
    start_bkk = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    end_bkk = (datetime.datetime.now() + datetime.timedelta(seconds=DURATION)).strftime("%H:%M:%S")
    print("=" * 80)
    print(f"OVERNIGHT RUN: {v['name']}")
    print(f"Start: {start_bkk} Bangkok  |  Duration: {DURATION//3600}h  |  End: ~{end_bkk} Bangkok")
    print("=" * 80)

    env = build_env(v)
    proc = subprocess.Popen(
        ["python3", "quantplot_ai_server.py"],
        env=env,
        cwd=Path(__file__).parent,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print(f"Server started (pid {proc.pid}) on port {PORT}")

    if not wait_for_server(PORT, timeout=30):
        print("ERROR: server failed to start")
        proc.terminate()
        sys.exit(1)
    print("Server ready. Running harness...")

    harness_env = build_env(v)
    harness_env.update({
        "ALPHA_CONTROLLED_PORT": str(PORT),
        "ALPHA_CONTROLLED_DURATION_SECONDS": str(DURATION),
        "ALPHA_CONTROLLED_POLL_SECONDS": "30",
        "ALPHA_CONTROLLED_ENABLE_BTC": "1",
        "ALPHA_CONTROLLED_ENABLE_BASKET": "0",
        "ALPHA_CONTROLLED_BTC_MODEL": v["model"],
        "ALPHA_CONTROLLED_BASKET_MODEL": "",
    })

    log_path = f"/tmp/overnight_harness_{PORT}.log"
    timeout_s = DURATION + 900
    try:
        result = subprocess.run(
            ["python3", "run_controlled_paper_session.py"],
            env=harness_env,
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent,
            timeout=timeout_s,
        )
        output = (result.stdout or "") + (result.stderr or "")
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        err = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
        output = out + err + f"\n[TIMEOUT]\n"

    with open(log_path, "w") as f:
        f.write(output)

    proc.terminate()
    proc.wait(timeout=5)

    # Parse result
    delta = {}
    buf = []
    in_delta = False
    for line in output.splitlines():
        if line.strip().startswith("DELTA"):
            in_delta = True
        if in_delta:
            buf.append(line)
            if line.strip() == "}":
                break
    if buf:
        try:
            delta = json.loads("\n".join(buf).split("DELTA", 1)[1].strip())
        except Exception:
            pass

    net = delta.get("delta_net_pnl", 0.0)
    ex_fee = delta.get("delta_ex_fee_pnl", 0.0)
    trades = delta.get("delta_trades", 0)
    wins = delta.get("delta_wins", 0)
    losses = delta.get("delta_losses", 0)
    win_rate = (wins / trades * 100) if trades > 0 else 0.0
    fee_drag = ex_fee - net

    print("\n" + "=" * 80)
    print("OVERNIGHT RESULT")
    print("=" * 80)
    print(f"  net={net:+.4f}  ex_fee={ex_fee:+.4f}  fee_drag={fee_drag:.4f}")
    print(f"  trades={trades}  wins={wins}  losses={losses}  win_rate={win_rate:.1f}%")
    verdict = "PROFITABLE ✓" if net > 0 else "LOSS ✗"
    print(f"  verdict={verdict}")
    print("=" * 80)
    print(f"Full log: {log_path}")

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    summary = {
        "timestamp": ts,
        "duration_s": DURATION,
        "variant": v["name"],
        "net": net, "ex_fee": ex_fee, "fee_drag": fee_drag,
        "trades": trades, "wins": wins, "losses": losses, "win_rate_pct": win_rate,
    }
    out_path = f"/tmp/overnight_result_{ts}.json"
    with open(out_path, "w") as f:
        json.dump(summary, f, indent=2)
    with open("/tmp/overnight_latest.json", "w") as f:
        json.dump(summary, f, indent=2)
    print(f"Result saved → {out_path}")


if __name__ == "__main__":
    main()
