#!/usr/bin/env python3
"""
Test script to evaluate signal generation strategies:
1. trend_filter - Strict trend filtering (reject counter-trend signals)
2. simple_prompt - Simplified LLM prompt
3. reversal - Invert all LLM signals
4. selective_reverse - Invert only clear counter-trend signals
5. deterministic_momentum - Skip the LLM and trade on recent move direction
6. deterministic_confirmed - Stronger deterministic momentum with multi-tick confirmation
"""
import os
import sys
import time
import json
import subprocess
import signal
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

# Test configuration
STRATEGIES = [
    "trend_filter",
    "simple_prompt",
    "reversal",
    "selective_reverse",
    "deterministic_momentum",
    "deterministic_confirmed",
]
TEST_DURATION_SECONDS = 120  # 2 minutes per strategy
CHECK_INTERVAL_SECONDS = 5
STATE_URL = "http://127.0.0.1:8000/api/state"
WORKSPACE = Path("/Users/chetantemkar/development/alphaarena")

def stop_server(proc):
    """Stop the trading server process"""
    if proc and proc.poll() is None:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Stopping server (PID {proc.pid})...")
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Server stopped")

def start_server(strategy):
    """Start the trading server with specified strategy"""
    print(f"\n{'='*70}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] STARTING TEST: Strategy = {strategy.upper()}")
    print(f"{'='*70}")
    
    env = os.environ.copy()
    env["ALPHA_SIGNAL_STRATEGY"] = strategy
    env["ALPHA_INSECURE_SSL"] = "1"
    env["ALPHA_LIVE_TRADING"] = "0"
    env["ALPHA_PAPER_MODE"] = "1"
    env["ALPHA_AUTO_SELECT_ENABLED"] = "0"
    env["ALPHA_BASE_SIGNAL_CHANCE"] = "1.0"
    env["ALPHA_MIN_PROFIT_EDGE_PCT"] = "0.0"
    env["ALPHA_MIN_TRADE_MOVE_PCT"] = "0.0"
    env["ALPHA_MOMENTUM_OVERRIDE_THRESHOLD_PCT"] = "0.005"
    env["ALPHA_HOLD_STREAK_MOMENTUM_OVERRIDE_ENABLED"] = "1"
    env["ALPHA_HOLD_STREAK_MOMENTUM_OVERRIDE_MIN_STREAK"] = "1"
    env["ALPHA_SELECTIVE_REVERSE_MIN_MOVE_PCT"] = "0.02"
    env["ALPHA_DETERMINISTIC_MOMENTUM_MIN_MOVE_PCT"] = "0.02"
    env["ALPHA_DETERMINISTIC_CONFIRMED_MIN_MOVE_PCT"] = "0.04"
    env["ALPHA_DETERMINISTIC_CONFIRMED_MIN_TICKS"] = "2"
    
    # Reset paper mode state before starting
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Resetting paper mode to clear ledger...")
    # This will be done via API after startup
    
    proc = subprocess.Popen(
        [sys.executable, "quantplot_ai_server.py"],
        cwd=WORKSPACE,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Server started with PID {proc.pid}")
    return proc

def wait_for_server_ready(max_retries=30):
    """Wait for server to be ready"""
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(STATE_URL)
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Server is ready")
                    return True
        except (urllib.error.URLError, Exception):
            pass
        
        if attempt < max_retries - 1:
            time.sleep(1)
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] ERROR: Server did not become ready")
    return False

def reset_paper_mode():
    """Reset paper mode via API"""
    try:
        data = json.dumps({"enabled": True}).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:8000/api/paper-mode",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Paper mode reset")
            return True
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Warning: Could not reset paper mode: {e}")
        return False

def _post_json(url, payload, timeout=5):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode() or "{}"
        return resp.status, json.loads(body)

def select_models_for_test():
    """Select one model per desk so trades are actually generated."""
    try:
        status_btc, _ = _post_json("http://127.0.0.1:8000/api/select", {"model": "Qwen-2.5", "desk": "btc"})
        status_basket, _ = _post_json("http://127.0.0.1:8000/api/select", {"model": "DeepSeek-R1", "desk": "basket"})
        ok = status_btc == 200 and status_basket == 200
        if ok:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Selected models: Qwen-2.5 (BTC), DeepSeek-R1 (BASKET)")
        else:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Warning: Model selection returned non-200 status")
        return ok
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Warning: Could not select models: {e}")
        return False

def get_daily_stats():
    """Get win rate and trade stats from current server state"""
    try:
        req = urllib.request.Request(STATE_URL)
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status != 200:
                return None
            
            data = json.loads(resp.read().decode())
            daily = data.get("daily_summary") or data.get("daily") or {}
            
            return {
                "trades": daily.get("trades", 0),
                "wins": daily.get("wins", 0),
                "losses": daily.get("losses", 0),
                "win_rate_pct": daily.get("win_rate_pct", 0.0),
                "total_pnl_usd": daily.get("total_pnl_usd", 0.0),
                "max_drawdown_usd": daily.get("max_drawdown_usd", 0.0),
                "expectancy_usd": daily.get("expectancy_usd", 0.0),
            }
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Error fetching stats: {e}")
        return None

def run_test_for_strategy(strategy):
    """Run test for one strategy"""
    server = None
    try:
        # Start server
        server = start_server(strategy)
        time.sleep(3)
        
        # Wait for server to be ready
        if not wait_for_server_ready():
            print(f"[{datetime.now().strftime('%H:%M:%S')}] ERROR: Server failed to start")
            return None
        
        # Reset paper mode to clear any previous ledger
        time.sleep(2)
        reset_paper_mode()
        time.sleep(1)
        select_models_for_test()
        time.sleep(2)
        
        # Initial stats
        initial_stats = get_daily_stats()
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Initial stats: {initial_stats}")
        
        # Run for TEST_DURATION_SECONDS
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Running test for {TEST_DURATION_SECONDS} seconds...")
        start_time = time.time()
        last_check = start_time
        check_count = 0
        
        while time.time() - start_time < TEST_DURATION_SECONDS:
            now = time.time()
            if now - last_check >= CHECK_INTERVAL_SECONDS:
                check_count += 1
                elapsed = int(now - start_time)
                stats = get_daily_stats()
                if stats:
                    init = initial_stats or {}
                    run_trades = max(0, int(stats.get("trades", 0)) - int(init.get("trades", 0)))
                    run_wins = max(0, int(stats.get("wins", 0)) - int(init.get("wins", 0)))
                    run_pnl = float(stats.get("total_pnl_usd", 0.0)) - float(init.get("total_pnl_usd", 0.0))
                    run_win_rate = (100.0 * run_wins / run_trades) if run_trades > 0 else 0.0
                    print(
                        f"[{datetime.now().strftime('%H:%M:%S')}] "
                        f"Elapsed: {elapsed}s | "
                        f"RunTrades: {run_trades} | "
                        f"RunWins: {run_wins} | "
                        f"RunWin%: {run_win_rate:.2f}% | "
                        f"RunPnL: ${run_pnl:+.2f}"
                    )
                last_check = now
            
            time.sleep(1)
        
        # Final stats
        time.sleep(2)  # Let any in-flight trades settle
        final_stats = get_daily_stats() or {}

        init = initial_stats or {}
        d_trades = max(0, int(final_stats.get("trades", 0)) - int(init.get("trades", 0)))
        d_wins = max(0, int(final_stats.get("wins", 0)) - int(init.get("wins", 0)))
        d_losses = max(0, int(final_stats.get("losses", 0)) - int(init.get("losses", 0)))
        d_pnl = float(final_stats.get("total_pnl_usd", 0.0)) - float(init.get("total_pnl_usd", 0.0))
        d_expectancy = (d_pnl / d_trades) if d_trades > 0 else 0.0
        d_win_rate = (100.0 * d_wins / d_trades) if d_trades > 0 else 0.0

        result = {
            "trades": d_trades,
            "wins": d_wins,
            "losses": d_losses,
            "win_rate_pct": round(d_win_rate, 2),
            "total_pnl_usd": round(d_pnl, 4),
            "expectancy_usd": round(d_expectancy, 4),
            "max_drawdown_usd": float(final_stats.get("max_drawdown_usd", 0.0)),
            "initial_snapshot": init,
            "final_snapshot": final_stats,
        }

        print(f"[{datetime.now().strftime('%H:%M:%S')}] Final delta stats: {result}")

        return result
        
    finally:
        stop_server(server)
        time.sleep(2)  # Cool down between tests

def main():
    """Run all configured tests sequentially"""
    print(f"\n{'='*70}")
    print(f"ALPHA ARENA SIGNAL STRATEGY COMPARISON TEST")
    print(f"Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Strategies: {', '.join(STRATEGIES)}")
    print(f"Duration per strategy: {TEST_DURATION_SECONDS}s")
    print(f"{'='*70}\n")
    
    results = {}
    
    for strategy in STRATEGIES:
        stats = run_test_for_strategy(strategy)
        results[strategy] = stats
        
        if stats:
            print(f"\n✓ {strategy.upper()} Complete")
            print(f"  Trades: {stats['trades']}")
            print(f"  Wins: {stats['wins']}")
            print(f"  Win Rate: {stats['win_rate_pct']:.2f}%")
            print(f"  Total PnL: ${stats['total_pnl_usd']:+.2f}")
            print(f"  Expectancy: ${stats['expectancy_usd']:+.2f}")
            print(f"  Max Drawdown: ${stats['max_drawdown_usd']:.2f}")
        else:
            print(f"\n✗ {strategy.upper()} Failed - No stats collected")
    
    # Summary comparison
    print(f"\n{'='*70}")
    print("FINAL RESULTS COMPARISON")
    print(f"{'='*70}")
    
    sorted_results = sorted(
        [(k, v) for k, v in results.items() if v is not None],
        key=lambda x: (
            x[1].get("win_rate_pct", 0.0),
            x[1].get("expectancy_usd", -1e9),
            x[1].get("total_pnl_usd", -1e9),
        ),
        reverse=True,
    )
    
    for rank, (strategy, stats) in enumerate(sorted_results, 1):
        print(
            f"{rank}. {strategy.upper():15s} | "
            f"Win%: {stats['win_rate_pct']:6.2f}% | "
            f"Trades: {stats['trades']:3d} | "
            f"PnL: ${stats['total_pnl_usd']:8.2f}"
        )
    
    if sorted_results:
        winner = sorted_results[0][0]
        print(f"\n🏆 RECOMMENDED STRATEGY: {winner.upper()}")
        print(f"   Win Rate: {sorted_results[0][1]['win_rate_pct']:.2f}%")
        print(f"   Total PnL: ${sorted_results[0][1]['total_pnl_usd']:.2f}")
    
    # Save results to file
    results_file = WORKSPACE / "strategy_test_results.json"
    with open(results_file, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults saved to: {results_file}")

if __name__ == "__main__":
    main()
