#!/usr/bin/env python3
"""
Wide-SL sweep — fix the SL-too-tight problem from V3-fix sweep (May 3-4).

INSIGHT from V3-fix sweep:
  - V2 (thr=0.03%, TP=5bps, SL=2.5bps): ex_fee=+$5.27 POSITIVE — direction right.
    0 wins, 2 losses. SL=2.5bps too tight — noise stops out before reversal plays out.
  - V1 (thr=0.03%, TP=4bps, SL=2bps): same problem, worse.
  - V3/V4 (thr=0.04-0.05%): 0 trades — BTC range too small.

FIX STRATEGY: Keep thr=0.03% (generates trades), widen SL dramatically.
  - SL 2.5 → 5-8bps to survive noise before reversal.
  - Keep TP > SL for positive expected value.
  - R:R = 1.5:1 minimum (e.g. TP=8, SL=5 or TP=10, SL=6).
  - Wider hold window so position stays open long enough to hit TP.

All maker fees (0.5bps/leg = 1bps rt).
"""
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

VARIANTS = [
    # V1 — thr=0.03%, TP=8bps SL=5bps (1.6:1 R:R)
    # SL doubled vs best V3-fix. Enough room to survive 1-2 tick noise.
    {
        "name": "VARIANT-V1 (wide-SL thr=0.03% TP=8 SL=5)",
        "port": 8001,
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
        "hold_extend_ticks": "100",
        "tp_bps": "8",
        "sl_bps": "5",
        "reversal_require_recovery": "0",
    },
    # V2 — thr=0.03%, TP=10bps SL=6bps (1.67:1 R:R)
    # Even wider. Based on May 2 V2 winner (TP=6, SL=4) but more room.
    {
        "name": "VARIANT-V2 (wide-SL thr=0.03% TP=10 SL=6)",
        "port": 8002,
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
    },
    # V3 — thr=0.03%, TP=6bps SL=4bps (1.5:1 R:R)
    # Mirrors May 2 V2 (the only ever profitable run). Repeat with current market.
    {
        "name": "VARIANT-V3 (wide-SL thr=0.03% TP=6 SL=4)",
        "port": 8003,
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
        "hold_extend_ticks": "80",
        "tp_bps": "6",
        "sl_bps": "4",
        "reversal_require_recovery": "0",
    },
    # V4 — thr=0.03%, TP=12bps SL=8bps (1.5:1 R:R), no forced close
    # Maximum room. Lets reversal fully play out before closing.
    {
        "name": "VARIANT-V4 (wide-SL thr=0.03% TP=12 SL=8)",
        "port": 8004,
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
        "hold_extend_ticks": "150",
        "tp_bps": "12",
        "sl_bps": "8",
        "reversal_require_recovery": "0",
    },
]

DURATION = int(os.getenv("ALPHA_PARALLEL_DURATION", "7200"))


def build_env(v: dict) -> dict:
    env = os.environ.copy()
    env.update({
        "ALPHA_PORT": str(v["port"]),
        "ALPHA_MODEL_TAG": v["model"],
        "ALPHA_PAPER_MODE": "1",
        "ALPHA_AUTO_SELECT_ENABLED": "0",
        "ALPHA_MIN_TRADE_MOVE_PCT": "0.0",
        "ALPHA_MOMENTUM_OVERRIDE_THRESHOLD_PCT_BASKET": str(v.get("momentum_threshold", v["edge"])),
        "ALPHA_MOMENTUM_OVERRIDE_THRESHOLD_PCT_BTC": str(v.get("momentum_threshold", v["edge"])),
        "ALPHA_DIRECTIONAL_PERSISTENCE_MIN_STREAK_BASKET": str(v["persistence"]),
        "ALPHA_DIRECTIONAL_PERSISTENCE_MIN_STREAK_BTC": str(v["persistence"]),
        "ALPHA_REVERSAL_EDGE_MULTIPLIER_BASKET": str(v["reversal"]),
        "ALPHA_REVERSAL_EDGE_MULTIPLIER_BTC": str(v["reversal"]),
        "ALPHA_BASKET_ORDER_USD": str(v["size_usd"]),
        "ALPHA_BTC_ORDER_USD": str(v["size_usd"]),
        "ALPHA_LIVE_ORDER_USD": str(v["size_usd"]),
        "ALPHA_MAX_ORDER_USD": str(v["size_usd"]),
        "ALPHA_HARD_MAX_ORDER_USD": str(v["size_usd"]),
        "ALPHA_PAPER_HOLD_EXTEND_TICKS": str(v.get("hold_extend_ticks", "0")),
        "ALPHA_PAPER_TP_BPS": str(v.get("tp_bps", "0")),
        "ALPHA_PAPER_SL_BPS": str(v.get("sl_bps", "0")),
        "ALPHA_REVERSAL_REQUIRE_RECOVERY": str(v.get("reversal_require_recovery", "0")),
        "ALPHA_HOLD_STREAK_MOMENTUM_OVERRIDE_ENABLED": v.get("momentum_override", "1"),
        "ALPHA_HOLD_STREAK_MOMENTUM_OVERRIDE_MIN_STREAK": "1" if v.get("signal_strategy", "").startswith("deterministic") else "3",
        "ALPHA_PAPER_FORCE_CLOSE_ON_HOLD": v.get("force_close_on_hold", "1"),
        "ALPHA_SIGNAL_STRATEGY": v.get("signal_strategy", ""),
        "ALPHA_DETERMINISTIC_MOMENTUM_MIN_MOVE_PCT": str(v.get("momentum_threshold", v["edge"])),
        "ALPHA_DETERMINISTIC_REVERSAL_MIN_MOVE_PCT": str(v.get("momentum_threshold", v["edge"])),
        "ALPHA_ANALYTICS_FEE_BPS": str(v.get("fee_bps", "0.5")),
        "ALPHA_ANALYTICS_SLIPPAGE_BPS": str(v.get("slippage_bps", "0")),
        "ALPHA_SIGNAL_REGIME_FILTER_ENABLED": str(v.get("regime_filter", "0")),
        "ALPHA_DET_MOVE_WINDOW": str(v.get("det_move_window", "20")),
        "ALPHA_HOLD_COOLDOWN_TICKS": str(v.get("hold_cooldown_ticks", "6")),
    })
    return env


def start_server(v: dict) -> subprocess.Popen:
    env = build_env(v)
    proc = subprocess.Popen(
        ["python3", "quantplot_ai_server.py"],
        env=env,
        cwd=Path(__file__).parent,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print(f"  Started {v['name']} on port {v['port']} (pid {proc.pid})")
    return proc


def wait_for_server(port: int, timeout: int = 30) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://localhost:{port}/api/state", timeout=2)
            print(f"  ✓ port {port} ready")
            return True
        except Exception:
            time.sleep(1)
    print(f"  ✗ port {port} not ready after {timeout}s")
    return False


def run_harness(v: dict, result_store: dict) -> None:
    env = build_env(v)
    desk = v.get("desk", "basket")
    env.update({
        "ALPHA_CONTROLLED_PORT": str(v["port"]),
        "ALPHA_CONTROLLED_DURATION_SECONDS": str(DURATION),
        "ALPHA_CONTROLLED_POLL_SECONDS": "15",
        "ALPHA_CONTROLLED_ENABLE_BTC": "1" if desk == "btc" else "0",
        "ALPHA_CONTROLLED_ENABLE_BASKET": "0" if desk == "btc" else "1",
        "ALPHA_CONTROLLED_BTC_MODEL": v["model"] if desk == "btc" else "",
        "ALPHA_CONTROLLED_BASKET_MODEL": v["model"] if desk != "btc" else "",
    })
    log_path = f"/tmp/alpha_harness_{v['port']}.log"
    print(f"  [{v['name']}] harness started → {log_path}")
    timeout_slack = max(600, int(DURATION * 0.20))
    timeout_s = DURATION + timeout_slack
    status = "ok"
    try:
        proc = subprocess.run(
            ["python3", "run_controlled_paper_session.py"],
            env=env,
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent,
            timeout=timeout_s,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired as e:
        status = f"timeout({timeout_s}s)"
        out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        err = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
        output = out + err + f"\n[TIMEOUT] harness exceeded {timeout_s}s\n"
    except Exception as e:
        status = f"error({type(e).__name__})"
        output = f"[ERROR] harness failed: {e}\n"

    with open(log_path, "w") as f:
        f.write(output)
    delta = {}
    in_delta = False
    buf = []
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
    result_store[v["name"]] = {"delta": delta, "raw": output, "status": status}
    print(f"  [{v['name']}] harness done ({status})")


def print_results(results: dict) -> None:
    import datetime
    print("\n" + "=" * 80)
    print("FINAL RESULTS")
    print("=" * 80)
    ranked = []
    for name, r in results.items():
        d = r.get("delta", {})
        ranked.append({
            "name": name,
            "status": r.get("status", "ok"),
            "net": d.get("delta_net_pnl", 0.0),
            "ex_fee": d.get("delta_ex_fee_pnl", 0.0),
            "trades": d.get("delta_trades", 0),
            "wins": d.get("delta_wins", 0),
            "losses": d.get("delta_losses", 0),
        })
    ranked.sort(key=lambda x: x["net"], reverse=True)
    for i, r in enumerate(ranked):
        fee_drag = r["ex_fee"] - r["net"]
        win_rate = (r["wins"] / r["trades"] * 100) if r["trades"] > 0 else 0.0
        label = "WINNER" if i == 0 else f"#{i+1}"
        print(f"\n[{label}] {r['name']}")
        print(f"  status={r['status']}")
        print(f"  net={r['net']:+.4f}  ex_fee={r['ex_fee']:+.4f}  fee_drag={fee_drag:.4f}")
        print(f"  trades={r['trades']}  wins={r['wins']}  losses={r['losses']}  win_rate={win_rate:.1f}%")
    print("\n" + "=" * 80)
    print("WIDE-SL SWEEP COMPLETE")
    print("=" * 80)

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    summary_path = f"/tmp/sweep_result_{ts}.json"
    with open(summary_path, "w") as f:
        json.dump({"timestamp": ts, "duration_s": DURATION, "ranked": ranked}, f, indent=2)
    with open("/tmp/sweep_latest_result.json", "w") as f:
        json.dump({"timestamp": ts, "duration_s": DURATION, "ranked": ranked}, f, indent=2)
    print(f"\nResults saved → {summary_path}")


def main():
    total = len(VARIANTS)
    print("=" * 80)
    print(f"WIDE-SL SEQUENTIAL SWEEP  ({total} variants × {DURATION}s each = {total * DURATION // 60} min total)")
    print("=" * 80)

    os.system("pkill -f quantplot_ai_server.py 2>/dev/null; sleep 1")

    results = {}
    for i, v in enumerate(VARIANTS, 1):
        print(f"\n[{i}/{total}] Starting {v['name']} on port {v['port']}...")
        os.system(f"lsof -ti:{v['port']} 2>/dev/null | xargs kill -9 2>/dev/null; sleep 1")
        proc = start_server(v)
        if not wait_for_server(v["port"]):
            print(f"  ✗ Server failed to start. Skipping.")
            proc.terminate()
            results[v["name"]] = {"delta": {}, "raw": "", "status": "server_failed"}
            continue
        print(f"  ✓ Server up. Running {DURATION}s harness...")
        try:
            run_harness(v, results)
        finally:
            proc.terminate()
            proc.wait(timeout=5)
            os.system(f"lsof -ti:{v['port']} 2>/dev/null | xargs kill -9 2>/dev/null")
            print(f"  Server stopped.")

    print_results(results)


if __name__ == "__main__":
    main()
