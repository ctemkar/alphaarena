#!/usr/bin/env python3
"""
Parallel test runner - extended-hold duration sweep.
Insight: reversal IS real (ex_fee positive) but HOLD exits capture only 30% of needed recovery.
Fix: hold longer (timed exit or TP set just above breakeven).
Breakeven = 8 bps round-trip (taker). Maker = 1 bps round-trip.
"""
import json
import os
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

# EXTENDED-HOLD DURATION SWEEP
# Insight from prior runs: ex_fee=+$1.22 on $5k (0.024% captured) but need 0.080% (8bps) to
# break even on taker fees. Direction is correct but HOLD-triggered exit only captures 30% of
# the needed recovery. Fix: hold longer (timed or TP just above breakeven).
#
# Taker breakeven: 8 bps round-trip. Maker breakeven: 1 bps round-trip.
# If BTC drops 8bps and reverts fully, gross profit = 8bps. Need > 8bps to be net positive.
# Currently capturing 2.4bps → close 3x too early.
VARIANTS = [
    # V1 — Timed exit: hold exactly 80 ticks (4 min) after HOLD signal, no TP/SL.
    # Tests: does BTC revert enough in 4 min to overcome 8bps fee? Pure time-based.
    {
        "name": "VARIANT-V1 (Timed 80tick=4min hold, taker 8bps)",
        "port": 8001,
        "model": "Llama-3.2",
        "desk": "btc",
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
        "fee_bps": "3",
        "slippage_bps": "1",
        "hold_extend_ticks": "80",   # 80 ticks × 3s = 4 min max
        "tp_bps": "0",               # no TP — pure timed exit
        "sl_bps": "0",               # no SL — ride it out
        "reversal_require_recovery": "0",
    },
    # V2 — TP=8bps (exactly taker breakeven), SL=6bps, max=80ticks.
    # Exit the moment we reach breakeven (profit $0 ex-fees but net=$0 instead of net<0).
    # SL at 6bps caps the loss per trade to ~75% of typical loss.
    {
        "name": "VARIANT-V2 (TP=8bps=breakeven SL=6bps max=80ticks)",
        "port": 8002,
        "model": "Llama-3.2",
        "desk": "btc",
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
        "fee_bps": "3",
        "slippage_bps": "1",
        "hold_extend_ticks": "80",
        "tp_bps": "8",               # exactly breakeven on taker fees
        "sl_bps": "6",
        "reversal_require_recovery": "0",
    },
    # V3 — TP=16bps (2× taker breakeven), SL=8bps, max=120ticks (6 min).
    # Ambitious: wait for full reversal + profit. Long enough for BTC to complete the move.
    {
        "name": "VARIANT-V3 (TP=16bps SL=8bps max=120ticks=6min)",
        "port": 8003,
        "model": "Llama-3.2",
        "desk": "btc",
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
        "fee_bps": "3",
        "slippage_bps": "1",
        "hold_extend_ticks": "120",  # 120 ticks × 3s = 6 min max
        "tp_bps": "16",              # 2× taker breakeven
        "sl_bps": "8",
        "reversal_require_recovery": "0",
    },
    # V4 — Maker fees + TP=6bps (6× maker breakeven), SL=4bps, max=80ticks.
    # Maker breakeven = 1bps. TP=6bps = large profit relative to cost. Feasible if BTC reverts.
    {
        "name": "VARIANT-V4 (Maker 1bps rt + TP=6bps SL=4bps max=80ticks)",
        "port": 8004,
        "model": "Llama-3.2",
        "desk": "btc",
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
        "fee_bps": "0.5",            # maker: 0.5bps/leg = 1bps rt
        "slippage_bps": "0",
        "hold_extend_ticks": "80",
        "tp_bps": "6",               # 6× maker breakeven (1bps)
        "sl_bps": "4",
        "reversal_require_recovery": "0",
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
        # Fee/slippage per variant: taker default = 3+1 bps/leg; maker simulation = 0.5+0 bps/leg
        "ALPHA_ANALYTICS_FEE_BPS": str(v.get("fee_bps", "3")),
        "ALPHA_ANALYTICS_SLIPPAGE_BPS": str(v.get("slippage_bps", "1")),
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
        "ALPHA_HARD_MAX_ORDER_USD": str(v["size_usd"]),  # override paper-mode hard cap for size sweep
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
        # Keep partial output so START/END/DELTA can still be parsed if present.
        status = f"timeout({timeout_s}s)"
        out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        err = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
        output = out + err + f"\n[TIMEOUT] harness exceeded {timeout_s}s\n"
    except Exception as e:
        status = f"error({type(e).__name__})"
        output = f"[ERROR] harness failed: {e}\n"

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

