#!/usr/bin/env python3
"""
Backtest: Compare 3 strategy improvements vs baseline on real BTC 1-min data.
  Alt 1: Trend filter    — only counter-trend when 15m bar is flat (<0.15% move)
  Alt 2: Momentum follow — take direction of the move, not against it
  Alt 3: Wide TP         — TP=20bps (≈3.3× SL=6bps) to survive ~40% WR
Reports: net PnL, Sharpe, max drawdown, win rate, expectancy per risk policy.
"""
import json, math, sys, urllib.request
from datetime import datetime, timezone

SIZE_USD    = 5_000
FEE_BPS     = 0.5       # per leg
COOLDOWN_M  = 1
DAYS        = 7

# ── Fetch ──────────────────────────────────────────────────────────────────────
def fetch_btc_1m(days=7):
    limit = min(days * 24 * 60, 1000)
    url = f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit={limit}"
    print(f"Fetching {limit} 1-min BTC/USDT bars from Binance...", flush=True)
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            raw = json.loads(r.read())
    except Exception as e:
        print(f"Binance failed ({e}), trying Coinbase...")
        now_s = int(datetime.now(timezone.utc).timestamp())
        start = now_s - days * 86400
        url2 = f"https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=60&start={start}&end={now_s}"
        try:
            req = urllib.request.Request(url2, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                raw_cb = json.loads(r.read())
            raw = [[c[0]*1000, c[3], c[2], c[1], c[4], c[5]] for c in raw_cb]
            raw.sort(key=lambda x: x[0])
        except Exception as e2:
            print(f"Coinbase failed ({e2})"); return []
    candles = [{"t": int(c[0])//1000, "o": float(c[1]), "h": float(c[2]),
                "l": float(c[3]), "c": float(c[4])} for c in raw]
    return candles

def build_15m(candles):
    """Aggregate 1-min bars into 15-min bars keyed by bar-start index."""
    bars = {}
    for i, c in enumerate(candles):
        slot = c["t"] // 900 * 900   # floor to 15-min bucket
        if slot not in bars:
            bars[slot] = {"o": c["o"], "h": c["h"], "l": c["l"], "c": c["c"], "first_i": i}
        else:
            bars[slot]["h"] = max(bars[slot]["h"], c["h"])
            bars[slot]["l"] = min(bars[slot]["l"], c["l"])
            bars[slot]["c"] = c["c"]
    # build reverse lookup: 1m-bar index → parent 15m bar
    idx_to_15m = {}
    for slot, bar in bars.items():
        fi = bar["first_i"]
        for i in range(fi, fi + 15):
            if i < len(candles):
                idx_to_15m[i] = bar
    return idx_to_15m

# ── Core simulate (shared engine, strategy variant passed as callables) ────────
def simulate(candles, threshold, tp_bps, sl_bps,
             direction_fn=None,   # fn(move) -> -1 or 1
             entry_filter=None,   # fn(i, candles, ctx) -> bool (True = allow trade)
             ctx=None):
    trades = []
    last_i = -999
    fee_rt = (FEE_BPS * 2) / 10_000   # round-trip fee fraction

    if direction_fn is None:
        direction_fn = lambda move: -1 if move > 0 else 1   # reversal default

    for i in range(len(candles) - 1):
        bar = candles[i]
        move = (bar["c"] - bar["o"]) / bar["o"]

        if abs(move) < threshold or i - last_i < COOLDOWN_M:
            continue
        if entry_filter and not entry_filter(i, candles, ctx):
            continue

        direction = direction_fn(move)
        entry_px = bar["c"]
        tp_px = entry_px * (1 + direction * tp_bps / 10_000)
        sl_px = entry_px * (1 - direction * sl_bps / 10_000)

        outcome, exit_px = "TIMEOUT", candles[min(i+30, len(candles)-1)]["c"]
        for j in range(i+1, min(i+30, len(candles))):
            nb = candles[j]
            if direction == 1:
                if nb["h"] >= tp_px: outcome, exit_px = "WIN",  tp_px; break
                if nb["l"] <= sl_px: outcome, exit_px = "LOSS", sl_px; break
            else:
                if nb["l"] <= tp_px: outcome, exit_px = "WIN",  tp_px; break
                if nb["h"] >= sl_px: outcome, exit_px = "LOSS", sl_px; break

        pnl_raw = direction * (exit_px - entry_px) / entry_px * SIZE_USD
        pnl_net = pnl_raw - fee_rt * SIZE_USD
        trades.append({
            "ts": datetime.fromtimestamp(bar["t"]).strftime("%Y-%m-%d %H:%M"),
            "dir": "LONG" if direction==1 else "SHORT",
            "move_pct": round(move*100, 4),
            "outcome": outcome,
            "pnl_net": round(pnl_net, 4),
        })
        last_i = i

    return trades

def stats(trades, candles):
    if not trades: return None
    pnls = [t["pnl_net"] for t in trades]
    net = sum(pnls)
    wins = sum(1 for p in pnls if p > 0)
    wr = wins / len(trades) * 100
    exp = net / len(trades)
    hours = (candles[-1]["t"] - candles[0]["t"]) / 3600
    avg = sum(pnls)/len(pnls)
    std = math.sqrt(sum((p-avg)**2 for p in pnls)/(len(pnls)-1)) if len(pnls)>1 else 0
    tpd = len(trades) / max(hours/24,1)
    sharpe = (avg/std)*math.sqrt(tpd*365) if std>0 else 0.0
    cum, peak, mdd = 0.0, 0.0, 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    return {"n": len(trades), "wins": wins, "wr": wr, "net": net,
            "exp": exp, "sharpe": sharpe, "mdd": mdd,
            "per4h": net/hours*4, "hours": hours}

def print_variant(label, trades, candles):
    s = stats(trades, candles)
    if not s:
        print(f"  {label:<42} 0 trades — threshold never hit"); return s
    print(f"  {label:<42} trades={s['n']:>4}  wr={s['wr']:.0f}%  net={s['net']:>+8.2f}  "
          f"$/4h={s['per4h']:>+7.2f}  sharpe={s['sharpe']:>+6.2f}  maxDD={s['mdd']:>6.2f}")
    # last 3 trades
    for t in trades[-3:]:
        print(f"    {t['ts']}  {t['dir']:5}  mv={t['move_pct']:+.3f}%  {t['outcome']:7}  pnl={t['pnl_net']:+.2f}")
    return s

# ── Entry ──────────────────────────────────────────────────────────────────────
def main():
    candles = fetch_btc_1m(DAYS)
    if not candles:
        print("Cannot fetch data."); sys.exit(1)
    hours = (candles[-1]["t"] - candles[0]["t"]) / 3600
    print(f"✓ {len(candles)} bars  |  {datetime.fromtimestamp(candles[0]['t'])} → "
          f"{datetime.fromtimestamp(candles[-1]['t'])}  ({hours:.0f}h)\n")

    idx_to_15m = build_15m(candles)
    THRESHOLD = 0.0005   # 0.05% (sweep winner C)
    TP_BASE, SL = 10, 6

    # ── BASELINE (reversal, no filter) ────────────────────────────────────────
    print(f"{'='*72}")
    print("  BASELINE: C (reversal, thr=0.05%, TP=10bps, SL=6bps)")
    print(f"{'='*72}")
    base_trades = simulate(candles, THRESHOLD, TP_BASE, SL)
    base_stats  = print_variant("Baseline (reversal TP10 SL6)", base_trades, candles)

    # ── ALT 1: TREND FILTER ───────────────────────────────────────────────────
    print(f"\n{'='*72}")
    print("  ALT 1: Trend filter — skip if 15m bar move > 0.15% (trending)")
    print(f"{'='*72}")

    def trend_filter_015(i, candles, ctx):
        bar = candles[i]
        parent = ctx.get(i)
        if parent is None: return True
        parent_move = abs((parent["c"] - parent["o"]) / parent["o"])
        return parent_move < 0.0015   # allow only if 15m bar is flat

    alt1_trades_015 = simulate(candles, THRESHOLD, TP_BASE, SL,
                               entry_filter=trend_filter_015, ctx=idx_to_15m)
    s1a = print_variant("Alt1a: trend filter 0.15%", alt1_trades_015, candles)

    def trend_filter_010(i, candles, ctx):
        bar = candles[i]
        parent = ctx.get(i)
        if parent is None: return True
        parent_move = abs((parent["c"] - parent["o"]) / parent["o"])
        return parent_move < 0.0010   # tighter: 0.10%

    alt1_trades_010 = simulate(candles, THRESHOLD, TP_BASE, SL,
                               entry_filter=trend_filter_010, ctx=idx_to_15m)
    s1b = print_variant("Alt1b: trend filter 0.10%", alt1_trades_010, candles)

    # ── ALT 2: MOMENTUM FOLLOW ────────────────────────────────────────────────
    print(f"\n{'='*72}")
    print("  ALT 2: Momentum follow — enter IN direction of move (not reversal)")
    print(f"{'='*72}")

    momentum_fn = lambda move: 1 if move > 0 else -1   # follow the move

    alt2_trades_tp10 = simulate(candles, THRESHOLD, TP_BASE, SL, direction_fn=momentum_fn)
    s2a = print_variant("Alt2a: momentum TP10 SL6", alt2_trades_tp10, candles)

    alt2_trades_tp15 = simulate(candles, THRESHOLD, 15, SL, direction_fn=momentum_fn)
    s2b = print_variant("Alt2b: momentum TP15 SL6", alt2_trades_tp15, candles)

    alt2_trades_tp20 = simulate(candles, THRESHOLD, 20, SL, direction_fn=momentum_fn)
    s2c = print_variant("Alt2c: momentum TP20 SL6", alt2_trades_tp20, candles)

    # ── ALT 3: WIDE TP ────────────────────────────────────────────────────────
    print(f"\n{'='*72}")
    print("  ALT 3: Wide TP (reversal but TP ≥ 3× SL to survive ~40% WR)")
    print(f"{'='*72}")
    # breakeven WR = SL/(TP+SL) for symmetric payoff
    # TP=18 SL=6 → breakeven = 6/24 = 25% ✓ (we have 39%)
    # TP=20 SL=6 → breakeven = 6/26 = 23% ✓
    # TP=15 SL=6 → breakeven = 6/21 = 29% ✓
    for tp in [15, 18, 20, 24]:
        be = SL / (tp + SL) * 100
        alt3_trades = simulate(candles, THRESHOLD, tp, SL)
        s = print_variant(f"Alt3:  reversal TP{tp} SL{SL} (BE={be:.0f}%)", alt3_trades, candles)

    # ── ALT 3+FILTER COMBO ────────────────────────────────────────────────────
    print(f"\n{'='*72}")
    print("  ALT 3+FILTER: Wide TP + trend filter combo")
    print(f"{'='*72}")
    for tp in [18, 20, 24]:
        be = SL / (tp + SL) * 100
        combo_trades = simulate(candles, THRESHOLD, tp, SL,
                                entry_filter=trend_filter_015, ctx=idx_to_15m)
        s = print_variant(f"Combo: reversal TP{tp} SL{SL} + filter 0.15%", combo_trades, candles)

    # ── SUMMARY TABLE ─────────────────────────────────────────────────────────
    print(f"\n{'='*72}")
    print("  FINAL RANKING — sorted by $/4h")
    print(f"{'='*72}")

    all_variants = [
        ("Baseline reversal TP10 SL6",        base_trades),
        ("Alt1a trend-filter 0.15%",           alt1_trades_015),
        ("Alt1b trend-filter 0.10%",           alt1_trades_010),
        ("Alt2a momentum TP10 SL6",            alt2_trades_tp10),
        ("Alt2b momentum TP15 SL6",            alt2_trades_tp15),
        ("Alt2c momentum TP20 SL6",            alt2_trades_tp20),
        ("Alt3a reversal TP15 SL6",            simulate(candles, THRESHOLD, 15, SL)),
        ("Alt3b reversal TP18 SL6",            simulate(candles, THRESHOLD, 18, SL)),
        ("Alt3c reversal TP20 SL6",            simulate(candles, THRESHOLD, 20, SL)),
        ("Alt3d reversal TP24 SL6",            simulate(candles, THRESHOLD, 24, SL)),
        ("Combo TP18 SL6 + filter 0.15%",      simulate(candles, THRESHOLD, 18, SL, entry_filter=trend_filter_015, ctx=idx_to_15m)),
        ("Combo TP20 SL6 + filter 0.15%",      simulate(candles, THRESHOLD, 20, SL, entry_filter=trend_filter_015, ctx=idx_to_15m)),
        ("Combo TP24 SL6 + filter 0.15%",      simulate(candles, THRESHOLD, 24, SL, entry_filter=trend_filter_015, ctx=idx_to_15m)),
    ]

    rows = []
    for name, trades in all_variants:
        s = stats(trades, candles)
        if s:
            rows.append((name, s))
    rows.sort(key=lambda x: x[1]["per4h"], reverse=True)

    print(f"  {'Variant':<44} {'N':>5} {'WR%':>5} {'Net$':>8} {'$/4h':>8} {'Sharpe':>7} {'MaxDD':>7}")
    print(f"  {'-'*80}")
    for name, s in rows:
        marker = " <-- BEST" if name == rows[0][0] else ""
        print(f"  {name:<44} {s['n']:>5} {s['wr']:>5.0f}% {s['net']:>+8.2f} "
              f"{s['per4h']:>+8.2f} {s['sharpe']:>+7.2f} {s['mdd']:>7.2f}{marker}")

    best_name, best_s = rows[0]
    print(f"\n  WINNER: {best_name}")
    print(f"    Net P&L over {best_s['hours']:.0f}h: ${best_s['net']:+.2f}")
    print(f"    $/4h:       ${best_s['per4h']:+.2f}")
    print(f"    Win rate:   {best_s['wr']:.0f}%")
    print(f"    Expectancy: ${best_s['exp']:+.2f}/trade")
    print(f"    Sharpe:     {best_s['sharpe']:.2f}")
    print(f"    Max DD:     ${best_s['mdd']:.2f}")
    print(f"{'='*72}")

    result = {
        "period_hours": hours,
        "baseline": vars_to_dict("Baseline", base_stats),
        "ranking": [(n, {"per4h": round(s["per4h"],2), "wr": round(s["wr"],1),
                         "sharpe": round(s["sharpe"],2), "net": round(s["net"],2),
                         "mdd": round(s["mdd"],2), "n": s["n"]}) for n,s in rows],
        "best": best_name,
    }
    with open("/tmp/alt_backtest_results.json","w") as f:
        json.dump(result, f, indent=2)
    print(f"\n  Saved → /tmp/alt_backtest_results.json")

def vars_to_dict(name, s):
    if not s: return {"name": name, "trades": 0}
    return {"name": name, "per4h": round(s["per4h"],2), "wr": round(s["wr"],1),
            "sharpe": round(s["sharpe"],2), "net": round(s["net"],2), "mdd": round(s["mdd"],2)}

if __name__ == "__main__":
    main()
