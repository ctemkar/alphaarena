#!/usr/bin/env python3
import json
import urllib.request
import time
import sys
from datetime import datetime

URL = "http://127.0.0.1:8000/api/state"
DURATION = 300  # 5 minutes
INTERVAL = 10   # Check every 10 seconds

def get_stats():
    try:
        req = urllib.request.Request(URL)
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode())
            return data.get("daily", {})
    except Exception as e:
        print(f"Error: {e}")
        return None

print(f"\n{'='*70}")
print(f"TREND_FILTER STRATEGY TEST")
print(f"Duration: 5 minutes | Check interval: 10 seconds")
print(f"{'='*70}\n")

start_time = time.time()
last_stats = None

while time.time() - start_time < DURATION:
    stats = get_stats()
    if stats:
        elapsed = int(time.time() - start_time)
        if stats != last_stats:
            print(
                f"[{elapsed:3d}s] "
                f"Trades: {stats['trades']:3d} | "
                f"Wins: {stats['wins']:2d} | "
                f"Losses: {stats['losses']:3d} | "
                f"Win%: {stats['win_rate_pct']:6.2f}% | "
                f"PnL: ${stats['total_pnl_usd']:+8.2f}"
            )
            last_stats = stats
    
    time.sleep(INTERVAL)

print(f"\n{'='*70}")
print("FINAL RESULTS")
print(f"{'='*70}")
if stats:
    print(f"Trades: {stats['trades']}")
    print(f"Wins: {stats['wins']}")
    print(f"Losses: {stats['losses']}")
    print(f"Win Rate: {stats['win_rate_pct']:.2f}%")
    print(f"Total PnL: ${stats['total_pnl_usd']:+.2f}")
    print(f"Expectancy: ${stats['expectancy_usd']:+.2f}")
    print(f"Max Drawdown: ${stats['max_drawdown_usd']:.2f}")
    
    # Compare to baseline
    baseline_win_pct = 0.78
    improvement = stats['win_rate_pct'] - baseline_win_pct
    if improvement > 0:
        print(f"\n✓ IMPROVEMENT: +{improvement:.2f}% vs baseline {baseline_win_pct:.2f}%")
    else:
        print(f"\n✗ WORSE: {improvement:.2f}% vs baseline {baseline_win_pct:.2f}%")
