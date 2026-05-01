#!/usr/bin/env python3
"""
Parallel test runner - 3-way comparison of middle-ground basket configs.
Variant A: edge=0.045, persistence=1, reversal=1.3, size=$25  (strict-mid)
Variant B: edge=0.035, persistence=1, reversal=1.2, size=$30  (middle)
Variant C: edge=0.028, persistence=1, reversal=1.0, size=$40  (lenient)
All use Llama-3.2 (fast), canary off.
"""
import json
import os
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

VARIANTS = [
    # V1: Reversal basket 0.060% — best ex-fee alpha seen (60-min session), now with hold=1.
    {
        "name": "VARIANT-V1 (det_reversal BASKET 0.060 hold=1 regime=1 size=5000)",
        "port": 8001,
        "model": "Llama-3.2",
        "desk": "basket",
        "edge": 0.060,
        "persistence": 1,
        "reversal": 1.6,
        "size_usd": 5000,
        "force_close_on_hold": "1",
        "signal_chance": 1.0,
        "momentum_override": "0",
        "momentum_threshold": 0.060,
        "signal_strategy": "deterministic_reversal",
        "det_move_window": "20",
        "hold_cooldown_ticks": "12",
        "regime_filter": "1",
    },
    # V2: Reversal basket 0.080% — fewer but higher-quality reversal signals.
    {
        "name": "VARIANT-V2 (det_reversal BASKET 0.080 hold=1 regime=1 size=5000)",
        "port": 8002,
        "model": "Llama-3.2",
        "desk": "basket",
        "edge": 0.080,
        "persistence": 1,
        "reversal": 1.6,
        "size_usd": 5000,
        "force_close_on_hold": "1",
        "signal_chance": 1.0,
        "momentum_override": "0",
        "momentum_threshold": 0.080,
        "signal_strategy": "deterministic_reversal",
        "det_move_window": "20",
        "hold_cooldown_ticks": "12",
        "regime_filter": "1",
    },
    # V3: Confirmed momentum basket 0.060/2 ticks — tonight's best confirmed config.
    {
        "name": "VARIANT-V3 (det_confirmed BASKET 0.060 ticks=2 cd=10 hold=1 size=5000)",
        "port": 8003,
        "model": "Llama-3.2",
        "desk": "basket",
        "edge": 0.060,
        "persistence": 1,
        "reversal": 1.6,
        "size_usd": 5000,
        "force_close_on_hold": "1",
        "signal_chance": 1.0,
        "momentum_override": "0",
        "momentum_threshold": 0.060,
        "signal_strategy": "deterministic_confirmed",
        "det_move_window": "20",
        "det_confirmed_min_move_pct": 0.060,
        "det_confirmed_min_ticks": 2,
        "hold_cooldown_ticks": "10",
    },
    # V4: Reversal BTC 0.060% — BTC is more volatile overnight; regime filter guards trending.
    {
        "name": "VARIANT-V4 (det_reversal BTC 0.060 hold=1 regime=1 size=5000)",
        "port": 8004,
        "model": "Llama-3.2",
        "desk": "btc",
        "edge": 0.060,
        "persistence": 1,
        "reversal": 1.6,
        "size_usd": 5000,
        "force_close_on_hold": "1",
        "signal_chance": 1.0,
        "momentum_override": "0",
        "momentum_threshold": 0.060,
        "signal_strategy": "deterministic_reversal",
        "det_move_window": "20",
        "hold_cooldown_ticks": "12",
        "regime_filter": "1",
    },
]

DURATION = int(os.getenv("ALPHA_PARALLEL_DURATION", "3600"))


def wait_for_server(port: int, max_retries: int = 50, delay: float = 1.0) -> bool:
    for _ in range(max_retries):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=2) as r:
                json.loads(r.read())
                print(f"  ✓ port {port} ready")
                return True
        except Exception:
            pass
        time.sleep(delay)
    print(f"  ✗ port {port} failed to start")
    return False


def start_server(v: dict) -> subprocess.Popen:
    env = os.environ.copy()
    env.update({
        "ALPHA_PORT": str(v["port"]),
        "ALPHA_INSECURE_SSL": "1",
        "ALPHA_LIVE_TRADING": "0",
        "ALPHA_PAPER_MODE": "1",
        "ALPHA_AUTO_SELECT_ENABLED": "0",
        "ALPHA_CANARY_ENABLED": "0",
        "ALPHA_BASE_SIGNAL_CHANCE": str(v.get("signal_chance", "1.0")),
        "ALPHA_MIN_PROFIT_EDGE_PCT": "0.03",
        # Use realistic taker fee: 5 bps per leg = 10 bps round-trip (Binance/Bybit standard)
        # Realistic perpetual futures fees: 3 bps taker fee + 1 bps slippage = 4 bps per leg = 8 bps round-trip
        "ALPHA_ANALYTICS_FEE_BPS": "3",
        "ALPHA_ANALYTICS_SLIPPAGE_BPS": "1",
        # Zero out per-desk edge gate for deterministic strategies; threshold is the sole gate.
        "ALPHA_MIN_PROFIT_EDGE_PCT_BTC": "0.0" if v.get("signal_strategy", "").startswith("deterministic") else "0.05",
        "ALPHA_MIN_PROFIT_EDGE_PCT_BASKET": "0.0" if v.get("signal_strategy", "").startswith("deterministic") else str(v["edge"]),
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
        # Deterministic strategies re-evaluate every tick — don't block re-entry for 12 ticks after a HOLD.
        "ALPHA_HOLD_COOLDOWN_TICKS": str(v.get("hold_cooldown_ticks", "2" if v.get("signal_strategy", "").startswith("deterministic") else "12")),
    })
    proc = subprocess.Popen(
        ["python3", "quantplot_ai_server.py"],
        env=env,
        stdout=open(f"/tmp/alpha_server_{v['port']}.log", "w"),
        stderr=subprocess.STDOUT,
        cwd=Path(__file__).parent,
    )
    print(f"  Started {v['name']} on port {v['port']} (pid {proc.pid})")
    return proc


def run_harness(v: dict, result_store: dict) -> None:
    env = os.environ.copy()
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
    proc = subprocess.run(
        ["python3", "run_controlled_paper_session.py"],
        env=env,
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent,
        timeout=DURATION + 120,
    )
    output = proc.stdout + proc.stderr
    with open(log_path, "w") as f:
        f.write(output)
    # Extract DELTA block
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
    result_store[v["name"]] = {"delta": delta, "raw": output}
    print(f"  [{v['name']}] harness done")


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
        print(f"  net={r['net']:+.4f}  ex_fee={r['ex_fee']:+.4f}  fee_drag={fee_drag:.4f}")
        print(f"  trades={r['trades']}  wins={r['wins']}  losses={r['losses']}  win_rate={win_rate:.1f}%")
    print("\n" + "=" * 80)
    print("PARALLEL TEST COMPLETE")
    print("=" * 80)

    # Write timestamped JSON summary so results are always retrievable
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    summary_path = f"/tmp/sweep_result_{ts}.json"
    with open(summary_path, "w") as f:
        json.dump({"timestamp": ts, "duration_s": DURATION, "ranked": ranked}, f, indent=2)
    # Overwrite latest sentinel so a watcher knows the run finished
    with open("/tmp/sweep_latest_result.json", "w") as f:
        json.dump({"timestamp": ts, "duration_s": DURATION, "ranked": ranked}, f, indent=2)
    print(f"\nResults saved → {summary_path}")


def main():
    print("=" * 80)
    print(f"4-WAY PARALLEL TEST  ({DURATION}s each = {DURATION//60} min)")
    print("=" * 80)

    os.system("pkill -f quantplot_ai_server.py 2>/dev/null; sleep 1")

    print("\n[SETUP] Starting servers...")
    servers = []
    for v in VARIANTS:
        servers.append((v, start_server(v)))
        time.sleep(1)

    print("\n[SETUP] Waiting for servers...")
    ready = [wait_for_server(v["port"]) for v, _ in servers]
    if not all(ready):
        print("✗ Not all servers came up. Aborting.")
        for _, p in servers:
            p.terminate()
        return

    results = {}
    threads = [threading.Thread(target=run_harness, args=(v, results)) for v, _ in servers]

    print(f"\n[TESTS] All {len(threads)} sessions starting in parallel...\n")
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        print("\n[CLEANUP] Stopping servers...")
        for _, p in servers:
            p.terminate()
        for _, p in servers:
            p.wait(timeout=5)

    print_results(results)


if __name__ == "__main__":
    main()

