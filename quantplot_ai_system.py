import http.server
import socketserver
import json
import threading
import time
import urllib.request
from datetime import datetime

# Configuration
PORT = 8000
DATA = {
    "price": 0.0,
    "models": {
        "Qwen 3 Max": {"balance": 12231.82, "pos": 0.5, "color": "#00ff88"},
        "DeepSeek V3": {"balance": 10489.00, "pos": 0.2, "color": "#00ccff"},
        "Claude 4.5": {"balance": 7296.00, "pos": -0.3, "color": "#ff4d4d"},
        "GPT-5": {"balance": 4126.00, "pos": -0.8, "color": "#f0b90b"}
    },
    "logs": []
}

def fetch_price():
    while True:
        try:
            url = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
            with urllib.request.urlopen(url) as response:
                res = json.loads(response.read().decode())
                new_price = float(res['price'])
                
                # Update logic: Move portfolio based on price delta
                if DATA["price"] > 0:
                    diff = new_price - DATA["price"]
                    for name, m in DATA["models"].items():
                        m["balance"] += diff * m["pos"]
                
                DATA["price"] = new_price
        except Exception as e:
            print(f"Fetch Error: {e}")
        time.sleep(1)

class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/data':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(DATA).encode())
        else:
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(HTML_CONTENT.encode())

HTML_CONTENT = """
<!DOCTYPE html>
<html>
<head>
    <title>Alpha Arena | Live Mac Mini Backend</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body { background: #0d0f14; color: #fff; font-family: 'SF Pro Display', sans-serif; margin: 20px; }
        .grid { display: grid; grid-template-columns: 1fr 300px; gap: 20px; }
        .card { background: #1a1d26; border: 1px solid #2d313e; padding: 20px; border-radius: 12px; }
        .price { font-size: 32px; font-weight: bold; color: #f0b90b; }
        .model-line { display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid #2d313e; }
        #canvas-container { height: 400px; margin-top: 20px; }
    </style>
</head>
<body>
    <h1>Alpha Arena <span style="font-size: 14px; color: #00ff88;">● LIVE FROM MAC MINI</span></h1>
    <div class="grid">
        <div class="card">
            <div>BTC/USDT PRICE</div>
            <div id="btc-price" class="price">Loading...</div>
            <div id="canvas-container"><canvas id="mainChart"></canvas></div>
        </div>
        <div class="card">
            <h3>Portfolios</h3>
            <div id="portfolio-list"></div>
        </div>
    </div>
    <script>
        const ctx = document.getElementById('mainChart').getContext('2d');
        const chart = new Chart(ctx, {
            type: 'line',
            data: { labels: [], datasets: [] },
            options: { responsive: true, maintainAspectRatio: false, animation: false }
        });

        async function update() {
            const res = await fetch('/data');
            const data = await res.json();
            document.getElementById('btc-price').innerText = '$' + data.price.toLocaleString();
            
            let html = '';
            Object.keys(data.models).forEach((name, i) => {
                const m = data.models[name];
                html += `<div class="model-line"><span>${name}</span><strong>$${m.balance.toFixed(2)}</strong></div>`;
                
                if(!chart.data.datasets[i]) {
                    chart.data.datasets.push({ label: name, borderColor: m.color, data: [], pointRadius: 0 });
                }
                chart.data.datasets[i].data.push(m.balance);
            });
            document.getElementById('portfolio-list').innerHTML = html;
            
            chart.data.labels.push(new Date().toLocaleTimeString());
            if(chart.data.labels.length > 50) {
                chart.data.labels.shift();
                chart.data.datasets.forEach(d => d.data.shift());
            }
            chart.update();
        }
        setInterval(update, 1000);
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    threading.Thread(target=fetch_price, daemon=True).start()
    print(f"Server started at http://localhost:{PORT}")
    with socketserver.TCPServer(("", PORT), DashboardHandler) as httpd:
        httpd.serve_forever()
