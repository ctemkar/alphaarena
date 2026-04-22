import http.server, socketserver, json, threading, time, urllib.request, os, re

def load_env():
    env = {}
    if os.path.exists('.env'):
        with open('.env') as f:
            for line in f:
                if '=' in line and not line.startswith('#'):
                    k, v = line.strip().split('=', 1)
                    env[k.strip()] = v.strip().replace('"', '').replace("'", "")
    return env

ENV = load_env()
OR_KEY = ENV.get('OPENROUTER_API_KEY', '')
PORT = 8000
SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT", "BNB": "BNBUSDT", "DOGE": "DOGEUSDT"}
LIVE_PRICES = {s: 0.0 for s in SYMBOLS}
START_PRICES = {s: 0.0 for s in SYMBOLS}
HISTORY = []
LOGS = []
MODEL_REQUEST_TIMEOUT = 120
OLLAMA_KEEP_ALIVE = "30m"
OLLAMA_NUM_PREDICT = 8
SCOUT_MODEL = ENV.get("OPENROUTER_PRIMARY_MODEL", "meta-llama/llama-4-scout")
SCOUT_INTERVAL_SEC = 600
SCOUT_NOTE = ""
SCOUT_LAST_TS = 0.0
CORE_MODELS = ("DeepSeek R1", "Qwen 3")
MODEL_START_BANKROLL = 100.0
CORE_START_BANKROLL = MODEL_START_BANKROLL
CORE_BET = {
    "target_pct": 0.0,
    "confidence": 0.0,
    "reason": "warming up",
    "last_update": 0.0,
    "bankroll": CORE_START_BANKROLL,
    "allocation_pct": 0.25,
    "direction": "FLAT",
}

# Section 1 uses six distinct models and compares them by signed exposure/equity.
# DeepSeek R1 and Qwen 3 are treated as core winners from prior runs.
ARENA_DATA = {
    "DeepSeek R1": {"bal": 100.0, "pos": 0.0, "color": "#10a37f", "active": True, "p": "ollama", "m": "deepseek-r1:8b", "poll_interval_sec": 25, "core": True},
    "Qwen 3": {"bal": 100.0, "pos": 0.0, "color": "#6366f1", "active": True, "p": "ollama", "m": "qwen3:latest", "poll_interval_sec": 35, "core": True},
    "Mistral": {"bal": 100.0, "pos": 0.0, "color": "#d97757", "active": True, "p": "ollama", "m": "mistral:latest", "poll_interval_sec": 45},
    "Llama 3.1 8B": {"bal": 100.0, "pos": 0.0, "color": "#0668E1", "active": True, "p": "ollama", "m": "llama3.1:8b", "poll_interval_sec": 60},
    "Llama 3": {"bal": 100.0, "pos": 0.0, "color": "#67e8f9", "active": True, "p": "ollama", "m": "llama3:latest", "poll_interval_sec": 90},
    "GPT-OSS": {"bal": 100.0, "pos": 0.0, "color": "#f0b90b", "active": True, "p": "ollama", "m": "gpt-oss:20b", "poll_interval_sec": 150}
}

# System 2 starts as the full multi-coin basket, unlike System 1 which is BTC-only.
BASKET_ACTIVE = ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE"]

TRADE_SIZE_USD = 20.0
MAX_POSITION_USD = 20.0

for bot in ARENA_DATA.values():
    bot.setdefault("start_bankroll", MODEL_START_BANKROLL)
    bot.setdefault("avg_entry", 0.0)
    bot.setdefault("peak_price", 0.0)
    bot.setdefault("last_trade_ts", 0.0)
    bot.setdefault("last_action", "WAIT")
    bot.setdefault("last_confidence", 0.0)
    bot.setdefault("last_reason", "")
    bot.setdefault("last_target_pct", 0.0)
    bot.setdefault("next_poll_ts", 0.0)

_now = time.time()
for idx, bot in enumerate(ARENA_DATA.values()):
    bot["next_poll_ts"] = _now + (idx * 5)


def _recent_prices(limit=12):
    return [float(p) for p in HISTORY[-limit:] if float(p) > 0]


def _pct_change(old, new):
    old = float(old or 0.0)
    new = float(new or 0.0)
    if old <= 0:
        return 0.0
    return (new - old) / old


def _clamp(value, lo, hi):
    return max(lo, min(hi, float(value)))


def _direction_sign(value):
    txt = str(value or "").strip().upper()
    if txt in {"LONG", "BUY", "UP", "BULL", "BULLISH"}:
        return 1
    if txt in {"SHORT", "SELL", "DOWN", "BEAR", "BEARISH"}:
        return -1
    return 0


def _ema(values, period):
    vals = [float(v) for v in values if float(v) > 0]
    if not vals:
        return 0.0
    if len(vals) == 1:
        return vals[0]
    period = max(2, min(int(period), len(vals)))
    alpha = 2.0 / (period + 1.0)
    ema = vals[0]
    for value in vals[1:]:
        ema = (alpha * value) + ((1.0 - alpha) * ema)
    return ema


def _market_context():
    prices = _recent_prices(12)
    last = prices[-1] if prices else 0.0
    fast = _ema(prices[-5:], 5) if len(prices) >= 2 else last
    slow = _ema(prices[-10:], 10) if len(prices) >= 2 else last
    mom_3 = _pct_change(prices[-4], last) if len(prices) >= 4 else 0.0
    mom_6 = _pct_change(prices[-7], last) if len(prices) >= 7 else 0.0
    trend_score = 0
    trend_score += 1 if last >= fast else -1
    trend_score += 1 if fast >= slow else -1
    trend_score += 1 if mom_3 >= 0 else -1
    trend = "UP" if trend_score >= 2 else "DOWN" if trend_score <= -2 else "FLAT"
    return {
        "last": last,
        "fast": fast,
        "slow": slow,
        "mom_3": mom_3,
        "mom_6": mom_6,
        "trend": trend,
        "samples": prices,
    }


def _parse_decision(text):
    raw = str(text or "").strip()
    if not raw:
        return {"action": "HOLD", "confidence": 0.0, "reason": ""}

    match = re.search(r"\{.*\}", raw, re.S)
    if match:
        try:
            data = json.loads(match.group(0))
            target_pct = data.get("target_pct", data.get("target_position_pct", data.get("target", None)))
            direction = data.get("direction", data.get("side", data.get("bias", None)))
            allocation_pct = data.get(
                "allocation_pct",
                data.get("size_pct", data.get("risk_pct", data.get("position_pct", None))),
            )
            leverage = float(data.get("leverage", 1.0) or 1.0)
            if target_pct is None and "action" in data:
                action = str(data.get("action", "HOLD")).upper().strip()
                if action == "BUY":
                    target_pct = 0.35
                elif action == "SELL":
                    target_pct = -0.35
                else:
                    target_pct = 0.0
            if target_pct is None and direction is not None:
                sign = _direction_sign(direction)
                if allocation_pct is not None and sign != 0:
                    target_pct = sign * float(allocation_pct) * leverage
            target_pct = _clamp(target_pct if target_pct is not None else 0.0, -1.0, 1.0)
            return {
                "target_pct": target_pct,
                "confidence": float(data.get("confidence", 0.0) or 0.0),
                "reason": str(data.get("reason", "")).strip(),
            }
        except Exception:
            pass

    upper = raw.upper()
    if "SHORT" in upper or ("SELL" in upper and "BUY" not in upper):
        target_pct = -0.35
    elif "LONG" in upper or "BUY" in upper:
        target_pct = 0.35
    else:
        target_pct = 0.0

    confidence = 0.55 if target_pct != 0 else 0.0
    if "CONFIDENCE" in upper:
        m = re.search(r"CONFIDENCE[^0-9]*([0-9]+(?:\.[0-9]+)?)", upper)
        if m:
            try:
                confidence = float(m.group(1))
            except Exception:
                pass

    return {"target_pct": target_pct, "confidence": confidence, "reason": raw[:120]}


def _position_pnl_pct(bot, price):
    entry = float(bot.get("avg_entry", 0.0) or 0.0)
    price = float(price or 0.0)
    if entry <= 0 or price <= 0:
        return 0.0
    return (price - entry) / entry


def _equity(bot, price):
    return float(bot.get("bal", 0.0) or 0.0) + (float(bot.get("pos", 0.0) or 0.0) * float(price or 0.0))


def _net_exposure_pct(bot, price):
    equity = _equity(bot, price)
    if equity <= 0 or price <= 0:
        return 0.0
    return (float(bot.get("pos", 0.0) or 0.0) * price) / equity


def _refresh_core_bet():
    global CORE_BET
    core_rows = []
    for name in CORE_MODELS:
        bot = ARENA_DATA.get(name)
        if not bot:
            continue
        core_rows.append({
            "name": name,
            "target_pct": float(bot.get("last_target_pct", 0.0) or 0.0),
            "confidence": float(bot.get("last_confidence", 0.0) or 0.0),
            "reason": str(bot.get("last_reason", "") or ""),
        })
    if not core_rows:
        return

    weighted = 0.0
    weight_sum = 0.0
    signs = set()
    reasons = []
    for row in core_rows:
        conf_w = max(0.1, row["confidence"] or 0.0)
        weighted += row["target_pct"] * conf_w
        weight_sum += conf_w
        if row["target_pct"] > 0:
            signs.add(1)
        elif row["target_pct"] < 0:
            signs.add(-1)
        if row["reason"]:
            reasons.append(f"{row['name']}: {row['reason'][:40]}")

    target_pct = weighted / weight_sum if weight_sum > 0 else 0.0
    if len(signs) > 1:
        target_pct *= 0.5

    confidence = min(1.0, weight_sum / max(1.0, len(core_rows)))
    conviction = _clamp(abs(target_pct) * max(0.25, confidence), 0.0, 1.0)
    allocation_pct = 0.25 + (0.75 * conviction)
    bankroll = CORE_START_BANKROLL * allocation_pct
    direction = "LONG" if target_pct > 0 else "SHORT" if target_pct < 0 else "FLAT"

    CORE_BET = {
        "target_pct": _clamp(target_pct, -1.0, 1.0),
        "confidence": confidence,
        "reason": " | ".join(reasons)[:180] if reasons else "core pair warming up",
        "last_update": time.time(),
        "bankroll": bankroll,
        "allocation_pct": allocation_pct,
        "direction": direction,
    }


def _sell_position(name, price, reason):
    bot = ARENA_DATA[name]
    qty = float(bot.get("pos", 0.0) or 0.0)
    if qty <= 0:
        return False
    entry = float(bot.get("avg_entry", 0.0) or 0.0)
    proceeds = qty * price
    pnl = (price - entry) * qty if entry > 0 else 0.0
    bot["bal"] += proceeds
    bot["pos"] = 0.0
    bot["avg_entry"] = 0.0
    bot["peak_price"] = 0.0
    bot["last_trade_ts"] = time.time()
    LOGS.append(f"{name}: SELL @ {price:.2f} | pnl=${pnl:.2f} | {reason}")
    return True


def _buy_position(name, price, reason):
    bot = ARENA_DATA[name]
    if float(bot.get("pos", 0.0) or 0.0) > 0:
        return False
    trade_amt = min(TRADE_SIZE_USD, float(bot.get("bal", 0.0) or 0.0), MAX_POSITION_USD)
    if trade_amt < 5.0:
        LOGS.append(f"{name}: BUY SKIP | insufficient cash | {reason}")
        return False
    qty = trade_amt / price if price > 0 else 0.0
    if qty <= 0:
        return False
    bot["bal"] -= trade_amt
    bot["pos"] = qty
    bot["avg_entry"] = price
    bot["peak_price"] = price
    bot["last_trade_ts"] = time.time()
    LOGS.append(f"{name}: BUY @ {price:.2f} | usd=${trade_amt:.2f} | {reason}")
    return True


def _set_target_position(name, price, target_pct, reason):
    bot = ARENA_DATA[name]
    equity = max(1.0, _equity(bot, price))
    target_pct = _clamp(target_pct, -1.0, 1.0)
    target_notional = target_pct * equity
    target_qty = target_notional / price if price > 0 else 0.0
    current_qty = float(bot.get("pos", 0.0) or 0.0)
    delta_qty = target_qty - current_qty
    bot["bal"] -= delta_qty * price
    bot["pos"] = target_qty
    if abs(target_qty) < 1e-10:
        bot["avg_entry"] = 0.0
    elif current_qty == 0 or (current_qty > 0 and target_qty < 0) or (current_qty < 0 and target_qty > 0):
        bot["avg_entry"] = price
    elif abs(target_qty) > abs(current_qty):
        prev_basis = float(bot.get("avg_entry", price) or price)
        bot["avg_entry"] = ((abs(current_qty) * prev_basis) + (abs(delta_qty) * price)) / max(abs(target_qty), 1e-10)
    bot["peak_price"] = max(float(bot.get("peak_price", 0.0) or 0.0), price)
    bot["last_trade_ts"] = time.time()
    bot["last_target_pct"] = target_pct
    side = "LONG" if target_pct > 0 else "SHORT" if target_pct < 0 else "FLAT"
    LOGS.append(f"{name}: TARGET {side} {abs(target_pct) * 100:.0f}% @ {price:.2f} | {reason}")
    return True


def _build_prompt(name, bot, ctx):
    equity = _equity(bot, ctx["last"])
    position = "FLAT"
    pos = float(bot.get("pos", 0.0) or 0.0)
    if pos > 0:
        position = (
            f"LONG qty={pos:.6f} "
            f"entry={float(bot.get('avg_entry', 0.0)):.2f} "
            f"unrealized_pct={_position_pnl_pct(bot, ctx['last']) * 100:.2f}"
        )
    elif pos < 0:
        position = (
            f"SHORT qty={abs(pos):.6f} "
            f"entry={float(bot.get('avg_entry', 0.0)):.2f} "
            f"unrealized_pct={-(_position_pnl_pct(bot, ctx['last']) * 100):.2f}"
        )

    return (
        f"You are {name}, trading BTC only.\n"
        "Goal: maximize risk-adjusted growth by choosing a signed BTC exposure.\n"
        "You are managing a real $100 bankroll, like a human trader in Alpha Arena.\n"
        "Return JSON only in this exact shape:\n"
        '{"direction":"LONG","allocation_pct":0.25,"confidence":0.0,"reason":"brief"}\n'
        "Rules:\n"
        "- target_pct is net exposure as a fraction of current equity.\n"
        "- direction must be LONG or SHORT; allocation_pct must be between 0.10 and 1.00.\n"
        "- Never return flat unless market data is unavailable. Always commit some capital.\n"
        "- If uncertain, use a small but real allocation like 0.15 or 0.20.\n"
        "- DeepSeek R1 and Qwen 3 are benchmark leaders. Compare yourself to them, but make your own trade.\n"
        f"Market snapshot:\n"
        f"equity={equity:.2f}\n"
        f"starting_bankroll={float(bot.get('start_bankroll', MODEL_START_BANKROLL) or MODEL_START_BANKROLL):.2f}\n"
        f"last_price={ctx['last']:.2f}\n"
        f"ema_fast={ctx['fast']:.2f}\n"
        f"ema_slow={ctx['slow']:.2f}\n"
        f"momentum_3={ctx['mom_3'] * 100:.3f}%\n"
        f"momentum_6={ctx['mom_6'] * 100:.3f}%\n"
        f"trend={ctx['trend']}\n"
        f"recent_closes={ctx['samples']}\n"
        f"current_exposure_pct={_net_exposure_pct(bot, ctx['last']) * 100:.2f}\n"
        f"position={position}\n"
        f"online_scout={SCOUT_NOTE}\n"
        f"core_bet_target_pct={CORE_BET['target_pct'] * 100:.2f}\n"
    )


def _enforce_bet_floor(name, target_pct, ctx):
    target_pct = float(target_pct or 0.0)
    if abs(target_pct) >= 0.05:
        return _clamp(target_pct, -1.0, 1.0)

    trend = str(ctx.get("trend", "FLAT")).upper()
    if trend == "UP":
        fallback = 0.15
    elif trend == "DOWN":
        fallback = -0.15
    else:
        fallback = 0.20 if name in CORE_MODELS else (-0.20 if hash(name) % 2 else 0.20)

    return _clamp(fallback, -1.0, 1.0)


def _poll_openrouter_scout():
    global SCOUT_NOTE, SCOUT_LAST_TS
    if not OR_KEY:
        return
    now = time.time()
    if now - SCOUT_LAST_TS < SCOUT_INTERVAL_SEC:
        return
    if LIVE_PRICES.get("BTC", 0.0) <= 0 or len(HISTORY) < 5:
        return
    ctx = _market_context()
    prompt = (
        "You are a sparse BTC market scout for a trading arena.\n"
        "Return JSON only in this exact shape:\n"
        '{"bias":"bullish|bearish|neutral","confidence":0.0,"note":"brief"}\n'
        "Give one short note about regime or risk, not a trade.\n"
        f"last_price={ctx['last']:.2f}\n"
        f"ema_fast={ctx['fast']:.2f}\n"
        f"ema_slow={ctx['slow']:.2f}\n"
        f"trend={ctx['trend']}\n"
        f"recent_closes={ctx['samples']}\n"
    )
    url = "https://openrouter.ai/api/v1/chat/completions"
    payload = json.dumps({
        "model": SCOUT_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(url, data=payload)
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {OR_KEY}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            res = json.loads(r.read().decode())
            text = res['choices'][0]['message']['content']
            note = text.strip()[:120]
            try:
                scout_json = json.loads(re.search(r"\{.*\}", text, re.S).group(0)) if re.search(r"\{.*\}", text, re.S) else {}
                bias = str(scout_json.get("bias", "neutral")).upper()
                confidence = float(scout_json.get("confidence", 0.0) or 0.0)
                note = str(scout_json.get("note", note)).strip()[:120]
                SCOUT_NOTE = f"{bias} {confidence:.2f} {note}"
            except Exception:
                SCOUT_NOTE = note
            SCOUT_LAST_TS = now
    except Exception as e:
        SCOUT_NOTE = f"scout_error:{str(e)[:60]}"
        SCOUT_LAST_TS = now

def fetch_binance():
    global START_PRICES
    while True:
        try:
            url = "https://api.binance.com/api/v3/ticker/price"
            with urllib.request.urlopen(url) as r:
                data = json.loads(r.read().decode())
                temp = {i['symbol']: float(i['price']) for i in data if 'symbol' in i}
                for s, pair in SYMBOLS.items():
                    if pair in temp:
                        LIVE_PRICES[s] = temp[pair]
                        if START_PRICES[s] == 0: START_PRICES[s] = LIVE_PRICES[s]
                if LIVE_PRICES["BTC"] > 0:
                    HISTORY.append(LIVE_PRICES["BTC"])
                    if len(HISTORY) > 30: HISTORY.pop(0)
        except: pass
        time.sleep(1)

def call_model(name):
    b = ARENA_DATA[name]
    if not b["active"]: return
    try:
        p = float(LIVE_PRICES.get("BTC") or 0.0)
        if p <= 0:
            return
        ctx = _market_context()
        prompt = _build_prompt(name, b, ctx)
        if b["p"] == "ollama":
            url = "http://localhost:11434/api/generate"
            payload = json.dumps({
                "model": b["m"],
                "prompt": prompt,
                "stream": False,
                "keep_alive": OLLAMA_KEEP_ALIVE,
                "format": "json",
                "options": {"num_predict": OLLAMA_NUM_PREDICT, "temperature": 0},
            }).encode()
        else:
            if not OR_KEY: return
            url = "https://openrouter.ai/api/v1/chat/completions"
            payload = json.dumps({"model": b["m"], "messages": [{"role": "user", "content": prompt}], "temperature": 0}).encode()
        
        req = urllib.request.Request(url, data=payload)
        req.add_header("Content-Type", "application/json")
        if b["p"] == "openrouter": req.add_header("Authorization", f"Bearer {OR_KEY}")
        
        with urllib.request.urlopen(req, timeout=MODEL_REQUEST_TIMEOUT) as r:
            res = json.loads(r.read().decode())
            if b["p"] == "ollama":
                text = res.get("response") or res.get("message", {}).get("content", "") or res.get("thinking", "")
            else:
                text = res['choices'][0]['message']['content']
            decision = _parse_decision(text)
            target_pct = _enforce_bet_floor(name, decision.get("target_pct", 0.0), ctx)
            confidence = float(decision.get("confidence", 0.0) or 0.0)
            reason = decision["reason"] or text.strip()[:80] or "no_reason"
            b["last_target_pct"] = target_pct
            side = "LONG" if target_pct > 0 else "SHORT" if target_pct < 0 else "FLAT"
            b["last_action"] = f"{side} {abs(target_pct) * 100:.0f}%"
            b["last_confidence"] = confidence
            b["last_reason"] = reason
            LOGS.append(f"{name}: {side} {abs(target_pct) * 100:.0f}% conf={confidence:.2f} | {reason}")
            _set_target_position(name, p, target_pct, reason)
            if b.get("core"):
                _refresh_core_bet()
    except Exception as e:
        LOGS.append(f"{name}: ERROR | {str(e)[:120]}")

def arena_loop():
    while True:
        if len(HISTORY) > 5:
            _poll_openrouter_scout()
            now = time.time()
            _refresh_core_bet()
            for name, b in ARENA_DATA.items():
                if b["active"] and now >= float(b.get("next_poll_ts", 0.0) or 0.0):
                    call_model(name)
                    b["next_poll_ts"] = time.time() + float(b.get("poll_interval_sec", 60.0) or 60.0)
                    time.sleep(1)
        if len(LOGS) > 3: LOGS.pop(0)
        time.sleep(15)

class H(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/data':
            p_btc = LIVE_PRICES["BTC"]
            for b in ARENA_DATA.values():
                b["total"] = b["bal"] + (b["pos"] * p_btc)
            bv = {s: (16.66 * (LIVE_PRICES[s]/START_PRICES[s])) if START_PRICES[s]>0 else 16.66 for s in SYMBOLS}
            self.send_response(200); self.send_header('Content-type','application/json'); self.end_headers()
            self.wfile.write(json.dumps({"prices":LIVE_PRICES, "bots":ARENA_DATA, "basket":bv, "active_c": BASKET_ACTIVE, "logs":LOGS, "core_bet": CORE_BET}).encode())
        elif self.path.startswith('/toggle?name='):
            n = self.path.split('=')[1]
            if n in ARENA_DATA:
                ARENA_DATA[n]["active"] = not ARENA_DATA[n]["active"]
                ARENA_DATA[n]["next_poll_ts"] = time.time() + 5
            elif n in SYMBOLS:
                if n in BASKET_ACTIVE: BASKET_ACTIVE.remove(n)
                else: BASKET_ACTIVE.append(n)
            self.send_response(200); self.end_headers()
        else:
            self.send_response(200); self.send_header('Content-type','text/html'); self.end_headers()
            self.wfile.write(HTML.encode())

class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True

HTML = """
<!DOCTYPE html><html><head><style>
    body { background:#0b0e11; color:#fff; font-family:sans-serif; text-align:center; padding:10px; margin:0; }
    .row-label { font-size: 10px; color: #848e9c; text-transform: uppercase; margin: 10px 0; }
    .grid { display:flex; flex-wrap:wrap; justify-content:center; gap:8px; min-height:40px; }
    .card { background:#181a20; border-radius:8px; padding:12px; border-bottom:4px solid; width:160px; cursor:pointer; }
    .card.small { width: 90px; padding: 6px; opacity: 0.3; border-bottom-width: 2px; }
    .total { font-size:24px; font-weight:900; font-family:monospace; color:#f0b90b; }
    .summary { background:#1e2329; border-radius:10px; padding:12px; border:2px solid #02c076; width:200px; }
</style></head><body>
    <h3 style="color:#f0b90b; margin:5px;">SYSTEM 1: AI ARENA (BTC BITMAP)</h3>
    <div class="row-label">Each model starts with $100 and trades its own book</div>
    <div class="row-label">Selection Row</div><div id="bi" class="grid"></div>
    <div class="row-label">Active Row</div><div id="ba" class="grid"></div>
    <div id="logs" style="font-size:11px; color:#f0b90b; margin-top:10px; font-family:monospace;"></div>
    <hr style="border:0; border-top:1px solid #2b2f36; margin:15px 0;">
    <h3 style="color:#02c076; margin:5px;">SYSTEM 2: CRYPTO BASKET</h3>
    <div class="row-label">Selection Row</div><div id="ci" class="grid"></div>
    <div class="row-label">Active Row</div><div id="ca" class="grid"></div>
    <script>
        async function tgl(n) { await fetch('/toggle?name='+n); }
        async function update() {
            try {
                const r = await fetch('/data'); const d = await r.json();
                let bAct="", bIn="", bS=0, bCount=0;
                const coreBet = d.core_bet || {target_pct:0, confidence:0, reason:""};
                Object.entries(d.bots).forEach(([n,b])=>{
                    const action = String(b.last_action || "WAIT").toUpperCase();
                    const conf = Number(b.last_confidence || 0);
                    const reason = String(b.last_reason || "");
                    const targetPct = Number(b.last_target_pct || 0) * 100;
                    const targetSide = targetPct > 0 ? "LONG" : targetPct < 0 ? "SHORT" : "FLAT";
                    const coreTag = b.core ? '<div style="font-size:9px;color:#f0b90b;margin-top:2px;">CORE WINNER</div>' : '';
                    const cash = Number(b.bal || 0);
                    const exposure = Math.abs(Number(b.pos || 0) * Number(d.prices.BTC || 0));
                    const h=`<div class="card ${b.active?'':'small'}" style="border-color:${b.color}" onclick="tgl('${n}')">
                        <div style="font-size:9px;opacity:0.5">${b.p.toUpperCase()}</div><div style="color:${b.color}">${n}</div>${coreTag}
                        <div class="total">$${b.total.toFixed(2)}</div></div>`;
                    const decisionLine = `<div style="font-size:10px;opacity:0.55;margin-top:4px;">${targetSide} ${Math.abs(targetPct).toFixed(0)}% ${conf > 0 ? `| ${conf.toFixed(2)}` : ""}${reason ? ` | ${reason.slice(0, 26)}` : ""}</div><div style="font-size:9px;opacity:0.45;margin-top:2px;">cash $${cash.toFixed(2)} | exp $${exposure.toFixed(2)}</div>`;
                    const card = h.replace('</div></div>', `${decisionLine}</div></div>`);
                    if(b.active){
                        bAct += card;
                        bS += b.total;
                        bCount += 1;
                    } else {
                        bIn += card;
                    }
                });
                const arenaCount = bCount;
                const arenaBase = arenaCount * 100.0;
                const arenaDelta = bS - arenaBase;
                const arenaAvg = arenaCount > 0 ? (bS / arenaCount) : 0;
                const arenaDeltaText = `${arenaDelta >= 0 ? '+' : '-'}$${Math.abs(arenaDelta).toFixed(2)}`;
                const coreSide = Number(coreBet.target_pct || 0) > 0 ? 'LONG' : Number(coreBet.target_pct || 0) < 0 ? 'SHORT' : 'FLAT';
                const coreSize = Math.abs(Number(coreBet.target_pct || 0) * 100).toFixed(0);
                const coreReason = String(coreBet.reason || "").slice(0, 48);
                document.getElementById('ba').innerHTML=bAct + (bAct?`<div class="summary"><div style="font-size:24px;color:#02c076">$${bS.toFixed(2)}</div><div style="font-size:12px;color:#848e9c">AVG $${arenaAvg.toFixed(2)}</div><div style="font-size:12px;color:${arenaDelta < 0 ? '#f84960' : '#02c076'}">P/L ${arenaDeltaText}</div><div>${arenaDelta < 0 ? 'ACTIVE DOWN' : 'ACTIVE TOTAL'}</div><div style="font-size:12px;color:#f0b90b;margin-top:6px;">CORE SIGNAL ${coreSide} ${coreSize}%</div><div style="font-size:10px;color:#848e9c;">${coreReason}</div></div>`:'');
                document.getElementById('bi').innerHTML=bIn;
                let cAct="", cIn="", cS=0, cCount=0;
                Object.entries(d.basket).forEach(([n,v])=>{
                    const isA=d.active_c.includes(n);
                    const h=`<div class="card ${isA?'':'small'}" style="border-color:#02c076" onclick="tgl('${n}')">
                        <div>${n}</div><div class="total">$${v.toFixed(2)}</div></div>`;
                    if(isA){cAct+=h; cS+=v; cCount++;} else cIn+=h;
                });
                const basketBase = cCount * 16.66;
                const basketDelta = cS - basketBase;
                const basketAvg = cCount > 0 ? (cS / cCount) : 0;
                const basketDeltaClass = basketDelta < 0 ? 'red' : 'green';
                const basketDeltaText = `${basketDelta >= 0 ? '+' : '-'}$${Math.abs(basketDelta).toFixed(2)}`;
                document.getElementById('ca').innerHTML=cAct + (cAct?`<div class="summary"><div style="font-size:24px;color:#02c076">$${cS.toFixed(2)}</div><div style="font-size:12px;color:#848e9c">AVG $${basketAvg.toFixed(2)}</div><div style="font-size:12px;color:${basketDelta < 0 ? '#f84960' : '#02c076'}">P/L ${basketDeltaText}</div><div>${basketDelta < 0 ? 'BASKET DOWN' : 'BASKET TOTAL'}</div></div>`:'');
                document.getElementById('ci').innerHTML=cIn;
                document.getElementById('logs').innerText = d.logs.join(" | ");
            } catch(e) {}
        }
        setInterval(update, 1000);
    </script></body></html>
"""
threading.Thread(target=fetch_binance, daemon=True).start()
threading.Thread(target=arena_loop, daemon=True).start()
ReusableTCPServer(("", PORT), H).serve_forever()
