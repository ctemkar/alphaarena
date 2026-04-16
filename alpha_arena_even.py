import http.server
import socketserver
import json
import threading
import time
import urllib.request
from datetime import datetime

PORT = 8000
# Starting everyone at exactly $1,000.00
DATA = {
    "price": 0.0,
    "models": {
        "Qwen 3 Max": {"balance": 1000.00, "pos": 0.08, "color": "#00ff88"},
        "DeepSeek V3": {"balance": 1000.00, "pos": 0.05, "color": "#00ccff"},
        "Claude 4.5": {"balance": 1000.00, "pos": -0.04, "color": "#ff4d4d"},
        "GPT-5": {"balance": 1000.00, "pos": -0.09, "color": "#f0b90b"}
    }
}

def fetch_price():
    while True:
        try:
            url = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
            with urllib.request.urlopen(url) as response:
                res = json.loads(response.read().decode())
                new_price = float(res['price'])
                
                if DATA["price"] > 0:
                    diff = new_price - DATA["price"]
                    for name, m in DATA["models"].items():
                        # Portfolio fluctuates based on real price delta
                        m["balance"] += diff * m["pos"]
                
                DATA["price"] = new_price
        except Exception:
            pass
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
    <title>Alpha Arena | $1,000 Start</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body { background: #0b0e11; color: #fff; font-family: 'SF Mono', monospace; margin: 20px; }
        .header { border-bottom: 1px solid #2b2f36; padding-bottom: 10px; margin-bottom: 20px; display: flex; justify-content: space-between; }
        .grid { display: grid; grid-template-columns: 1fr 350px; gap: 20px; }
        .card { background: #181a20; border: 1px solid #2b2f36; padding: 20px; border-radius: 8px; }
        .price-val { font-size: 28px; color: #f0b90b; font-weight: bold; }
        .model-row { display: flex; justify-content: space-between; padding: 12px 0; border-bottom: 1px solid #2b2f36; }
        .pct { font-size: 12px; margin-left: 10px; }
        .up { color: #02c076; }
        .down { color: #f84960; }
        #canvas-wrap { height: 450px; margin-top: 20px; }
    </style>
</head>
<body>
    <div class="header">
        <h2>ALPHA ARENA: <span style="color:#00ff88">LIVE RESET</span></h2>
        <div id="status">● LOCAL BACKEND ACTIVE</div>
    </div>
    <div class="grid">
        <div class="card">
            <div style="color:#848e9c">LIVE BTC/USDT</div>
            <div id="btc-price" class="price-val">FETCHING...</div>
            <div id="canvas-wrap"><canvas id="arenaChart"></canvas></div>
        </div>
        <div class="card">
            <h3 style="margin-top:0">Portfolio Tracking</h3>
            <div style="font-size:11px; color:#848e9c; margin-bottom:10px;">ALL MODELS STARTED AT $1,000.00</div>
            <div id="list"></div>
        </div>
    </div>
    <script>
        const ctx = document.getElementById('arenaChart').getContext('2d');
        const chart = new Chart(ctx, {
            type: 'line',
            data: { labels: [], datasets: [] },
            options: { 
                responsive: true, 
                maintainAspectRatio: false, 
                animation: false,
                scales: {
                    y: { grid: { color: '#2b2f36' }, ticks: { color: '#848e9c' } },
                    x: { ticks: { display: false } }
                },
                plugins: { legend: { labels: { color: '#fff' } } }
            }
        });

        async function refresh() {
            try {
                const res = await fetch('/data');
                const data = await res.json();
                document.getElementById('btc-price').innerText = '$' + data.price.toLocaleString(undefined, {minimumFractionDigits: 2});
                
                let html = '';
                Object.keys(data.models).forEach((name, i) => {
                    const m = data.models[name];
                    const diff = m.balance - 1000;
                    const pct = (diff / 10).toFixed(2);
                    const colorClass = diff >= 0 ? 'up' : 'down';

                    html += `<div class="model-row">
                        <span>${name}</span>
                        <span class="${colorClass}">$${m.balance.toFixed(2)} <span class="pct">(${pct}%)</span></span>
                    </div>`;
                    
                    if(!chart.data.datasets[i]) {
                        chart.data.datasets.push({ label: name, borderColor: m.color, data: [], pointRadius: 0, borderWidth: 2 });
                    }
                    chart.data.datasets[i].data.push(m.balance);
                });
                document.getElementById('list').innerHTML = html;
                
                chart.data.labels.push("");
                if(chart.data.labels.length > 60) {
                    chart.data.labels.shift();
                    chart.data.datasets.forEach(d => d.data.shift());
                }
                chart.update();
            } catch (e) {}
        }
        setInterval(refresh, 1000);
    </script>
</body>
</html>
