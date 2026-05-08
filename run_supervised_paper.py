#!/usr/bin/env python3
"""
Supervised paper trading run - V3C config (thr=0.05%, TP=10bps, SL=6bps).
SUPERVISOR mode: automatically restarts server+harness on crash.
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
DURATION = int(os.getenv("ALPHA_OVERNIGHT_DURATION", str(2 * 3600)))  # 2h validation
SESSION_MAX = int(os.getenv("ALPHA_SESSION_MAX", str(2 * 3600)))
SUPERVISOR_CHUNK_SECONDS = int(os.getenv("ALPHA_SUPERVISOR_CHUNK", "300"))
KILL_SWITCH_THRESHOLD = float(os.getenv("ALPHA_KILL_SWITCH_THRESHOLD", "-15.0"))
KILL_SWITCH_TRADE_GATE = int(os.getenv("ALPHA_KILL_SWITCH_TRADE_GATE", "20"))
PORT = 8001
STATUS_PATH = "/tmp/supervised_paper_status.json"

VARIANT = {
    "name": "QUICK-V3C-LO (thr=0.03% TP=10 SL=6)",
    "port": PORT,
    "model": "Llama-3.2",
    "desk": "btc",
    "edge": 0.030,
    "persistence": 1,
    "reversal": 1.0,
    "size_usd": 5000,
    "force_close_on_hold": "1",
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


SERVER_LOG = "/tmp/supervised_server.log"
PYTHON_BIN = "/opt/homebrew/bin/python3"  # 3.14, supports list[dict]|None syntax

def start_server(v):
    server_log_fh = open(SERVER_LOG, "a")
    return subprocess.Popen(
        [PYTHON_BIN, "quantplot_ai_server.py"],
        env=build_env(v),
        cwd=Path(__file__).parent,
        stdout=server_log_fh,
        stderr=server_log_fh,
    )


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


def wait_for_server(port, timeout=40):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://localhost:{port}/api/state", timeout=2)
            return True
        except Exception:
            time.sleep(1)
    return False


def run_session(v, session_dur, session_num):
    env = build_env(v)
    env.update({
        "ALPHA_CONTROLLED_PORT": str(v["port"]),
        "ALPHA_CONTROLLED_DURATION_SECONDS": str(session_dur),
        "ALPHA_CONTROLLED_POLL_SECONDS": "30",
        "ALPHA_CONTROLLED_ENABLE_BTC": "1",
        "ALPHA_CONTROLLED_ENABLE_BASKET": "0",
        "ALPHA_CONTROLLED_BTC_MODEL": v["model"],
        "ALPHA_CONTROLLED_BASKET_MODEL": "",
    })
    session_log = f"/tmp/overnight_session_{session_num}.log"
    try:
        result = subprocess.run(
            ["python3", "run_controlled_paper_session.py"],
            env=env,
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent,
            timeout=session_dur + 300,
        )
        output = (result.stdout or "") + (result.stderr or "")
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        err = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
        output = out + err + "\n[SESSION TIMEOUT]\n"
    except Exception as ex:
        output = f"[SESSION ERROR] {ex}\n"

    with open(session_log, "w") as f:
        f.write(output)

    # Try DELTA block first
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
            return json.loads("\n".join(buf).split("DELTA", 1)[1].strip())
        except Exception:
            pass

    # Fallback: last net= poll line (covers crash before DELTA is written)
    last = {}
    for line in output.splitlines():
        if "net=" in line and "trades=" in line:
            try:
                nm = re.search(r"net=([+-]?\d+\.\d+)", line)
                em = re.search(r"ex_fee=([+-]?\d+\.\d+)", line)
                tm = re.search(r"trades=(\d+)", line)
                wm = re.search(r"wins=(\d+)", line)
                lm = re.search(r"losses=(\d+)", line)
                if nm:
                    last = {
                        "delta_net_pnl": float(nm.group(1)),
                        "delta_ex_fee_pnl": float(em.group(1)) if em else 0.0,
                        "delta_trades": int(tm.group(1)) if tm else 0,
                        "delta_wins": int(wm.group(1)) if wm else 0,
                        "delta_losses": int(lm.group(1)) if lm else 0,
                    }
            except Exception:
                pass
    return last


def save_status(totals, session, wall_elapsed, wall_total):
    trades = totals["trades"]
    win_rate = (totals["wins"] / trades * 100) if trades > 0 else 0.0
    status = {
        "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "session": session,
        "wall_elapsed_min": wall_elapsed // 60,
        "wall_remaining_min": max(0, (wall_total - wall_elapsed) // 60),
        "net": round(totals["net"], 4),
        "ex_fee": round(totals["ex_fee"], 4),
        "trades": trades,
        "wins": totals["wins"],
        "losses": totals["losses"],
        "win_rate_pct": round(win_rate, 1),
    }
    with open(STATUS_PATH, "w") as f:
        json.dump(status, f, indent=2)
    print(f"  CUMULATIVE: net={totals['net']:+.4f}  ex_fee={totals['ex_fee']:+.4f}  "
          f"trades={trades}  wins={totals['wins']}  losses={totals['losses']}  "
          f"win_rate={win_rate:.1f}%  elapsed={wall_elapsed//60}min")


def main():
    if WATCH_PID > 0 and pid_alive(WATCH_PID):
        print(f"Waiting for PID {WATCH_PID} to finish...")
        while pid_alive(WATCH_PID):
            time.sleep(15)

    v = VARIANT
    wall_start = time.time()
    wall_end = wall_start + DURATION
    session = 0
    totals = {"net": 0.0, "ex_fee": 0.0, "trades": 0, "wins": 0, "losses": 0}
    crashes = 0

    end_dt = datetime.datetime.now() + datetime.timedelta(seconds=DURATION)
    print("=" * 80)
    print(f"SUPERVISED PAPER RUN: {v['name']}")
    print(f"Total: {DURATION//3600}h  |  Session max: {SESSION_MAX//3600}h  |  Chunk: {SUPERVISOR_CHUNK_SECONDS}s  |  End: ~{end_dt.strftime('%H:%M')} Bangkok")
    print(f"Kill-switch: stop if net <= {KILL_SWITCH_THRESHOLD:.1f} before {KILL_SWITCH_TRADE_GATE} trades")
    print("=" * 80)

    kill_server(PORT)
    kill_switch_fired = False

    while time.time() < wall_end and not kill_switch_fired:
        remaining = int(wall_end - time.time())
        if remaining < 60:
            break

        session_dur = min(SESSION_MAX, remaining)
        session += 1
        print(f"\n[Session {session}] {session_dur//60}min session | {remaining//60}min total remaining")

        proc = start_server(v)
        print(f"  Server pid={proc.pid}")

        if not wait_for_server(PORT, timeout=40):
            print("  FAILED to start server - retrying in 30s...")
            proc.terminate()
            kill_server(PORT)
            crashes += 1
            time.sleep(30)
            continue

        print("  Server ready. Running chunked harness with mid-session crash recovery...")

        session_remaining = session_dur
        chunk_index = 0
        session_net = 0.0
        session_ex_fee = 0.0
        session_trades = 0
        session_wins = 0
        session_losses = 0

        while session_remaining > 0 and time.time() < wall_end:
            chunk_index += 1
            wall_remaining = int(wall_end - time.time())
            chunk_dur = min(SUPERVISOR_CHUNK_SECONDS, session_remaining, wall_remaining)
            if chunk_dur < 30:
                break

            # If the server died while the harness wasn't running, restart immediately.
            if proc.poll() is not None:
                crashes += 1
                print("  Server died mid-session; restarting now...")
                kill_server(PORT)
                proc = start_server(v)
                if not wait_for_server(PORT, timeout=40):
                    print("  Restart failed; retrying in 10s...")
                    crashes += 1
                    time.sleep(10)
                    continue

            delta = run_session(v, chunk_dur, f"{session}_{chunk_index}")

            net = delta.get("delta_net_pnl", 0.0)
            ex_fee = delta.get("delta_ex_fee_pnl", 0.0)
            trades = delta.get("delta_trades", 0)
            wins = delta.get("delta_wins", 0)
            losses = delta.get("delta_losses", 0)

            session_net += net
            session_ex_fee += ex_fee
            session_trades += trades
            session_wins += wins
            session_losses += losses

            totals["net"] += net
            totals["ex_fee"] += ex_fee
            totals["trades"] += trades
            totals["wins"] += wins
            totals["losses"] += losses

            session_remaining -= chunk_dur

            print(
                f"  Chunk {chunk_index}: net={net:+.4f} trades={trades} wins={wins} losses={losses} "
                f"remaining={max(0, session_remaining)//60}min"
            )

            # Empty delta and dead server means likely crash before DELTA flush; recover now.
            if not delta and proc.poll() is not None:
                crashes += 1
                print("  Harness returned empty DELTA and server is down; restarting server...")
                kill_server(PORT)
                proc = start_server(v)
                if wait_for_server(PORT, timeout=40):
                    print("  Server restart successful.")
                else:
                    print("  Server restart failed; will retry on next chunk.")
                    crashes += 1

            save_status(totals, session, int(time.time() - wall_start), DURATION)

            # Check kill-switch: if net <= threshold and trades < gate, stop early
            if totals["trades"] < KILL_SWITCH_TRADE_GATE and totals["net"] <= KILL_SWITCH_THRESHOLD:
                print(
                    f"\n[KILL-SWITCH] Net {totals['net']:+.4f} <= {KILL_SWITCH_THRESHOLD:.1f} threshold "
                    f"with only {totals['trades']} trades (< {KILL_SWITCH_TRADE_GATE} gate). "
                    f"Stopping early to conserve overnight time."
                )
                kill_switch_fired = True
                session_remaining = 0
                break

        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        kill_server(PORT)

        print(
            f"  Session {session} result: net={session_net:+.4f}  trades={session_trades}  "
            f"wins={session_wins}  losses={session_losses}"
        )

        if time.time() < wall_end - 10:
            time.sleep(5)

    trades = totals["trades"]
    win_rate = (totals["wins"] / trades * 100) if trades > 0 else 0.0
    fee_drag = totals["ex_fee"] - totals["net"]
    stopped_early = trades < KILL_SWITCH_TRADE_GATE and totals["net"] <= KILL_SWITCH_THRESHOLD
    print("\n" + "=" * 80)
    print("OVERNIGHT FINAL RESULT")
    print("=" * 80)
    if stopped_early:
        print("[EARLY STOP via kill-switch]")
    print(f"  net={totals['net']:+.4f}  ex_fee={totals['ex_fee']:+.4f}  fee_drag={fee_drag:.4f}")
    print(f"  trades={trades}  wins={totals['wins']}  losses={totals['losses']}  win_rate={win_rate:.1f}%")
    print(f"  sessions={session}  crashes={crashes}")
    print(f"  verdict={'PROFITABLE' if totals['net'] > 0 else 'LOSS (below threshold)' if stopped_early else 'LOSS'}")
    print("=" * 80)

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    summary = {
        "timestamp": ts, "duration_s": DURATION, "variant": v["name"],
        "sessions": session, "crashes": crashes,
        "net": totals["net"], "ex_fee": totals["ex_fee"], "fee_drag": fee_drag,
        "trades": trades, "wins": totals["wins"], "losses": totals["losses"],
        "win_rate_pct": round(win_rate, 1),
    }
    out_path = f"/tmp/overnight_result_{ts}.json"
    for path in [out_path, "/tmp/overnight_latest.json"]:
        with open(path, "w") as f:
            json.dump(summary, f, indent=2)
    print(f"Result saved -> {out_path}")

    # macOS notification
    verdict = "PROFITABLE ✅" if totals["net"] > 0 else "LOSS ❌"
    notif_msg = f"net={totals['net']:+.2f}  trades={trades}  wr={win_rate:.0f}%  {verdict}"
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{notif_msg}" with title "Alpha Arena — Overnight Done" sound name "Glass"'],
            timeout=5, capture_output=True,
        )
    except Exception:
        pass


if __name__ == "__main__":
    # Write PID file so watchdog can check liveness
    with open("/tmp/supervised_paper.pid", "w") as _pf:
        _pf.write(str(os.getpid()))
    main()
