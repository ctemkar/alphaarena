#!/usr/bin/env python3
"""
V3-fix sweep - fix May 2 V3's fee drag problem.
INSIGHT from history:
  - May 2 V3 (reversal, thr=0.03%, TP=2bps SL=1.5bps, 42 trades): ex_fee=+$2.23 POSITIVE.
    Signal direction was correct. Problem: $10 fee drag from 42 trades killed it.
  - May 2 V2 (reversal, thr=0.08%, TP=6bps SL=4bps, 2 trades): net=+$0.86. Only winner.
FIX STRATEGY: Keep V3's low threshold (more signals) but raise TP so each winner earns more.
  - Raise TP: 2bps → 4-5bps. Each win earns 4-5x more than fees cost.
  - Tighten SL slightly: 1.5bps → 2bps. Better R:R.
  - Try mid threshold 0.04-0.05% to reduce churn vs pure 0.03%.
All maker fees (0.5bps/leg = 1bps rt).
"""
import json
import os
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

# V3-FIX SWEEP — same low-threshold reversal signal, higher TP to overcome fee drag
VARIANTS = [
    # V1 — thr=0.03% (exact V3 threshold), TP=4bps SL=2bps (2:1 R:R)
    # Doubles the TP vs original V3 (was 2bps). Same entry frequency, more per winner.
    {
        "name": "VARIANT-V1 (V3fix thr=0.03% TP=4 SL=2)",
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
        "hold_extend_ticks": "60",
        "tp_bps": "4",
        "sl_bps": "2",
        "reversal_require_recovery": "0",
    },
    # V2 — thr=0.03%, TP=5bps SL=2.5bps (2:1 R:R)
    # Even wider TP. Needs less win rate to be profitable (breakeven ~25% at 2:1).
    {
        "name": "VARIANT-V2 (V3fix thr=0.03% TP=5 SL=2.5)",
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
        "hold_extend_ticks": "80",
        "tp_bps": "5",
        "sl_bps": "2.5",
        "reversal_require_recovery": "0",
    },
    # V3 — thr=0.04% (slightly selective), TP=4bps SL=2bps (2:1 R:R)
    # Fewer entries than 0.03% but each should be a cleaner extreme. Same wide TP.
    {
        "name": "VARIANT-V3 (V3fix thr=0.04% TP=4 SL=2)",
        "port": 8003,
        "model": "Llama-3.2",
        "desk": "btc",
        "edge": 0.040,
        "persistence": 1,
        "reversal": 1.0,
        "size_usd": 5000,
        "force_close_on_hold": "1",
        "signal_chance": 1.0,
        "momentum_override": "0",
        "momentum_threshold": 0.040,
        "signal_strategy": "deterministic_reversal",
        "det_move_window": "20",
        "hold_cooldown_ticks": "8",
        "regime_filter": "0",
        "fee_bps": "0.5",
        "slippage_bps": "0",
        "hold_extend_ticks": "60",
        "tp_bps": "4",
        "sl_bps": "2",
        "reversal_require_recovery": "0",
    },
    # V4 — thr=0.05% (mid threshold), TP=6bps SL=3bps (2:1 R:R)
    # Bridges May 2 V2 (0.08%) and V3 (0.03%). Should get more trades than V2 but
    # cleaner entries than V3. Same TP/SL ratio as the May 2 overall winner.
    {
        "name": "VARIANT-V4 (V3fix thr=0.05% TP=6 SL=3)",
        "port": 8004,
        "model": "Llama-3.2",
        "desk": "btc",
        "edge": 0.050,
        "persistence": 1,
        "reversal": 1.0,
        "size_usd": 5000,
        "force_close_on_hold": "1",
        "signal_chance": 1.0,
        "momentum_override": "0",
        "momentum_threshold": 0.050,
        "signal_strategy": "deterministic_reversal",
        "det_move_window": "20",
        "hold_cooldown_ticks": "10",
        "regime_filter": "0",
        "fee_bps": "0.5",
        "slippage_bps": "0",
        "hold_extend_ticks": "80",
        "tp_bps": "6",
        "sl_bps": "3",
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
        "ALPHA_DETERMINISTIC_CONFIRMED_MIN_MOVE_PCT": str(v.get("det_confirmed_min_move_pct", v.get("momentum_threshold", v["edge"]))),
        "ALPHA_DETERMINISTIC_CONFIRMED_MIN_TICKS": str(v.get("det_confirmed_min_ticks", 3)),
        "ALPHA_DETERMINISTIC_MOVE_WINDOW": str(v.get("det_move_window", "20")),
        "ALPHA_DISABLE_BASKET_TIMEOUT_FALLBACK": "1",
        "ALPHA_PAPER_RISK_OFF_MAX_DRAWDOWN_PCT": "0.20",
        "ALPHA_REVERSAL_REGIME_FILTER_ENABLED": v.get("regime_filter", "1"),
        "ALPHA_HOLD_COOLDOWN_TICKS": str(v.get("hold_cooldown_ticks", "2" if v.get("signal_strategy", "").startswith("deterministic") else "12")),
    })
    return env


def start_server(v: dict):
    env = build_env(v)
    proc = subprocess.Popen(
        ["python3", "quantplot_ai_server.py"],
        env=env,
        stdout=open(f"/tmp/alpha_server_{v['port']}.log", "w"),
        stderr=subprocess.STDOUT,
        cwd=Path(__file__).parent,
    )
    print(f"  Started {v['name']} on port {v['port']} (pid {proc.pid})")
    return proc


def wait_for_server(port: int, max_retries: int = 120) -> bool:
    url = f"http://127.0.0.1:{port}/api/state"
    for i in range(max_retries):
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status == 200:
                    print(f"  ✓ port {port} ready")
                    return True
        except Exception:
            pass
        time.sleep(1)
    print(f"  ✗ port {port} timed out after {max_retries}s")
    return False


def run_harness(v: dict, results: dict):
    log = f"/tmp/alpha_harness_{v['port']}.log"
    harness = Path(__file__).parent / "run_controlled_paper_session.py"
    env = build_env(v)
    env["ALPHA_SESSION_DURATION"] = str(DURATION)
    print(f"  ✓ Server up. Running {DURATION}s harness...")
    print(f"  [{v['name']}] harness started → {log}")
    try:
        subprocess.run(
            ["python3", str(harness), "--port", str(v["port"]), "--duration", str(DURATION)],
            env=env,
            stdout=open(log, "w"),
            stderr=subprocess.STDOUT,
            timeout=DURATION + 120,
        )
        status = "ok"
    except subprocess.TimeoutExpired:
        status = "timeout"
    print(f"  [{v['name']}] harness done ({status})")

    # Parse final result from harness log
    net, ex_fee, trades, wins, losses = 0.0, 0.0, 0, 0, 0
    try:
        with open(log) as f:
            data = json.load(f) if log.endswith(".json") else None
        # Parse last JSON block from log
        import re
        text = open(log).read()
        blocks = re.findall(r'\{[^{}]*"net_pnl"[^{}]*\}', text, re.DOTALL)
        if blocks:
            last = json.loads(blocks[-1])
            net = last.get("net_pnl", 0)
            ex_fee = last.get("ex_fee_pnl", 0)
            trades = last.get("total_trades", 0)
            wins = last.get("total_wins", 0)
            losses = last.get("total_losses", 0)
    except Exception:
        pass

    results[v["name"]] = {
        "status": status,
        "net": net,
        "ex_fee": ex_fee,
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "port": v["port"],
    }


def print_results(results: dict):
    print("\n" + "=" * 80)
    print("FINAL RESULTS")
    print("=" * 80 + "\n")
    sorted_r = sorted(results.items(), key=lambda x: x[1].get("net", -999), reverse=True)
    labels = ["[WINNER]"] + [f"[#{i}]" for i in range(2, len(sorted_r) + 1)]
    for label, (name, r) in zip(labels, sorted_r):
        wr = (r["wins"] / r["trades"] * 100) if r["trades"] else 0.0
        print(f"{label} {name}")
        print(f"  status={r['status']}")
        print(f"  net={r['net']:+.4f}  ex_fee={r['ex_fee']:+.4f}  fee_drag={abs(r['net']-r['ex_fee']):.4f}")
        print(f"  trades={r['trades']}  wins={r['wins']}  losses={r['losses']}  win_rate={wr:.1f}%")
        print()

    import datetime
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = f"/tmp/sweep_result_{ts}.json"
    with open(out, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Results saved → {out}")
    print("\n" + "=" * 80)
    print("V3-FIX SWEEP COMPLETE")
    print("=" * 80)


def main():
    total = len(VARIANTS)
    print("=" * 80)
    print(f"V3-FIX SEQUENTIAL SWEEP  ({total} variants × {DURATION}s each = {total * DURATION // 60} min total)")
    print("=" * 80 + "\n")

    os.system("pkill -f quantplot_ai_server.py 2>/dev/null; sleep 1")
    results = {}

    for i, v in enumerate(VARIANTS, 1):
        print(f"[{i}/{total}] Starting {v['name']} on port {v['port']}...")
        os.system(f"lsof -ti:{v['port']} 2>/dev/null | xargs kill -9 2>/dev/null; sleep 1")
        proc = start_server(v)
        if not wait_for_server(v["port"]):
            proc.terminate()
            results[v["name"]] = {"status": "server_failed", "net": 0, "ex_fee": 0,
                                   "trades": 0, "wins": 0, "losses": 0, "port": v["port"]}
            continue
        run_harness(v, results)
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        os.system(f"lsof -ti:{v['port']} 2>/dev/null | xargs kill -9 2>/dev/null")
        print(f"  Server stopped.\n")

    print_results(results)


if __name__ == "__main__":
    main()
