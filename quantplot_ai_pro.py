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
    "GPT-4o": {"bal": 100.0, "pos": 0, "color": "#10a37f", "active": True, "p": "openrouter", "m": "openai/gpt-4o-mini"},
    "Claude 3.5": {"bal": 100.0, "pos": 0, "color": "#d97757", "active": True, "p": "openrouter", "m": "anthropic/claude-3.5-sonnet"},
    "Llama 3.1": {"bal": 100.0, "pos": 0, "color": "#0668E1", "active": False, "p": "ollama", "m": "llama3.1"},
    "DeepSeek": {"bal": 100.0, "pos": 0, "color": "#67e8f9", "active": False, "p": "ollama", "m": "deepseek-v3"},
    "Qwen 2.5": {"bal": 100.0, "pos": 0, "color": "#6366f1", "active": False, "p": "ollama", "m": "qwen2.5"}
}

BASKET_ACTIVE = ["BTC", "ETH", "SOL", "XRP", "DOGE"]

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
        p = LIVE_PRICES["BTC"]
        prompt = f"BTC: {HISTORY[-10:]}. Reply only: BUY, SELL, or HOLD."
        if b["p"] == "ollama":
            url = "http://localhost:11434/api/generate"
            payload = json.dumps({"model": b["m"], "prompt": prompt, "stream": False}).encode()
        else:
            if not OR_KEY: return
            url = "https://openrouter.ai/api/v1/chat/completions"
            payload = json.dumps({"model": b["m"], "messages": [{"role": "user", "content": prompt}]}).encode()
        
        req = urllib.request.Request(url, data=payload)
        req.add_header("Content-Type", "application/json")
        if b["p"] == "openrouter": req.add_header("Authorization", f"Bearer {OR_KEY}")
        
        with urllib.request.urlopen(req, timeout=5) as r:
            res = json.loads(r.read().decode())
            text = res['response'] if b["p"] == "ollama" else res['choices'][0]['message']['content']
            decision = text.strip().upper()
            
            # TRADE LOGIC
            trade_size_usd = 20.0 
            qty = trade_size_usd / p
            
            if "BUY" in decision and b["bal"] >= trade_size_usd:
                b["bal"] -= trade_size_usd
                b["pos"] += qty
                LOGS.append(f"{name}: BUY BTC")
            elif "SELL" in decision and b["pos"] > 0:
                b["bal"] += (b["pos"] * p)
                b["pos"] = 0
                LOGS.append(f"{name}: SOLD ALL BTC")
    except: pass

def arena_loop():
    while True:
        if len(HISTORY) > 5:
            for name, b in ARENA_DATA.items():
                if b["active"]:
                    threading.Thread(target=call_model, args=(name,)).start()
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
            self.wfile.write(json.dumps({"prices":LIVE_PRICES, "bots":ARENA_DATA, "basket":bv, "active_c": BASKET_ACTIVE, "logs":LOGS}).encode())
        elif self.path.startswith('/toggle?name='):
            n = self.path.split('=')[1]
            if n in ARENA_DATA:
                ARENA_DATA[n]["active"] = not ARENA_DATA[n]["active"]
            elif n in SYMBOLS:
                if n in BASKET_ACTIVE: BASKET_ACTIVE.remove(n)
                else: BASKET_ACTIVE.append(n)
            self.send_response(200); self.end_headers()
        else:
            self.send_response(200); self.send_header('Content-type','text/html'); self.end_headers()
            self.wfile.write(HTML.encode())

HTML = """
<!DOCTYPE html><html><head><style>
    body { background:#0b0e11; color:#fff; font-family:sans-serif; text-align:center; padding:10px; margin:0; }
    .row-label { font-size: 10px; color: #848e9c; text-transform: uppercase; margin: 10px 0; }
    .grid { display:flex; flex-wrap:wrap; justify-content:center; gap:8px; min-height:40px; }
    .card { background:#181a20; border-radius:8px; padding:12px; border-bottom:4px solid; width:160px; cursor:pointer; }
    .card.small { width: 90px; padding: 6px; opacity: 0.3; border-bottom-width: 2px; }
    .total { font-size:24px; font-weight:900; font-family:monospace; color:#f0b90b; }
    .summary { background:#1e2329; border-radius:10px; padding:12px; border:2px solid #02c076; width:200px; }
    #logs { font-size:12px; color:#f0b90b; font-family:monospace; margin-top:15px; background:#000; padding:5px; border-radius:5px; display:inline-block; min-width:300px; }
</style></head><body>
    <h3 style="color:#f0b90b; margin:5px;">SYSTEM 1: AI ARENA</h3>
    <div class="row-label">Selection Row</div><div id="bi" class="grid"></div>
    <div class="row-label">Active Row</div><div id="ba" class="grid"></div>
    <div id="logs">WAITING FOR FIRST TRADE...</div>
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
                Object.entries(d.bots).forEach(([n,b])=>{
                    const h=`<div class="card ${b.active?'':'small'}" style="border-color:${b.color}" onclick="tgl('${n}')">
                        <div style="font-size:9px;opacity:0.5">${b.p.toUpperCase()}</div><div style="color:${b.color}">${n}</div>
                        <div class="total">$${b.total.toFixed(2)}</div>
                        <div style="font-size:10px;opacity:0.4">Pos: ${b.pos.toFixed(5)}</div></div>`;
                    if(b.active){bAct+=h; bS+=b.total; bCount++;} else bIn+=h;
                });
                if(bCount>0) bAct+=`<div class="summary"><div style="font-size:24px;color:#02c076">$${bS.toFixed(2)}</div><div>ARENA TOTAL</div></div>`;
                document.getElementById('ba').innerHTML=bAct; document.getElementById('bi').innerHTML=bIn;
                
                let cAct="", cIn="", cS=0, cCount=0;
                Object.entries(d.basket).forEach(([n,v])=>{
                    const isA=d.active_c.includes(n);
                    const h=`<div class="card ${isA?'':'small'}" style="border-color:#02c076" onclick="tgl('${n}')">
                        <div>${n}</div><div class="total">$${v.toFixed(2)}</div></div>`;
                    if(isA){cAct+=h; cS+=v; cCount++;} else cIn+=h;
                });
                if(cCount>0) cAct+=`<div class="summary"><div style="font-size:24px;color:#02c076">$${cS.toFixed(2)}</div><div>BASKET TOTAL</div></div>`;
                document.getElementById('ca').innerHTML=cAct; document.getElementById('ci').innerHTML=cIn;
                if(d.logs.length > 0) document.getElementById('logs').innerText = d.logs.join(" | ");
            } catch(e) {}
        }
        setInterval(update, 1000);
    </script></body></html>
"""
threading.Thread(target=fetch_binance, daemon=True).start()
threading.Thread(target=arena_loop, daemon=True).start()
socketserver.TCPServer(("", PORT), H).serve_forever()
