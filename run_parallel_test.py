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
    {
        "name": "VARIANT-V1 (edge=0.055 p=1 rev=1.6 size=150 mthr=0.010)",
        "port": 8001,
        "model": "Llama-3.2",
        "edge": 0.055,
        "persistence": 1,
        "reversal": 1.6,
        "size_usd": 150,
        "force_close_on_hold": "0",
        "signal_chance": 1.0,
        "momentum_override": "1",
        "momentum_threshold": 0.010,
    },
    {
        "name": "VARIANT-V2 (edge=0.055 p=1 rev=1.6 size=150 mthr=0.020)",
        "port": 8002,
        "model": "Llama-3.2",
        "edge": 0.055,
        "persistence": 1,
        "reversal": 1.6,
        "size_usd": 150,
        "force_close_on_hold": "0",
        "signal_chance": 1.0,
        "momentum_override": "1",
        "momentum_threshold": 0.020,
    },
    {
        "name": "VARIANT-V3 (edge=0.055 p=1 rev=1.6 size=150 mthr=0.030)",
        "port": 8003,
        "model": "Llama-3.2",
        "edge": 0.055,
        "persistence": 1,
        "reversal": 1.6,
        "size_usd": 150,
        "force_close_on_hold": "0",
        "signal_chance": 1.0,
        "momentum_override": "1",
        "momentum_threshold": 0.030,
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
        "ALPHA_MIN_PROFIT_EDGE_PCT_BTC": "0.05",
        "ALPHA_MIN_PROFIT_EDGE_PCT_BASKET": str(v["edge"]),
        "ALPHA_MIN_TRADE_MOVE_PCT": "0.0",
        "ALPHA_MOMENTUM_OVERRIDE_THRESHOLD_PCT_BASKET": str(v.get("momentum_threshold", v["edge"])),
        "ALPHA_DIRECTIONAL_PERSISTENCE_MIN_STREAK_BASKET": str(v["persistence"]),
        "ALPHA_REVERSAL_EDGE_MULTIPLIER_BASKET": str(v["reversal"]),
        "ALPHA_BASKET_ORDER_USD": str(v["size_usd"]),
        "ALPHA_HOLD_STREAK_MOMENTUM_OVERRIDE_ENABLED": v.get("momentum_override", "1"),
        "ALPHA_PAPER_FORCE_CLOSE_ON_HOLD": v.get("force_close_on_hold", "1"),
        "ALPHA_DISABLE_BASKET_TIMEOUT_FALLBACK": "1",
        "ALPHA_PAPER_RISK_OFF_MAX_DRAWDOWN_PCT": "0.20",
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
    env.update({
        "ALPHA_CONTROLLED_PORT": str(v["port"]),
        "ALPHA_CONTROLLED_DURATION_SECONDS": str(DURATION),
        "ALPHA_CONTROLLED_POLL_SECONDS": "15",
        "ALPHA_CONTROLLED_ENABLE_BTC": "0",
        "ALPHA_CONTROLLED_ENABLE_BASKET": "1",
        "ALPHA_CONTROLLED_BASKET_MODEL": v["model"],
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
        label = "WINNER" if i == 0 else f"#{i+1}"
        print(f"\n[{label}] {r['name']}")
        print(f"  net={r['net']:+.6f}  ex_fee={r['ex_fee']:+.6f}  fee_drag={fee_drag:.6f}")
        print(f"  trades={r['trades']}  wins={r['wins']}  losses={r['losses']}")
    print("\n" + "=" * 80)
    print("PARALLEL TEST COMPLETE")
    print("=" * 80)


def main():
    print("=" * 80)
    print(f"3-WAY PARALLEL TEST  ({DURATION}s each = {DURATION//60} min)")
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

