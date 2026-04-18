import http.server, socketserver, json, threading, time, urllib.request, os

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
OLLAMA_TIMEOUT = 8
ARENA_CYCLE_SECONDS = 2
MAX_LEVERAGE = 8
TRADE_NOTIONAL_PCT = 0.75
MIN_TRADE_NOTIONAL = 25.0
SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT", "BNB": "BNBUSDT", "DOGE": "DOGEUSDT"}
LIVE_PRICES = {s: 0.0 for s in SYMBOLS}
START_PRICES = {s: 0.0 for s in SYMBOLS}
HISTORY = []
LOGS = []
OLLAMA_MODELS = []
OLLAMA_MODELS_TS = 0.0
MODEL_SEMAPHORE = threading.Semaphore(1)

ARENA_DATA = {
    "Qwen2.5-Coder (7B)": {"bal": 100.0, "pos": 0, "color": "#6366f1", "provider": "ollama", "model": "phi3:latest", "temperature": 0.25, "busy": False},
    "DeepSeek-R1 (8B)": {"bal": 100.0, "pos": 0, "color": "#67e8f9", "provider": "ollama", "model": "deepseek-r1:8b", "temperature": 0.45, "busy": False},
    "Llama 3.2 (3B)": {"bal": 100.0, "pos": 0, "color": "#10a37f", "provider": "ollama", "model": "llama3.2:3b", "temperature": 0.60, "busy": False},
    "Mistral (7B)": {"bal": 100.0, "pos": 0, "color": "#f0b90b", "provider": "ollama", "model": "llama3:latest", "temperature": 0.35, "busy": False},
    "Gemma 4": {"bal": 100.0, "pos": 0, "color": "#34d399", "provider": "ollama", "model": "phi3:latest", "temperature": 0.55, "busy": False}
}

BASKET_HISTORY = {s: [] for s in SYMBOLS}
BASKET_DATA = {
    name: {"bal": 100.0, "pos": {s: 0.0 for s in SYMBOLS}, "color": d["color"], "provider": d["provider"], "model": d["model"], "temperature": d.get("temperature", 0.35), "busy": False}
    for name, d in ARENA_DATA.items()
}
ARENA_ORDER = list(ARENA_DATA.keys())
BASKET_ORDER = list(BASKET_DATA.keys())
ARENA_IDX = 0
BASKET_IDX = 0

def extract_decision(text):
    upper = (text or "").strip().upper()
    if "BUY" in upper:
        return "BUY"
    if "SELL" in upper:
        return "SELL"
    if "LONG" in upper:
        return "BUY"
    if "SHORT" in upper:
        return "SELL"
    return None

def request_model_text(bot, prompt):
    with MODEL_SEMAPHORE:
        if bot["provider"] == "ollama":
            model = resolve_ollama_model(bot["model"])
            if not model:
                raise RuntimeError(f"model missing ({bot['model']})")
            url = "http://localhost:11434/api/generate"
            payload = json.dumps({
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": 1, "temperature": bot.get("temperature", 0.35)}
            }).encode()
        else:
            if not OR_KEY:
                raise RuntimeError("missing OPENROUTER_API_KEY")
            url = "https://openrouter.ai/api/v1/chat/completions"
            payload = json.dumps({
                "model": bot["model"],
                "messages": [{"role": "user", "content": prompt}],
                "temperature": bot.get("temperature", 0.35)
            }).encode()

        req = urllib.request.Request(url, data=payload)
        req.add_header("Content-Type", "application/json")
        if bot["provider"] == "openrouter":
            req.add_header("Authorization", f"Bearer {OR_KEY}")

        with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT if bot["provider"] == "ollama" else 10) as r:
            res = json.loads(r.read().decode())
            return res["response"] if bot["provider"] == "ollama" else res["choices"][0]["message"]["content"]

def get_model_decision(bot, prompt):
    first = request_model_text(bot, prompt)
    decision = extract_decision(first)
    if decision:
        return decision

    forced_prompt = (
        prompt
        + "\nYou must choose exactly one token from this set: BUY, SELL."
        + "\nDo not output HOLD or explanation."
    )
    second = request_model_text(bot, forced_prompt)
    return extract_decision(second)

def reset_all_state():
    for b in ARENA_DATA.values():
        b["bal"] = 100.0
        b["pos"] = 0
        b["busy"] = False

    for b in BASKET_DATA.values():
        b["bal"] = 100.0
        b["pos"] = {s: 0.0 for s in SYMBOLS}
        b["busy"] = False

    LOGS.clear()
    LOGS.append("SYSTEM RESET: all bots back to $100")

def refresh_ollama_models():
    global OLLAMA_MODELS, OLLAMA_MODELS_TS
    now = time.time()
    if now - OLLAMA_MODELS_TS < 30 and OLLAMA_MODELS:
        return OLLAMA_MODELS
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2) as r:
            data = json.loads(r.read().decode())
            OLLAMA_MODELS = [m.get("name", "") for m in data.get("models", []) if m.get("name")]
            OLLAMA_MODELS_TS = now
    except:
        pass
    return OLLAMA_MODELS

def resolve_ollama_model(target):
    available = refresh_ollama_models()
    if not available:
        return target
    if target in available:
        return target

    aliases = {
        "qwen2.5-coder": ["qwen2.5-coder:latest", "qwen3.5:latest"],
        "deepseek-r1": ["deepseek-r1:latest", "deepseek-r1:8b", "qwen3.5:latest", "phi3:latest"],
        "llama3.2": ["llama3.2:latest", "llama3:latest"],
        "mistral": ["mistral:latest"],
        "gemma4": ["gemma4:latest", "gemma3:latest"]
    }

    base = target.split(":", 1)[0]
    if base in aliases:
        for candidate in aliases[base]:
            if candidate in available:
                return candidate

    family = [m for m in available if m == base or m.startswith(base + ":")]
    if not family:
        return None

    for m in family:
        if m.endswith(":latest"):
            return m
    return family[0]

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
                HISTORY.append(LIVE_PRICES["BTC"])
                if len(HISTORY) > 30: HISTORY.pop(0)
                for s in SYMBOLS:
                    BASKET_HISTORY[s].append(LIVE_PRICES[s])
                    if len(BASKET_HISTORY[s]) > 30: BASKET_HISTORY[s].pop(0)
        except: pass
        time.sleep(1)

def call_model(name):
    b = ARENA_DATA[name]
    try:
        prompt = (
            f"BTC values: {HISTORY[-10:]}. "
            "Choose direction for the next tick. "
            "Respond with exactly one token: BUY or SELL."
        )
        decision = get_model_decision(b, prompt)
        if not decision:
            LOGS.append(f"{name}: HOLD")
            return

        p = LIVE_PRICES["BTC"]
        equity = b["bal"] + (b["pos"] * p)
        if equity <= 0 or p <= 0:
            LOGS.append(f"{name}: HOLD")
            return

        current_notional = abs(b["pos"]) * p
        max_notional = max(20.0, equity * MAX_LEVERAGE)
        trade_notional = max(MIN_TRADE_NOTIONAL, equity * TRADE_NOTIONAL_PCT)
        open_notional = min(trade_notional, max_notional - current_notional)
        trade_qty = open_notional / p if open_notional > 0 else 0.0

        if decision == "BUY":
            if b["pos"] < 0:
                cover_qty = min(abs(b["pos"]), trade_notional / p)
                if cover_qty > 0:
                    b["bal"] -= cover_qty * p
                    b["pos"] += cover_qty
                    LOGS.append(f"{name}: COVERED")
                else:
                    LOGS.append(f"{name}: HOLD")
            elif trade_qty > 0:
                b["bal"] -= open_notional
                b["pos"] += trade_qty
                LOGS.append(f"{name}: BOUGHT")
            else:
                LOGS.append(f"{name}: HOLD")
        elif decision == "SELL":
            if b["pos"] > 0:
                sell_qty = min(b["pos"], trade_notional / p)
                if sell_qty > 0:
                    b["bal"] += sell_qty * p
                    b["pos"] -= sell_qty
                    LOGS.append(f"{name}: SOLD")
                else:
                    LOGS.append(f"{name}: HOLD")
            elif trade_qty > 0:
                b["bal"] += open_notional
                b["pos"] -= trade_qty
                LOGS.append(f"{name}: SHORTED")
            else:
                LOGS.append(f"{name}: HOLD")
    except Exception as e:
        LOGS.append(f"{name}: error {str(e)[:40]}")

def run_bot_cycle(name):
    try:
        call_model(name)
    finally:
        ARENA_DATA[name]["busy"] = False

def arena_loop():
    global ARENA_IDX
    while True:
        if ARENA_ORDER:
            name = ARENA_ORDER[ARENA_IDX % len(ARENA_ORDER)]
            ARENA_IDX += 1
            if not ARENA_DATA[name]["busy"]:
                ARENA_DATA[name]["busy"] = True
                run_bot_cycle(name)
        if len(LOGS) > 120: LOGS.pop(0)
        time.sleep(ARENA_CYCLE_SECONDS)

def call_basket_model(name):
    b = BASKET_DATA[name]
    try:
        hist_summary = ", ".join(f"{s}: {BASKET_HISTORY[s][-5:]}" for s in SYMBOLS if BASKET_HISTORY[s])
        prompt = (
            f"Crypto basket prices (last 5 ticks): {hist_summary}. "
            "Choose basket direction for the next tick. "
            "Respond with exactly one token: BUY or SELL."
        )
        decision = get_model_decision(b, prompt)
        if not decision:
            LOGS.append(f"BASKET {name}: HOLD")
            return

        equity = b["bal"] + sum(b["pos"][s] * LIVE_PRICES[s] for s in SYMBOLS)
        if equity <= 0: return
        trade_notional = max(MIN_TRADE_NOTIONAL, equity * TRADE_NOTIONAL_PCT)
        per_sym_notional = trade_notional / len(SYMBOLS)

        action = "HOLD"
        for s in SYMBOLS:
            p = LIVE_PRICES[s]
            if p <= 0: continue
            max_notional = max(20.0 / len(SYMBOLS), equity * MAX_LEVERAGE / len(SYMBOLS))
            open_notional = min(per_sym_notional, max_notional - abs(b["pos"][s]) * p)
            trade_qty = open_notional / p if open_notional > 0 else 0.0
            if decision == "BUY":
                if b["pos"][s] < 0:
                    cover_qty = min(abs(b["pos"][s]), per_sym_notional / p)
                    if cover_qty > 0:
                        b["bal"] -= cover_qty * p
                        b["pos"][s] += cover_qty
                        action = "COVERED"
                elif trade_qty > 0:
                    b["bal"] -= open_notional
                    b["pos"][s] += trade_qty
                    action = "BOUGHT"
            elif decision == "SELL":
                if b["pos"][s] > 0:
                    sell_qty = min(b["pos"][s], per_sym_notional / p)
                    if sell_qty > 0:
                        b["bal"] += sell_qty * p
                        b["pos"][s] -= sell_qty
                        action = "SOLD"
                elif trade_qty > 0:
                    b["bal"] += open_notional
                    b["pos"][s] -= trade_qty
                    action = "SHORTED"
        LOGS.append(f"BASKET {name}: {action}")
    except Exception as e:
        LOGS.append(f"BASKET {name}: error {str(e)[:40]}")
    finally:
        b["busy"] = False

def basket_loop():
    global BASKET_IDX
    while True:
        if BASKET_ORDER:
            name = BASKET_ORDER[BASKET_IDX % len(BASKET_ORDER)]
            BASKET_IDX += 1
            if not BASKET_DATA[name]["busy"]:
                BASKET_DATA[name]["busy"] = True
                call_basket_model(name)
        time.sleep(ARENA_CYCLE_SECONDS)

class H(http.server.SimpleHTTPRequestHandler):
    def do_POST(self):
        if self.path == '/reset':
            reset_all_state()
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path == '/data':
            for b in ARENA_DATA.values(): b["total"] = b["bal"] + (b["pos"] * LIVE_PRICES["BTC"])
            for b in BASKET_DATA.values(): b["total"] = b["bal"] + sum(b["pos"][s] * LIVE_PRICES[s] for s in SYMBOLS)
            self.send_response(200); self.send_header('Content-type','application/json'); self.end_headers()
            self.wfile.write(json.dumps({"prices":LIVE_PRICES, "bots":ARENA_DATA, "basket_bots":BASKET_DATA, "logs":LOGS}).encode())
        else:
            self.send_response(200); self.send_header('Content-type','text/html'); self.end_headers()
            self.wfile.write(HTML.encode())

class ReuseTCPServer(socketserver.TCPServer):
    allow_reuse_address = True

HTML = """
<!DOCTYPE html><html><head><style>
    body { background:#0b0e11; color:#fff; font-family:sans-serif; text-align:center; padding:10px; margin:0; }
    .sidebar { position:fixed; left:0; top:0; height:100vh; width:180px; background:#11151a; border-right:1px solid #2b2f36; padding:16px; box-sizing:border-box; text-align:left; }
    .content { margin-left:190px; }
    .btn { width:100%; background:#f0b90b; color:#111; border:0; border-radius:8px; padding:10px; font-weight:700; cursor:pointer; }
    .btn:active { transform:translateY(1px); }
    .side-note { margin-top:10px; font-size:11px; color:#848e9c; line-height:1.4; }
    .grid { display:flex; justify-content:center; gap:8px; flex-wrap:wrap; margin-bottom:20px; }
    .card { background:#181a20; border-radius:10px; padding:12px; border-bottom:4px solid; width:170px; }
    .total { font-size:24px; font-weight:900; font-family:monospace; color:#f0b90b; }
    .summary { background:#1e2329; border-radius:10px; padding:15px; border:2px solid #02c076; width:220px; }
</style></head><body>
    <aside class="sidebar">
        <div style="font-size:13px; font-weight:700; margin-bottom:10px; color:#f0b90b;">CONTROLS</div>
        <button class="btn" onclick="resetArena()">Reset To $100</button>
        <div id="resetStatus" class="side-note"></div>
    </aside>
    <main class="content">
        <h3 style="color:#f0b90b">SYSTEM 1: AI ARENA (OLLAMA + CLOUD)</h3>
        <div id="ba" class="grid"></div>
        <hr style="border:0; border-top:1px solid #2b2f36; margin:20px 0;">
        <h3 style="color:#02c076">SYSTEM 2: CRYPTO BASKET</h3>
        <div id="ca" class="grid"></div>
        <div id="logs" style="font-size:10px; color:#848e9c; margin-top:10px;"></div>
    </main>
    <script>
        async function resetArena() {
            try {
                const r = await fetch('/reset', { method: 'POST' });
                if (r.ok) {
                    document.getElementById('resetStatus').innerText = 'Reset complete';
                    await update();
                } else {
                    document.getElementById('resetStatus').innerText = 'Reset failed';
                }
            } catch(e) {
                document.getElementById('resetStatus').innerText = 'Reset failed';
            }
        }

        async function update() {
            try {
                const r = await fetch('/data'); const d = await r.json();
                let bH="", bS=0;
                Object.entries(d.bots).forEach(([n,b])=>{
                    bH+=`<div class="card" style="border-color:${b.color}">
                        <div style="font-size:9px;opacity:0.5">${b.provider}</div>
                        <div style="color:${b.color};font-weight:bold">${n}</div>
                        <div class="total">$${b.total.toFixed(2)}</div>
                    </div>`;
                    bS+=b.total;
                });
                bH+=`<div class="summary"><div style="font-size:24px;color:#02c076">$${bS.toFixed(2)}</div><div>ARENA TOTAL</div></div>`;
                document.getElementById('ba').innerHTML=bH;

                let cH="", cS=0;
                Object.entries(d.basket_bots).forEach(([n,b])=>{
                    cH+=`<div class="card" style="border-color:${b.color}">
                        <div style="font-size:9px;opacity:0.5">${b.provider}</div>
                        <div style="color:${b.color};font-weight:bold">${n}</div>
                        <div class="total">$${b.total.toFixed(2)}</div>
                    </div>`;
                    cS+=b.total;
                });
                cH+=`<div class="summary"><div style="font-size:24px;color:#02c076">$${cS.toFixed(2)}</div><div>BASKET TOTAL</div></div>`;
                document.getElementById('ca').innerHTML=cH;
                document.getElementById('logs').innerText = d.logs.join(" | ");
            } catch(e) {}
        }
        // Use a Worker for the timer so the browser never throttles it
        const timerWorker = new Worker(URL.createObjectURL(new Blob(
            ['setInterval(()=>postMessage(1),1000)'],
            {type:'application/javascript'}
        )));
        timerWorker.onmessage = update;
        document.addEventListener('visibilitychange', () => { if (!document.hidden) update(); });
    </script></body></html>
"""
threading.Thread(target=fetch_binance, daemon=True).start()
threading.Thread(target=arena_loop, daemon=True).start()
threading.Thread(target=basket_loop, daemon=True).start()
ReuseTCPServer(("", PORT), H).serve_forever()
