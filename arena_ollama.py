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
OLLAMA_TIMEOUT = 2
ARENA_CYCLE_SECONDS = 1
MAX_LEVERAGE = 8
TRADE_NOTIONAL_PCT = 0.35
MIN_TRADE_NOTIONAL = 12.0
SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT", "BNB": "BNBUSDT", "DOGE": "DOGEUSDT"}
LIVE_PRICES = {s: 0.0 for s in SYMBOLS}
START_PRICES = {s: 0.0 for s in SYMBOLS}
HISTORY = []
LOGS = []
OLLAMA_MODELS = []
OLLAMA_MODELS_TS = 0.0

ARENA_DATA = {
    "Qwen2.5-Coder (7B)": {"bal": 100.0, "pos": 0, "color": "#6366f1", "provider": "ollama", "model": "qwen2.5-coder:7b", "busy": False},
    "DeepSeek-R1 (8B)": {"bal": 100.0, "pos": 0, "color": "#67e8f9", "provider": "ollama", "model": "deepseek-r1:8b", "busy": False},
    "Llama 3.2 (3B)": {"bal": 100.0, "pos": 0, "color": "#10a37f", "provider": "ollama", "model": "llama3.2:3b", "busy": False},
    "Mistral (7B)": {"bal": 100.0, "pos": 0, "color": "#f0b90b", "provider": "ollama", "model": "mistral:7b", "busy": False},
    "Gemma 4": {"bal": 100.0, "pos": 0, "color": "#34d399", "provider": "ollama", "model": "gemma4:latest", "busy": False}
}

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
        except: pass
        time.sleep(1)

def call_model(name):
    b = ARENA_DATA[name]
    try:
        prompt = f"BTC values: {HISTORY[-10:]}. Respond with exactly one token: BUY or SELL or HOLD."
        if b["provider"] == "ollama":
            model = resolve_ollama_model(b["model"])
            if not model:
                LOGS.append(f"{name}: model missing ({b['model']})")
                return
            url = "http://localhost:11434/api/generate"
            payload = json.dumps({
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": 4, "temperature": 0}
            }).encode()
        else:
            if not OR_KEY: return
            url = "https://openrouter.ai/api/v1/chat/completions"
            payload = json.dumps({"model": b["model"], "messages": [{"role": "user", "content": prompt}]}).encode()
        
        req = urllib.request.Request(url, data=payload)
        req.add_header("Content-Type", "application/json")
        if b["provider"] == "openrouter": req.add_header("Authorization", f"Bearer {OR_KEY}")
        
        with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT if b["provider"] == "ollama" else 10) as r:
            res = json.loads(r.read().decode())
            text = res['response'] if b["provider"] == "ollama" else res['choices'][0]['message']['content']
            upper = text.strip().upper()
            if "BUY" in upper:
                decision = "BUY"
            elif "SELL" in upper:
                decision = "SELL"
            else:
                decision = "BUY" if len(HISTORY) < 2 or HISTORY[-1] >= HISTORY[-2] else "SELL"

            # Force an action each tick using short momentum when model output is ambiguous.
            if len(HISTORY) >= 3 and decision not in ("BUY", "SELL"):
                decision = "BUY" if HISTORY[-1] >= HISTORY[-3] else "SELL"

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
            else:
                # If one side cannot increase due to caps, flip side so we still execute this tick.
                if decision == "BUY":
                    b["bal"] += open_notional
                    b["pos"] -= trade_qty
                    LOGS.append(f"{name}: SHORTED")
                else:
                    b["bal"] -= open_notional
                    b["pos"] += trade_qty
                    LOGS.append(f"{name}: BOUGHT")
    except Exception as e:
        # On timeout/error, still execute one directional trade per tick.
        p = LIVE_PRICES["BTC"]
        if p > 0:
            decision = "BUY" if len(HISTORY) < 2 or HISTORY[-1] >= HISTORY[-2] else "SELL"
            equity = b["bal"] + (b["pos"] * p)
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
                elif trade_qty > 0:
                    b["bal"] -= open_notional
                    b["pos"] += trade_qty
                    LOGS.append(f"{name}: BOUGHT")
            else:
                if b["pos"] > 0:
                    sell_qty = min(b["pos"], trade_notional / p)
                    if sell_qty > 0:
                        b["bal"] += sell_qty * p
                        b["pos"] -= sell_qty
                        LOGS.append(f"{name}: SOLD")
                elif trade_qty > 0:
                    b["bal"] += open_notional
                    b["pos"] -= trade_qty
                    LOGS.append(f"{name}: SHORTED")
        else:
            LOGS.append(f"{name}: error {str(e)[:40]}")

def run_bot_cycle(name):
    try:
        call_model(name)
    finally:
        ARENA_DATA[name]["busy"] = False

def arena_loop():
    while True:
        if len(HISTORY) > 5:
            for name in ARENA_DATA:
                if not ARENA_DATA[name]["busy"]:
                    ARENA_DATA[name]["busy"] = True
                    threading.Thread(target=run_bot_cycle, args=(name,), daemon=True).start()
                time.sleep(0.05)
        if len(LOGS) > 120: LOGS.pop(0)
        time.sleep(ARENA_CYCLE_SECONDS)

class H(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/data':
            for b in ARENA_DATA.values(): b["total"] = b["bal"] + (b["pos"] * LIVE_PRICES["BTC"])
            bv = {s: (16.66 * (LIVE_PRICES[s]/START_PRICES[s])) if START_PRICES[s]>0 else 16.66 for s in SYMBOLS}
            self.send_response(200); self.send_header('Content-type','application/json'); self.end_headers()
            self.wfile.write(json.dumps({"prices":LIVE_PRICES, "bots":ARENA_DATA, "basket":bv, "logs":LOGS}).encode())
        else:
            self.send_response(200); self.send_header('Content-type','text/html'); self.end_headers()
            self.wfile.write(HTML.encode())

class ReuseTCPServer(socketserver.TCPServer):
    allow_reuse_address = True

HTML = """
<!DOCTYPE html><html><head><style>
    body { background:#0b0e11; color:#fff; font-family:sans-serif; text-align:center; padding:10px; margin:0; }
    .grid { display:flex; justify-content:center; gap:8px; flex-wrap:wrap; margin-bottom:20px; }
    .card { background:#181a20; border-radius:10px; padding:12px; border-bottom:4px solid; width:170px; }
    .total { font-size:24px; font-weight:900; font-family:monospace; color:#f0b90b; }
    .summary { background:#1e2329; border-radius:10px; padding:15px; border:2px solid #02c076; width:220px; }
</style></head><body>
    <h3 style="color:#f0b90b">SYSTEM 1: AI ARENA (OLLAMA + CLOUD)</h3>
    <div id="ba" class="grid"></div>
    <hr style="border:0; border-top:1px solid #2b2f36; margin:20px 0;">
    <h3 style="color:#02c076">SYSTEM 2: CRYPTO BASKET</h3>
    <div id="ca" class="grid"></div>
    <div id="logs" style="font-size:10px; color:#848e9c; margin-top:10px;"></div>
    <script>
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
                Object.entries(d.basket).forEach(([n,v])=>{
                    cH+=`<div class="card" style="border-color:#02c076"><div>${n}</div><div class="total">$${v.toFixed(2)}</div><div style="font-size:9px;color:#848e9c">$${d.prices[n].toFixed(2)}</div></div>`;
                    cS+=v;
                });
                cH+=`<div class="summary"><div style="font-size:24px;color:#02c076">$${cS.toFixed(2)}</div><div>BASKET TOTAL</div></div>`;
                document.getElementById('ca').innerHTML=cH;
                document.getElementById('logs').innerText = d.logs.join(" | ");
            } catch(e) {}
        }
        setInterval(update, 1000);
    </script></body></html>
"""
threading.Thread(target=fetch_binance, daemon=True).start()
threading.Thread(target=arena_loop, daemon=True).start()
ReuseTCPServer(("", PORT), H).serve_forever()
