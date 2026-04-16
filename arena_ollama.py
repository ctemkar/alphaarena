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
SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT", "BNB": "BNBUSDT", "DOGE": "DOGEUSDT"}
LIVE_PRICES = {s: 0.0 for s in SYMBOLS}
START_PRICES = {s: 0.0 for s in SYMBOLS}
HISTORY = []
LOGS = []

ARENA_DATA = {
    "GPT-4o": {"bal": 100.0, "pos": 0, "color": "#10a37f", "provider": "openrouter", "model": "openai/gpt-4o-mini"},
    "Claude 3.5": {"bal": 100.0, "pos": 0, "color": "#d97757", "provider": "openrouter", "model": "anthropic/claude-3.5-sonnet"},
    "Llama 3.1": {"bal": 100.0, "pos": 0, "color": "#0668E1", "provider": "ollama", "model": "llama3.1"},
    "DeepSeek": {"bal": 100.0, "pos": 0, "color": "#67e8f9", "provider": "ollama", "model": "deepseek-v3"},
    "Qwen 2.5": {"bal": 100.0, "pos": 0, "color": "#6366f1", "provider": "ollama", "model": "qwen2.5"}
}

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
        prompt = f"BTC values: {HISTORY[-10:]}. Reply only: BUY, SELL, or HOLD."
        if b["provider"] == "ollama":
            url = "http://localhost:11434/api/generate"
            payload = json.dumps({"model": b["model"], "prompt": prompt, "stream": False}).encode()
        else:
            if not OR_KEY: return
            url = "https://openrouter.ai/api/v1/chat/completions"
            payload = json.dumps({"model": b["model"], "messages": [{"role": "user", "content": prompt}]}).encode()
        
        req = urllib.request.Request(url, data=payload)
        req.add_header("Content-Type", "application/json")
        if b["provider"] == "openrouter": req.add_header("Authorization", f"Bearer {OR_KEY}")
        
        with urllib.request.urlopen(req, timeout=5) as r:
            res = json.loads(r.read().decode())
            text = res['response'] if b["provider"] == "ollama" else res['choices'][0]['message']['content']
            decision = text.strip().upper()
            
            p = LIVE_PRICES["BTC"]
            size = 0.0001
            if "BUY" in decision and b["bal"] > (size * p):
                b["bal"] -= (size * p); b["pos"] += size
                LOGS.append(f"{name}: BOUGHT")
            elif "SELL" in decision and b["pos"] >= size:
                b["bal"] += (size * p); b["pos"] -= size
                LOGS.append(f"{name}: SOLD")
    except: pass

def arena_loop():
    while True:
        if len(HISTORY) > 5:
            for name in ARENA_DATA:
                threading.Thread(target=call_model, args=(name,)).start()
                time.sleep(1)
        if len(LOGS) > 4: LOGS.pop(0)
        time.sleep(15)

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
socketserver.TCPServer(("", PORT), H).serve_forever()
