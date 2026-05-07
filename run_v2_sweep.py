#!/usr/bin/env python3
"""
V2 comparison sweep: 3 variants x 4h each = 12h total.
  A: V2-repeat   thr=0.03%  TP=10  SL=6  (baseline repeat)
  B: V2-wideTP   thr=0.03%  TP=15  SL=6  (wider TP, better payoff ratio)
  C: V2-tightTHR thr=0.05%  TP=10  SL=6  (tighter entry, fewer noise trades)
Uses same supervisor/kill-switch/crash-recovery pattern as run_overnight_v2.py.
"""
import datetime
import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path

VARIANT_DURATION = int(os.getenv("ALPHA_VARIANT_DURATION", str(4 * 3600)))
SESSION_MAX = int(os.getenv("ALPHA_SESSION_MAX", str(2 * 3600)))
CHUNK = int(os.getenv("ALPHA_SUPERVISOR_CHUNK", "300"))
KILL_THRESHOLD = float(os.getenv("ALPHA_KILL_SWITCH_THRESHOLD", "-15.0"))
KILL_GATE = int(os.getenv("ALPHA_KILL_SWITCH_TRADE_GATE", "15"))
PORT = 8001

BASE = {
    "port": PORT,
    "model": "Llama-3.2",
    "persistence": 1,
    "reversal": 1.0,
    "size_usd": 5000,
    "force_close_on_hold": "1",
    "momentum_override": "0",
    "signal_strategy": "deterministic_reversal",
    "det_move_window": "20",
    "hold_cooldown_ticks": "6",
    "regime_filter": "0",
    "fee_bps": "0.5",
    "slippage_bps": "0",
    "hold_extend_ticks": "120",
    "reversal_require_recovery": "0",
}

VARIANTS = [
    {**BASE, "name": "A: V2-repeat   (thr=0.03% TP=10 SL=6)", "momentum_threshold": 0.030, "tp_bps": "10", "sl_bps": "6"},
    {**BASE, "name": "B: V2-wideTP   (thr=0.03% TP=15 SL=6)", "momentum_threshold": 0.030, "tp_bps": "15", "sl_bps": "6"},
    {**BASE, "name": "C: V2-tightTHR (thr=0.05% TP=10 SL=6)", "momentum_threshold": 0.050, "tp_bps": "10", "sl_bps": "6"},
]


def pid_alive(pid):
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def build_env(v):
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


def kill_server(port):
    os.system(f"lsof -ti:{port} 2>/dev/null | xargs kill -9 2>/dev/null")
    os.system("pkill -f quantplot_ai_server.py 2>/dev/null")
    time.sleep(2)


def start_server(v):
    return subprocess.Popen(
        ["python3", "quantplot_ai_server.py"],
        env=build_env(v),
        cwd=Path(__file__).parent,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_for_server(port, timeout=40):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://localhost:{port}/api/state", timeout=2)
            return True
        except Exception:
            time.sleep(1)
    return False


def run_chunk(v, chunk_dur, label):
    env = build_env(v)
    env.update({
        "ALPHA_CONTROLLED_PORT": str(v["port"]),
        "ALPHA_CONTROLLED_DURATION_SECONDS": str(chunk_dur),
        "ALPHA_CONTROLLED_POLL_SECONDS": "30",
        "ALPHA_CONTROLLED_ENABLE_BTC": "1",
        "ALPHA_CONTROLLED_ENABLE_BASKET": "0",
        "ALPHA_CONTROLLED_BTC_MODEL": v["model"],
        "ALPHA_CONTROLLED_BASKET_MODEL": "",
    })
    chunk_log = f"/tmp/v2sweep_{label}.log"
    try:
        result = subprocess.run(
            ["python3", "run_controlled_paper_session.py"],
            env=env,
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent,
            timeout=chunk_dur + 300,
        )
        output = (result.stdout or "") + (result.stderr or "")
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        err = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
        output = out + err + "\n[TIMEOUT]\n"
    except Exception as ex:
        output = f"[ERROR] {ex}\n"

    with open(chunk_log, "w") as f:
        f.write(output)

    # Try DELTA block first
    buf, in_delta = [], False
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

    # Fallback: last net= poll line
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


def run_variant(v, vi):
    """Run a single variant for VARIANT_DURATION with supervisor loop. Returns result dict."""
    wall_start = time.time()
    wall_end = wall_start + VARIANT_DURATION
    session = 0
    totals = {"net": 0.0, "ex_fee": 0.0, "trades": 0, "wins": 0, "losses": 0}
    crashes = 0
    kill_switch_fired = False

    end_dt = datetime.datetime.now() + datetime.timedelta(seconds=VARIANT_DURATION)
    print(f"\n{'='*80}")
    print(f"VARIANT {v['name']}")
    print(f"Duration: {VARIANT_DURATION//3600}h  |  End: ~{end_dt.strftime('%H:%M')} Bangkok")
    print(f"Kill-switch: net <= {KILL_THRESHOLD:.1f} before {KILL_GATE} trades")
    print("="*80)

    kill_server(PORT)

    while time.time() < wall_end and not kill_switch_fired:
        remaining = int(wall_end - time.time())
        if remaining < 60:
            break
        session_dur = min(SESSION_MAX, remaining)
        session += 1
        print(f"\n  [Session {session}] {session_dur//60}min | {remaining//60}min remaining")

        proc = start_server(v)
        print(f"  Server pid={proc.pid}")
        if not wait_for_server(PORT, timeout=40):
            print("  Server failed to start — retrying in 30s...")
            proc.terminate()
            kill_server(PORT)
            crashes += 1
            time.sleep(30)
            continue

        print("  Server ready.")
        session_remaining = session_dur
        chunk_index = 0

        while session_remaining > 0 and time.time() < wall_end and not kill_switch_fired:
            chunk_index += 1
            wall_rem = int(wall_end - time.time())
            chunk_dur = min(CHUNK, session_remaining, wall_rem)
            if chunk_dur < 30:
                break

            if proc.poll() is not None:
                crashes += 1
                print("  Server died mid-session; restarting...")
                kill_server(PORT)
                proc = start_server(v)
                if not wait_for_server(PORT, timeout=40):
                    crashes += 1
                    time.sleep(10)
                    continue

            delta = run_chunk(v, chunk_dur, f"v{vi}_s{session}_c{chunk_index}")
            net = delta.get("delta_net_pnl", 0.0)
            ex_fee = delta.get("delta_ex_fee_pnl", 0.0)
            trades = delta.get("delta_trades", 0)
            wins = delta.get("delta_wins", 0)
            losses = delta.get("delta_losses", 0)

            totals["net"] += net
            totals["ex_fee"] += ex_fee
            totals["trades"] += trades
            totals["wins"] += wins
            totals["losses"] += losses
            session_remaining -= chunk_dur

            wr = (totals["wins"] / totals["trades"] * 100) if totals["trades"] else 0
            elapsed = int(time.time() - wall_start)
            print(
                f"  Chunk {chunk_index}: net={net:+.4f} trades={trades} | "
                f"CUM net={totals['net']:+.4f} trades={totals['trades']} "
                f"wr={wr:.0f}% elapsed={elapsed//60}min"
            )

            if not delta and proc.poll() is not None:
                crashes += 1
                print("  Empty DELTA + dead server; restarting...")
                kill_server(PORT)
                proc = start_server(v)
                if wait_for_server(PORT, timeout=40):
                    print("  Restart OK.")
                else:
                    crashes += 1

            # Kill-switch
            if totals["trades"] < KILL_GATE and totals["net"] <= KILL_THRESHOLD:
                print(
                    f"  [KILL-SWITCH] net={totals['net']:+.4f} <= {KILL_THRESHOLD} "
                    f"with {totals['trades']} trades. Stopping variant."
                )
                kill_switch_fired = True
                break

        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        kill_server(PORT)

        if time.time() < wall_end - 10 and not kill_switch_fired:
            time.sleep(5)

    trades = totals["trades"]
    win_rate = (totals["wins"] / trades * 100) if trades else 0.0
    fee_drag = totals["ex_fee"] - totals["net"]
    print(
        f"\n  VARIANT RESULT: net={totals['net']:+.4f}  ex_fee={totals['ex_fee']:+.4f}  "
        f"fee_drag={fee_drag:.4f}  trades={trades}  wins={totals['wins']}  "
        f"losses={totals['losses']}  wr={win_rate:.1f}%  sessions={session}  "
        f"crashes={crashes}  kill_switch={'YES' if kill_switch_fired else 'no'}"
    )

    return {
        "name": v["name"],
        "tp_bps": v["tp_bps"],
        "sl_bps": v["sl_bps"],
        "momentum_threshold": v["momentum_threshold"],
        "net": totals["net"],
        "ex_fee": totals["ex_fee"],
        "fee_drag": fee_drag,
        "trades": trades,
        "wins": totals["wins"],
        "losses": totals["losses"],
        "win_rate_pct": round(win_rate, 1),
        "sessions": session,
        "crashes": crashes,
        "kill_switch_fired": kill_switch_fired,
    }


def main():
    total_variants = len(VARIANTS)
    total_dur_h = total_variants * VARIANT_DURATION / 3600
    end_dt = datetime.datetime.now() + datetime.timedelta(seconds=total_variants * VARIANT_DURATION)
    print("=" * 80)
    print("V2 COMPARISON SWEEP")
    print(f"  {total_variants} variants x {VARIANT_DURATION//3600}h each = {total_dur_h:.0f}h total")
    print(f"  End: ~{end_dt.strftime('%H:%M')} Bangkok")
    print("=" * 80)

    results = []
    for vi, v in enumerate(VARIANTS, 1):
        result = run_variant(v, vi)
        results.append(result)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = f"/tmp/v2sweep_{v['name'][:1]}_{ts}.json"
        with open(path, "w") as f:
            json.dump(result, f, indent=2)
        print(f"  Saved -> {path}")

    # Final comparison table
    print("\n" + "=" * 80)
    print("SWEEP FINAL COMPARISON")
    print("=" * 80)
    print(f"  {'Variant':<38} {'net':>8} {'trades':>7} {'wr%':>6} {'fee_drag':>10} {'kill?':>6}")
    print("  " + "-" * 77)
    for r in results:
        ks = "YES" if r["kill_switch_fired"] else "no"
        print(
            f"  {r['name']:<38} {r['net']:>+8.4f} {r['trades']:>7} "
            f"{r['win_rate_pct']:>6.1f} {r['fee_drag']:>10.4f} {ks:>6}"
        )
    print("=" * 80)

    best = max(results, key=lambda x: x["net"])
    print(f"\n  BEST: {best['name']}  net={best['net']:+.4f}")

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    summary_path = f"/tmp/v2sweep_summary_{ts}.json"
    with open(summary_path, "w") as f:
        json.dump(results, f, indent=2)
    with open("/tmp/v2sweep_latest.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"  Summary -> {summary_path}")


if __name__ == "__main__":
    # Write PID file so watchdog can check liveness
    with open("/tmp/v2sweep.pid", "w") as _pf:
        _pf.write(str(os.getpid()))
    main()
