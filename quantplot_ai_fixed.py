import http.server
import socketserver
import json
import threading
import time
import urllib.request

PORT = 8000
DATA = {
    "price": 0.0,
    "models": {
        "Qwen 3 Max": {"balance": 1000.00, "pos": 0.1, "color": "#00ff88"},
        "DeepSeek V3": {"balance": 1000.00, "pos": 0.05, "color": "#00ccff"},
        "Claude 4.5": {"balance": 1000.00, "pos": -0.04, "color": "#ff4d4d"},
        "GPT-5": {"balance": 1000.00, "pos": -0.12, "color": "#f0b90b"}
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
                        m["balance"] += diff * m["pos"]
                DATA["price"] = new_price
        except:
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
            self.wfile.write(HTML_TEMPLATE.encode())

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Alpha Arena | Live</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body { background: #0b0e11; color: #fff; font-family: monospace; padding: 20px; }
        .grid { display: grid; grid-template-columns: 1fr 300px; gap: 20px; }
        .card { background: #181a20; border: 1px solid #2b2f36; padding: 20px; border-radius: 8px; }
        .price { font-size: 28px; color: #f0b90b; }
        .row { display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid #2b2f36; }
        .up { color: #02c076; }
        .down { color: #f84960; }
    </style>
</head>
<body>
    <div class="grid">
        <div class="card">
            <div style="color:#848e9c">BTC/USDT</div>
            <div id="p" class="price">--</div>
            <div style="height:400px; margin-top:20px;"><canvas id="c"></canvas></div>
        </div>
        <div class="card">
            <h3>Portfolios ($1k Start)</h3>
            <div id="l"></div>
        </div>
    </div>
    <script>
        let chart;
        async function update() {
            try {
                const r = await fetch('/data');
                const d = await r.json();
                document.getElementById('p').innerText = '$' + d.price.toLocaleString();
                let h = '';
                Object.keys(d.models).forEach((n, i) => {
                    const m = d.models[n];
                    const diff = m.balance - 1000;
                    h += `<div class="row"><span>${n}</span><span class="${diff>=0?'up':'down'}">$${m.balance.toFixed(2)}</span></div>`;
                    if(!chart) return;
                    if(!chart.data.datasets[i]) chart.data.datasets.push({label:n, borderColor:m.color, data:[], pointRadius:0});
                    chart.data.datasets[i].data.push(m.balance);
                });
                document.getElementById('l').innerHTML = h;
                if(!chart) {
                    chart = new Chart(document.getElementById('c'), {
                        type:'line', data:{labels:[], datasets:[]},
                        options:{responsive:true, maintainAspectRatio:false, animation:false}
                    });
                }
                chart.data.labels.push("");
                if(chart.data.labels.length > 50) { chart.data.labels.shift(); chart.data.datasets.forEach(s=>s.data.shift()); }
                chart.update();
            } catch(e){}
        }
        setInterval(update, 1000);
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    threading.Thread(target=fetch_price, daemon=True).start()
    with socketserver.TCPServer(("", PORT), DashboardHandler) as httpd:
        print(f"Server live at http://localhost:{PORT}")
        httpd.serve_forever()
