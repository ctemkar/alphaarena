import http.server
import socketserver
import json
import threading
import time
import random

PORT = 8000
PRICES = {"BTC": 74641.10, "ETH": 2355.02, "SOL": 84.99, "XRP": 1.40, "BNB": 625.20, "DOGE": 0.10}

ARENA_DATA = {
    "GPT-5.2": {"bal": 100.00, "pos": 0, "color": "#10a37f", "tag": "Balanced"},
    "Claude 4.6": {"bal": 100.00, "pos": 0, "color": "#d97757", "tag": "Safety"},
    "Llama 4": {"bal": 100.00, "pos": 0, "color": "#0668E1", "tag": "Speed"},
    "Gemini 3": {"bal": 100.00, "pos": 0, "color": "#4285F4", "tag": "Context"},
    "DeepSeek R2": {"bal": 100.00, "pos": 0, "color": "#67e8f9", "tag": "Reason"},
    "Qwen 3": {"bal": 100.00, "pos": 0, "color": "#6366f1", "tag": "Pattern"},
    "Grok 4.2": {"bal": 100.00, "pos": 0, "color": "#ffffff", "tag": "Real-Time"}
}

def engine():
    while True:
        for c in PRICES: PRICES[c] *= (1 + random.uniform(-0.0005, 0.0005))
        for n, b in ARENA_DATA.items():
            mv = random.random()
            if n in ["Grok 4.2", "Llama 4"]:
                if mv > 0.6: b["pos"] += 0.0005; b["bal"] -= (PRICES["BTC"] * 0.0005)
                elif mv < 0.4: b["pos"] -= 0.0005; b["bal"] += (PRICES["BTC"] * 0.0005)
            else:
                if mv > 0.95: b["pos"] += 0.001; b["bal"] -= (PRICES["BTC"] * 0.001)
                elif mv < 0.05: b["pos"] -= 0.001; b["bal"] += (PRICES["BTC"] * 0.001)
            b["total"] = b["bal"] + (b["pos"] * PRICES["BTC"])
        time.sleep(1)

class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/data':
            self.send_response(200); self.send_header('Content-type','application/json'); self.end_headers()
            self.wfile.write(json.dumps({"prices": PRICES, "bots": ARENA_DATA}).encode())
        else:
            self.send_response(200); self.send_header('Content-type','text/html'); self.end_headers()
            self.wfile.write(HTML.encode())

HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>2026 Modular Arena</title>
    <style>
        body { background:#0b0e11; color:#fff; font-family:sans-serif; padding:20px; text-align:center; }
        .controls { background:#1e2329; padding:15px; border-radius:10px; margin-bottom:20px; display:flex; flex-wrap:wrap; justify-content:center; gap:10px; border:1px solid #2b2f36; }
        .section-h { color:#f0b90b; margin: 20px 0 10px; font-size: 20px; text-transform: uppercase; }
        .grid { display: flex; flex-wrap: wrap; justify-content: center; gap: 15px; max-width: 1400px; margin: 0 auto; }
        .card { background:#181a20; border-radius:12px; padding:20px; border-bottom: 5px solid; width: 220px; }
        .summary-card { background:#2b2f36; border-radius:12px; padding:20px; border-bottom: 5px solid #02c076; width: 220px; }
        .total { font-size: 38px; font-weight: 900; margin: 5px 0; font-family: monospace; }
        .avg { font-size: 24px; color: #02c076; font-weight: bold; }
        .label { font-size: 16px; font-weight: bold; }
        label { cursor:pointer; font-size: 12px; padding: 5px; border: 1px solid #444; border-radius: 5px; }
    </style>
</head>
<body>
    <div class="controls" id="ctrl-panel"></div>
    
    <div class="section-h">System 1: LLM Arena</div>
    <div class="grid" id="bot-grid"></div>

    <div class="section-h" style="color:#02c076">System 2: Crypto Basket</div>
    <div class="grid" id="coin-grid"></div>

    <script>
        let activeBots = ["GPT-5.2", "Claude 4.6", "Llama 4", "Gemini 3", "DeepSeek R2", "Qwen 3", "Grok 4.2"];
        let activeCoins = ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE"];

        function initControls() {
            const panel = document.getElementById('ctrl-panel');
            panel.innerHTML = '<b>Models:</b> ' + activeBots.map(n => `<label><input type="checkbox" checked onchange="toggle('bot', '${n}')"> ${n}</label>`).join('') + 
                             '<b style="margin-left:20px">Coins:</b> ' + activeCoins.map(n => `<label><input type="checkbox" checked onchange="toggle('coin', '${n}')"> ${n}</label>`).join('');
        }

        function toggle(type, name) {
            if(type === 'bot') activeBots.includes(name) ? activeBots = activeBots.filter(x => x!==name) : activeBots.push(name);
            else activeCoins.includes(name) ? activeCoins = activeCoins.filter(x => x!==name) : activeCoins.push(name);
        }

        async function update() {
            const r = await fetch('/data'); const d = await r.json();
            
            // System 1
            let botTotal = 0; let bCount = 0;
            let botHtml = Object.entries(d.bots).filter(([n]) => activeBots.includes(n)).map(([n, b]) => {
                botTotal += b.total; bCount++;
                return `<div class="card" style="border-color:${b.color}"><div class="label">${n}</div><div class="total">$${b.total.toFixed(2)}</div></div>`;
            }).join('');
            if(bCount > 0) botHtml += `<div class="summary-card"><div class="label">LLM SUMMARY</div><div class="total">$${botTotal.toFixed(2)}</div><div class="avg">AVG: $${(botTotal/bCount).toFixed(2)}</div></div>`;
            document.getElementById('bot-grid').innerHTML = botHtml;

            // System 2
            let coinTotal = 0; let cCount = 0;
            let coinHtml = Object.entries(d.prices).filter(([n]) => activeCoins.includes(n)).map(([n, p]) => {
                let val = (100/6) * (p / d.prices[n]); // Normalized to $100 start
                coinTotal += val; cCount++;
                return `<div class="card" style="border-color:#02c076"><div class="label">${n}</div><div class="total">$${val.toFixed(2)}</div></div>`;
            }).join('');
            if(cCount > 0) coinHtml += `<div class="summary-card"><div class="label">BASKET SUMMARY</div><div class="total">$${coinTotal.toFixed(2)}</div><div class="avg">AVG: $${(coinTotal/cCount).toFixed(2)}</div></div>`;
            document.getElementById('coin-grid').innerHTML = coinHtml;
        }
        initControls(); setInterval(update, 1000);
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    threading.Thread(target=engine, daemon=True).start()
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        httpd.allow_reuse_address = True
        httpd.serve_forever()
