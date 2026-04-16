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
B_KEY = ENV.get('BINANCE_API_KEY', '')
OR_KEY = ENV.get('OPENROUTER_API_KEY', '')

PORT = 8000
SYMBOLS = {"BTC": "BTCUSDT"}
LIVE_PRICES = {"BTC": 0.0}
HISTORY = []
LOGS = []

# Map your bots to real OpenRouter model strings
ARENA_DATA = {
    "GPT-4o": {"bal": 100.0, "pos": 0, "color": "#10a37f", "model": "openai/gpt-4o-mini"},
    "Claude 3.5": {"bal": 100.0, "pos": 0, "color": "#d97757", "model": "anthropic/claude-3.5-sonnet"},
    "Llama 3.1": {"bal": 100.0, "pos": 0, "color": "#0668E1", "model": "meta-llama/llama-3.1-8b-instruct"},
    "DeepSeek V3": {"bal": 100.0, "pos": 0, "color": "#67e8f9", "model": "deepseek/deepseek-chat"}
}

def fetch_binance():
    while True:
        try:
            url = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
            req = urllib.request.Request(url)
            if B_KEY: req.add_header('X-MBX-APIKEY', B_KEY)
            with urllib.request.urlopen(req) as r:
                data = json.loads(r.read().decode())
                LIVE_PRICES["BTC"] = float(data['price'])
                HISTORY.append(LIVE_PRICES["BTC"])
                if len(HISTORY) > 30: HISTORY.pop(0)
        except: pass
        time.sleep(1)

def call_openrouter(bot_name):
    if not OR_KEY: return
    b = ARENA_DATA[bot_name]
    try:
        prompt = f"BTC Price History (1s intervals): {HISTORY}. Decide: BUY, SELL, or HOLD. Reply only with the word."
        payload = json.dumps({
            "model": b["model"],
            "messages": [{"role": "user", "content": prompt}]
        }).encode()
        
        req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=payload)
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {OR_KEY}")
        
        with urllib.request.urlopen(req, timeout=10) as r:
            res = json.loads(r.read().decode())
            decision = res['choices'][0]['message']['content'].strip().upper()
            
            p = LIVE_PRICES["BTC"]
            size = 0.0001
            if "BUY" in decision and b["bal"] > (size * p):
                b["bal"] -= (size * p); b["pos"] += size
                LOGS.append(f"{bot_name}: BOUGHT")
            elif "SELL" in decision and b["pos"] >= size:
                b["bal"] += (size * p); b["pos"] -= size
                LOGS.append(f"{bot_name}: SOLD")
    except Exception as e:
        LOGS.append(f"{bot_name} Err: {str(e)[:15]}")

def arena_loop():
    while True:
        if len(HISTORY) > 10:
            for name in ARENA_DATA:
                threading.Thread(target=call_openrouter, args=(name,)).start()
                time.sleep(2) # Stagger calls to prevent rate limits
        if len(LOGS) > 5: LOGS.pop(0)
        time.sleep(20)

class H(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/data':
            for b in ARENA_DATA.values(): b["total"] = b["bal"] + (b["pos"] * LIVE_PRICES["BTC"])
            self.send_response(200); self.send_header('Content-type','application/json'); self.end_headers()
            self.wfile.write(json.dumps({"prices":LIVE_PRICES, "bots":ARENA_DATA, "logs":LOGS}).encode())
        else:
            self.send_response(200); self.send_header('Content-type','text/html'); self.end_headers()
            self.wfile.write(HTML.encode())

HTML = """
<!DOCTYPE html><html><head><style>
    body { background:#0b0e11; color:#fff; font-family:sans-serif; text-align:center; padding:10px; }
    .grid { display:flex; justify-content:center; gap:10px; flex-wrap:wrap; }
    .card { background:#181a20; border-radius:12px; padding:15px; border-bottom:5px solid; width:180px; }
    .total { font-size:28px; font-weight:900; font-family:monospace; color:#f0b90b; }
    .log { font-size:10px; color:#848e9c; margin-top:20px; font-family:monospace; }
</style></head><body>
    <h2 style="color:#f0b90b">MULTI-LLM OPENROUTER ARENA</h2>
    <div id="ba" class="grid"></div>
    <div class="log" id="logs"></div>
    <script>
        async function update() {
            try {
                const r = await fetch('/data'); const d = await r.json();
                let h="";
                Object.entries(d.bots).forEach(([n,b])=>{
                    h+=`<div class="card" style="border-color:${b.color}">
                        <div style="font-size:10px;opacity:0.6">${b.model}</div>
                        <div style="color:${b.color};font-weight:bold">${n}</div>
                        <div class="total">$${b.total.toFixed(2)}</div>
                        <div style="font-size:11px">Pos: ${b.pos.toFixed(4)}</div>
                    </div>`;
                });
                document.getElementById('ba').innerHTML=h;
                document.getElementById('logs').innerHTML=d.logs.join(" | ");
            } catch(e) {}
        }
        setInterval(update, 1000);
    </script></body></html>
"""
threading.Thread(target=fetch_binance, daemon=True).start()
threading.Thread(target=arena_loop, daemon=True).start()
socketserver.TCPServer(("", PORT), H).serve_forever()
