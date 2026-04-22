import http.server
import socketserver
import json
import threading
import time
import urllib.request
import hmac
import hashlib
import os

# Load .env manually to avoid dependencies
def load_env():
    env_vars = {}
    if os.path.exists('.env'):
        with open('.env') as f:
            for line in f:
                if '=' in line and not line.startswith('#'):
                    k, v = line.strip().split('=', 1)
                    env_vars[k] = v
    return env_vars

ENV = load_env()
API_KEY = ENV.get('BINANCE_API_KEY', '')
API_SECRET = ENV.get('BINANCE_SECRET', '')

PORT = 8000
SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT", "BNB": "BNBUSDT", "DOGE": "DOGEUSDT"}
LIVE_PRICES = {s: 0.0 for s in SYMBOLS}
START_PRICES = {s: 0.0 for s in SYMBOLS}
LOGS = []

ARENA_DATA = {
    "GPT-5.2": {"bal": 100.00, "pos": 0, "color": "#10a37f"},
    "Claude 4.6": {"bal": 100.00, "pos": 0, "color": "#d97757"},
    "Llama 4": {"bal": 100.00, "pos": 0, "color": "#0668E1"},
    "Gemini 3": {"bal": 100.00, "pos": 0, "color": "#4285F4"},
    "DeepSeek R2": {"bal": 100.00, "pos": 0, "color": "#67e8f9"},
    "Qwen 3": {"bal": 100.00, "pos": 0, "color": "#6366f1"},
    "Grok 4.2": {"bal": 100.00, "pos": 0, "color": "#ffffff"}
}

def log_event(msg):
    global LOGS
    LOGS.append(f"[{time.strftime('%H:%M:%S')}] {msg}")
    if len(LOGS) > 3: LOGS.pop(0)

def fetch_live_prices():
    global START_PRICES
    url = "https://api.binance.com/api/3/ticker/price"
    while True:
        try:
            req = urllib.request.Request(url)
            if API_KEY:
                req.add_header('X-MBX-APIKEY', API_KEY)
            
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode())
                # Handle both list and single object responses from Binance
                if isinstance(data, list):
                    temp = {item['symbol']: float(item['price']) for item in data}
                else:
                    temp = {data['symbol']: float(data['price'])}
                
                for s, pair in SYMBOLS.items():
                    if pair in temp:
                        LIVE_PRICES[s] = temp[pair]
                        if START_PRICES[s] == 0.0: START_PRICES[s] = LIVE_PRICES[s]
            log_event("Authenticated API Sync: OK")
        except Exception as e:
            log_event(f"Key Error: {str(e)}")
        time.sleep(1)

def trade_logic():
    while True:
        if LIVE_PRICES["BTC"] > 0:
            for n, b in ARENA_DATA.items():
                b["total"] = b["bal"] + (b["pos"] * LIVE_PRICES["BTC"])
        time.sleep(1)

class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/data':
            bv = {s: (100/6) * (LIVE_PRICES[s]/START_PRICES[s]) if START_PRICES[s] > 0 else 16.66 for s in SYMBOLS}
            self.send_response(200); self.send_header('Content-type', 'application/json'); self.end_headers()
            self.wfile.write(json.dumps({"prices": LIVE_PRICES, "bots": ARENA_DATA, "basket_vals": bv, "logs": LOGS, "key_active": bool(API_KEY)}).encode())
        else:
            self.send_response(200); self.send_header('Content-type', 'text/html'); self.end_headers()
            self.wfile.write(HTML.encode())

HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>2026 PRO ARENA</title>
    <style>
        body { background:#0b0e11; color:#fff; font-family:sans-serif; padding:15px; text-align:center; }
        .grid { display: flex; flex-wrap: wrap; justify-content: center; gap: 10px; margin-bottom: 20px; }
        .card { background:#181a20; border-radius:10px; padding:15px; border-bottom: 4px solid; width: 170px; cursor: pointer; }
        .card.small { width: 100px; padding: 8px; opacity: 0.25; border-bottom-width: 2px; }
        .total { font-size: 26px; font-weight: 900; font-family: monospace; }
        .summary { background:#1e2329; border-radius:10px; padding:15px; border: 2px solid #02c076; width: 210px; }
        #status { font-size: 10px; margin-bottom: 10px; padding: 5px; border-radius: 5px; display: inline-block; }
    </style>
</head>
<body>
    <div id="status">Checking .env...</div>
    <h2 style="color:#f0b90b">SYSTEM 1: AI MODELS</h2>
    <div id="bi" class="grid"></div><div id="ba" class="grid"></div>
    <hr style="border:0; border-top:1px solid #2b2f36; margin:20px 0;">
    <h2 style="color:#02c076">SYSTEM 2: CRYPTO BASKET</h2>
    <div id="ci" class="grid"></div><div id="ca" class="grid"></div>

    <script>
        let aB = ["GPT-5.2", "Grok 4.2", "Gemini 3", "Llama 4", "Claude 4.6", "DeepSeek R2", "Qwen 3"];
        let aC = ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE"];
        function toggle(t, n) {
            let l = (t==='b')?aB:aC;
            l.includes(n) ? l.splice(l.indexOf(n),1) : l.push(n);
        }
        async function update() {
            try {
                const r = await fetch('/data'); const d = await r.json();
                const stat = document.getElementById('status');
                stat.innerHTML = (d.key_active ? "✅ API KEY ACTIVE" : "⚠️ PUBLIC MODE") + " | " + d.logs.join(" | ");
                stat.style.background = d.key_active ? "#02c07644" : "#f8496044";
                
                let bAct="", bIn="", bS=0;
                Object.entries(d.bots).forEach(([n,b])=>{
                    const isA=aB.includes(n);
                    const h=`<div class="card ${isA?'':'small'}" style="border-color:${b.color}" onclick="toggle('b','${n}')"><div style="font-size:11px;color:${b.color}">${n}</div><div class="total">$${(b.total||100).toFixed(2)}</div></div>`;
                    if(isA){bAct+=h; bS+=b.total;} else bIn+=h;
                });
                if(aB.length>0) bAct+=`<div class="summary"><div style="font-size:32px;font-weight:900;color:#02c076">$${bS.toFixed(2)}</div><div style="font-size:14px;color:#f0b90b">AVG: $${(bS/aB.length).toFixed(2)}</div></div>`;
                document.getElementById('ba').innerHTML=bAct; document.getElementById('bi').innerHTML=bIn;

                let cAct="", cIn="", cS=0;
                Object.entries(d.basket_vals).forEach(([n,v])=>{
                    const isA=aC.includes(n);
                    const pr = d.prices[n] || 0;
                    const h=`<div class="card ${isA?'':'small'}" style="border-color:#02c076" onclick="toggle('c','${n}')"><div style="font-size:11px;color:#02c076">${n}</div><div class="total">$${v.toFixed(2)}</div><div style="font-size:10px">$${pr.toFixed(2)}</div></div>`;
                    if(isA){cAct+=h; cS+=v;} else cIn+=h;
                });
                if(aC.length>0) cAct+=`<div class="summary"><div style="font-size:32px;font-weight:900;color:#02c076">$${cS.toFixed(2)}</div><div style="font-size:14px;color:#f0b90b">AVG: $${(cS/aC.length).toFixed(2)}</div></div>`;
                document.getElementById('ca').innerHTML=cAct; document.getElementById('ci').innerHTML=cIn;
            } catch(e) {}
        }
        setInterval(update, 1000);
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    threading.Thread(target=fetch_live_prices, daemon=True).start()
    threading.Thread(target=trade_logic, daemon=True).start()
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        httpd.allow_reuse_address = True
        httpd.serve_forever()
