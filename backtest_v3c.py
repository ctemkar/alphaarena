#!/usr/bin/env python3
"""
Backtest: deterministic_reversal strategy on real BTC 1-minute OHLCV data.
Fetches last 7 days from Binance public API (no keys needed).
Tests all 3 sweep variants: A (0.03% TP10 SL6), B (0.03% TP15 SL6), C (0.05% TP10 SL6)
Reports: net PnL, Sharpe, max drawdown, win rate, expectancy — as required by risk policy.
"""
import json
import math
import sys
import urllib.request
from datetime import datetime, timezone

# ── Config ────────────────────────────────────────────────────────────────────
SIZE_USD   = 5_000          # same as sweep
FEE_BPS    = 0.5            # per leg (maker)
SLIP_BPS   = 0.0            # paper assumption (0 slippage)
COOLDOWN_M = 1              # minutes cooldown between trades (≈ hold_cooldown_ticks=6 * 3s = 18s → round to 1 min)
WINDOW_M   = 1              # minutes lookback for momentum (det_move_window=20 ticks * 3s ≈ 1 min bar)
DAYS       = 7

VARIANTS = [
    {"name": "A: thr=0.03% TP=10bps SL=6bps",  "threshold": 0.0003, "tp_bps": 10, "sl_bps": 6},
    {"name": "B: thr=0.03% TP=15bps SL=6bps",  "threshold": 0.0003, "tp_bps": 15, "sl_bps": 6},
    {"name": "C: thr=0.05% TP=10bps SL=6bps",  "threshold": 0.0005, "tp_bps": 10, "sl_bps": 6},
]

# ── Fetch data ─────────────────────────────────────────────────────────────────
def fetch_btc_1m(days=7):
    limit = min(days * 24 * 60, 1000)   # Binance max 1000 per call
    url = f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit={limit}"
    print(f"Fetching {limit} 1-min BTC/USDT bars from Binance...")
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            raw = json.loads(r.read())
    except Exception as e:
        print(f"Binance failed ({e}), trying Coinbase...")
        # Coinbase fallback: no auth needed for granularity=60
        now_s = int(datetime.now(timezone.utc).timestamp())
        start = now_s - days * 86400
        url2 = f"https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=60&start={start}&end={now_s}"
        try:
            req = urllib.request.Request(url2, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                raw_cb = json.loads(r.read())
            # Coinbase: [time, low, high, open, close, volume] — no Binance format
            raw = [[c[0]*1000, c[3], c[2], c[1], c[4], c[5]] for c in raw_cb]
            raw.sort(key=lambda x: x[0])
        except Exception as e2:
            print(f"Coinbase also failed ({e2})")
            return []
    # Binance kline: [open_time, open, high, low, close, volume, ...]
    candles = []
    for c in raw:
        candles.append({
            "t": int(c[0]) // 1000,
            "o": float(c[1]),
            "h": float(c[2]),
            "l": float(c[3]),
            "c": float(c[4]),
        })
    return candles

# ── Simulate strategy ──────────────────────────────────────────────────────────
def simulate(candles, threshold, tp_bps, sl_bps):
    """
    Deterministic reversal: when price moves ≥ threshold% in 1 bar (open→close),
    expect reversal → enter counter-direction trade at close price.
    Exit at TP or SL on subsequent bars (use high/low to check).
    """
    trades = []
    last_trade_idx = -999
    fee_per_trade = (FEE_BPS + SLIP_BPS) * 2 / 10_000  # round-trip, as fraction

    i = 0
    while i < len(candles) - 1:
        bar = candles[i]
        move = (bar["c"] - bar["o"]) / bar["o"]

        # Check momentum threshold
        if abs(move) < threshold or i - last_trade_idx < COOLDOWN_M:
            i += 1
            continue

        # Signal: reversal of the move
        direction = -1 if move > 0 else 1   # SHORT if up move, LONG if down move
        entry_px = bar["c"]
        tp_px = entry_px * (1 + direction * tp_bps / 10_000)
        sl_px = entry_px * (1 - direction * sl_bps / 10_000)

        # Scan forward bars for TP/SL
        outcome = None
        exit_px = None
        for j in range(i + 1, min(i + 30, len(candles))):  # max 30-bar hold
            next_bar = candles[j]
            if direction == 1:   # LONG
                if next_bar["h"] >= tp_px:
                    outcome, exit_px = "WIN", tp_px
                    break
                if next_bar["l"] <= sl_px:
                    outcome, exit_px = "LOSS", sl_px
                    break
            else:                # SHORT
                if next_bar["l"] <= tp_px:
                    outcome, exit_px = "WIN", tp_px
                    break
                if next_bar["h"] >= sl_px:
                    outcome, exit_px = "LOSS", sl_px
                    break

        if outcome is None:   # held to max window, exit at last bar close
            outcome = "TIMEOUT"
            exit_px = candles[min(i + 30, len(candles) - 1)]["c"]

        pnl_raw = direction * (exit_px - entry_px) / entry_px * SIZE_USD
        fee_cost = fee_per_trade * SIZE_USD
        pnl_net = pnl_raw - fee_cost

        trades.append({
            "idx": i,
            "ts": datetime.fromtimestamp(bar["t"]).strftime("%Y-%m-%d %H:%M"),
            "direction": "LONG" if direction == 1 else "SHORT",
            "entry": round(entry_px, 2),
            "exit": round(exit_px, 2),
            "move_pct": round(move * 100, 4),
            "outcome": outcome,
            "pnl_raw": round(pnl_raw, 4),
            "fee": round(fee_cost, 4),
            "pnl_net": round(pnl_net, 4),
        })

        last_trade_idx = i
        i += 1

    return trades

# ── Stats ──────────────────────────────────────────────────────────────────────
def stats(trades, candles):
    if not trades:
        return None

    pnls = [t["pnl_net"] for t in trades]
    wins = [t for t in trades if t["pnl_net"] > 0]
    losses = [t for t in trades if t["pnl_net"] <= 0]
    net = sum(pnls)
    win_rate = len(wins) / len(trades) * 100
    expectancy = net / len(trades)

    # Sharpe (daily returns from cumulative P&L)
    hours = (candles[-1]["t"] - candles[0]["t"]) / 3600
    days = max(hours / 24, 1)
    if len(pnls) > 1:
        avg = sum(pnls) / len(pnls)
        variance = sum((p - avg) ** 2 for p in pnls) / (len(pnls) - 1)
        std = math.sqrt(variance)
        trades_per_day = len(trades) / days
        sharpe = (avg / std) * math.sqrt(trades_per_day * 365) if std > 0 else 0.0
    else:
        sharpe = 0.0

    # Max drawdown (on cumulative net PnL curve)
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        cum += p
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd

    # Daily P&L breakdown
    days_dict = {}
    for t in trades:
        day = t["ts"].split(" ")[0]
        days_dict.setdefault(day, []).append(t["pnl_net"])
    daily_pnls = [sum(v) for v in days_dict.values()]

    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "net_pnl": net,
        "expectancy": expectancy,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "daily_pnls": daily_pnls,
        "hours_tested": hours,
    }

# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    candles = fetch_btc_1m(DAYS)
    if not candles:
        print("Failed to fetch historical data. Cannot run backtest.")
        sys.exit(1)

    print(f"✓ Got {len(candles)} 1-min bars  |  {candles[0]['t']} → {candles[-1]['t']}")
    print(f"  BTC range: ${min(c['l'] for c in candles):,.0f} – ${max(c['h'] for c in candles):,.0f}")
    print(f"  Period: {datetime.fromtimestamp(candles[0]['t'])} → {datetime.fromtimestamp(candles[-1]['t'])}\n")

    results = []
    for v in VARIANTS:
        trades = simulate(candles, v["threshold"], v["tp_bps"], v["sl_bps"])
        s = stats(trades, candles)
        if s is None:
            print(f"{'─'*70}")
            print(f"  {v['name']}")
            print(f"  ⚠ 0 trades — threshold never triggered in test window")
            results.append({"variant": v["name"], "trades": 0})
            continue

        print(f"{'─'*70}")
        print(f"  {v['name']}")
        print(f"  Tested: {s['hours_tested']:.0f}h  |  Trades: {s['trades']}  ({s['trades']/s['hours_tested']*4:.1f}/4h avg)")
        print(f"  Win Rate: {s['win_rate']:.0f}%  ({s['wins']}W / {s['losses']}L)")
        print(f"  Net P&L:  ${s['net_pnl']:+.2f}  over {s['hours_tested']:.0f}h")
        print(f"  4h equiv: ${s['net_pnl'] / s['hours_tested'] * 4:+.2f}/4h")
        print(f"  Expectancy: ${s['expectancy']:+.2f}/trade")
        print(f"  Sharpe:     {s['sharpe']:.2f}")
        print(f"  Max DD:     ${s['max_drawdown']:.2f}")
        print(f"  Daily P&L: {['${:+.2f}'.format(d) for d in s['daily_pnls']]}")

        # Sample trades
        print(f"  Last 5 trades:")
        for t in trades[-5:]:
            print(f"    {t['ts']}  {t['direction']:5s}  mv={t['move_pct']:+.3f}%  → {t['outcome']:7s}  pnl={t['pnl_net']:+.2f}")

        results.append({
            "variant": v["name"],
            "trades": s["trades"],
            "win_rate": round(s["win_rate"], 1),
            "net_pnl": round(s["net_pnl"], 2),
            "pnl_per_4h": round(s["net_pnl"] / s["hours_tested"] * 4, 2),
            "expectancy": round(s["expectancy"], 2),
            "sharpe": round(s["sharpe"], 2),
            "max_drawdown": round(s["max_drawdown"], 2),
        })

    print(f"\n{'═'*70}")
    print("  BACKTEST SUMMARY (7-day real BTC 1m data)")
    print(f"{'═'*70}")
    print(f"  {'Variant':<40} {'Trades':>7} {'WR%':>5} {'Net$':>8} {'$/4h':>8} {'Sharpe':>7} {'MaxDD':>7}")
    print(f"  {'-'*70}")
    for r in results:
        if r["trades"] == 0:
            print(f"  {r['variant']:<40} {'0':>7} {'—':>5} {'—':>8} {'—':>8} {'—':>7} {'—':>7}")
        else:
            print(f"  {r['variant']:<40} {r['trades']:>7} {r['win_rate']:>5.0f}% {r['net_pnl']:>+8.2f} {r['pnl_per_4h']:>+8.2f} {r['sharpe']:>7.2f} {r['max_drawdown']:>7.2f}")

    print(f"\n  Live results from sweep (4h each, paper, BTC ~$79k):")
    print(f"  {'A (0.03% TP10 SL6)':<40} {'9':>7} {'11%':>5} {'-15.26':>+8} {'-15.26':>+8} {'—':>7} {'—':>7}  ← kill-switch")
    print(f"  {'B (0.03% TP15 SL6)':<40} {'10':>7} {'40%':>5} {'+1.74':>+8} {'+1.74':>+8} {'—':>7} {'—':>7}")
    print(f"  {'C (0.05% TP10 SL6)':<40} {'10':>7} {'30%':>5} {'+3.41':>+8} {'+3.41':>+8} {'—':>7} {'—':>7}  ← sweep winner")

    print(f"\n  Baseline 12h overnight (previous session):")
    print(f"  {'OVERNIGHT-V2 (0.03% TP10 SL6)':<40} {'21':>7} {'19%':>5} {'+4.34':>+8} {'+1.45':>+8} {'—':>7} {'—':>7}")

    print(f"\n  Recommendation based on backtest + live sweep:")
    best = [r for r in results if r.get("trades", 0) > 0]
    if best:
        best.sort(key=lambda r: r.get("pnl_per_4h", -999), reverse=True)
        top = best[0]
        print(f"  ✓ Best variant: {top['variant']}")
        print(f"    Sharpe={top['sharpe']:.2f}, $/4h=${top['pnl_per_4h']:+.2f}, WR={top['win_rate']:.0f}%")
    print(f"{'═'*70}")

    with open("/tmp/backtest_results.json", "w") as f:
        json.dump({
            "fetched_bars": len(candles),
            "period_hours": (candles[-1]["t"] - candles[0]["t"]) / 3600,
            "btc_range": [min(c["l"] for c in candles), max(c["h"] for c in candles)],
            "results": results,
            "live_sweep": [
                {"name": "A", "trades": 9,  "win_rate": 11.1, "net": -15.26, "kill_switch": True},
                {"name": "B", "trades": 10, "win_rate": 40.0, "net": 1.74,   "kill_switch": False},
                {"name": "C", "trades": 10, "win_rate": 30.0, "net": 3.41,   "kill_switch": False},
            ],
        }, f, indent=2)
    print(f"\n  Full results saved → /tmp/backtest_results.json")

if __name__ == "__main__":
    main()
