import http.server
import socketserver
import json
import threading
import time
import random

PORT = 8000
PRICE = 74600.0

# THE 2026 ELITE SIX: $100.00 per model
ARENA_DATA = {
    "GPT-5.2": {"bal": 100.00, "pos": 0, "color": "#10a37f", "status": "Analyzing Trend..."},
    "Claude 4.6": {"bal": 100.00, "pos": 0, "color": "#d97757", "status": "Optimizing Logic..."},
    "Llama 4 Scout": {"bal": 100.00, "pos": 0, "color": "#0668E1", "status": "High-Freq Scalp..."},
    "Gemini 3 Pro": {"bal": 100.00, "pos": 0, "color": "#4285F4", "status": "Macro Analysis..."},
    "DeepSeek R2": {"bal": 100.00, "pos": 0, "color": "#67e8f9", "status": "Deep Reasoning..."},
    "Qwen 3 Max": {"bal": 100.00, "pos": 0, "color": "#6366f1", "status": "Pattern Search..."}
}

def market_engine():
    global PRICE
    while True:
        PRICE += random.uniform(-20, 20)
        for name, bot in ARENA_DATA.items():
            # Diverse Strategies
            move = random.random()
            if name == "Qwen 3 Max" or name == "Llama 4 Scout": # Fast Reacting
                if move > 0.7: bot["pos"] += 0.0004; bot["bal"] -= (PRICE * 0.0004); bot["status"] = "QUICK BUY"
                elif move < 0.3: bot["pos"] -= 0.0004; bot["bal"] += (PRICE * 0.0004); bot["status"] = "QUICK SELL"
            elif name == "DeepSeek R2" or name == "Gemini 3 Pro": # Thinking Models
                if move > 0.98: bot["pos"] += 0.002; bot["bal"] -= (PRICE * 0.002); bot["status"] = "STRATEGIC LONG"
                elif move < 0.02: bot["pos"] -= 0.002; bot["bal"] += (PRICE * 0.002); bot["status"] = "STRATEGIC SHORT"
            else: # GPT & Claude - Balanced
                if move > 0.9: bot["pos"] += 0.001; bot["bal"] -= (PRICE * 0.001); bot["status"] = "TREND BUY"
                elif move < 0.1: bot["pos"] -= 0.001; bot["bal"] += (PRICE * 0.001); bot["status"] = "TREND SELL"
            
            bot["total"] = bot["bal"] + (bot["pos"] * PRICE)
        time.sleep(1)

class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/data':
            self.send_response(200); self.send_header('Content-type','application/json'); self.end_headers()
            self.wfile.write(json.dumps({"price": PRICE, "bots": ARENA_DATA}).encode())
        else:
            self.send_response(200); self.send_header('Content-type','text/html'); self.end_headers()
            self.wfile.write(HTML.encode())

class ReuseServer(socketserver.TCPServer):
    allow_reuse_address = True

HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>The Ultimate $100 Arena (6 Models)</title>
    <style>
        body { background:#0b0e11; color:#fff; font-family:-apple-system, sans-serif; padding:20px; text-align:center; }
        .grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px; max-width: 1200px; margin: 0 auto; }
        .card { background:#181a20; border-radius:15px; padding:25px; border-bottom: 6px solid; transition: 0.3s; }
        .name { font-size: 22px; font-weight: bold; margin-bottom: 5px; }
        .total { font-size: 55px; font-weight: 900; margin: 10px 0; font-family: monospace; }
        .stat { color: #848e9c; font-size: 16px; height: 20px; overflow: hidden; }
        .price { font-size: 40px; color: #f0b90b; margin-bottom: 25px; font-family: monospace; font-weight: bold; }
        .perc { font-size: 18px; font-weight: bold; }
        .up { color: #02c076; } .down { color: #f84960; }
    </style>
</head>
<body>
    <div class="price">BTC PRICE: $<span id="p">--</span></div>
    <div class="grid" id="g"></div>
    <script>
        async function update() {
            try {
                const r = await fetch('/data'); const d = await r.json();
                document.getElementById('p').innerText = d.price.toLocaleString(undefined, {minimumFractionDigits: 2});
                document.getElementById('g').innerHTML = Object.entries(d.bots).map(([n, b]) => {
                    const diff = b.total - 100;
                    const pChange = (diff / 100 * 100).toFixed(2);
                    return `
                        <div class="card" style="border-color:${b.color}">
                            <div class="name" style="color:${b.color}">${n}</div>
                            <div class="stat">${b.status}</div>
                            <div class="total">$${b.total.toFixed(2)}</div>
                            <div class="perc ${diff >= 0 ? 'up' : 'down'}">${diff >= 0 ? '▲' : '▼'} ${pChange}%</div>
                        </div>
                    `;
                }).join('');
            } catch(e) {}
        }
        setInterval(update, 1000);
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    threading.Thread(target=market_engine, daemon=True).start()
    with ReuseServer(("", PORT), Handler) as httpd:
        print(f"6-Model Arena Live: http://localhost:{PORT}")
        httpd.serve_forever()
