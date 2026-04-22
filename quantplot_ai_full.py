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
        "Qwen 3 Max": {"balance": 1000.00, "pos": 0.12, "color": "#00ff88"},
        "Grok 3": {"balance": 1000.00, "pos": 0.08, "color": "#ffffff"},
        "Llama 4": {"balance": 1000.00, "pos": 0.04, "color": "#0081fb"},
        "DeepSeek V3": {"balance": 1000.00, "pos": 0.02, "color": "#00ccff"},
        "Claude 4.5": {"balance": 1000.00, "pos": -0.05, "color": "#ff4d4d"},
        "GPT-5": {"balance": 1000.00, "pos": -0.15, "color": "#f0b90b"}
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
    <title>Alpha Arena | Live Leaderboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body { background: #0b0e11; color: #eaecef; font-family: -apple-system, sans-serif; padding: 20px; margin: 0; }
        .container { max-width: 1200px; margin: 0 auto; }
        .grid { display: grid; grid-template-columns: 1fr 340px; gap: 20px; height: 80vh; }
        .panel { background: #181a20; border: 1px solid #2b2f36; border-radius: 12px; padding: 20px; display: flex; flex-direction: column; }
        .price-display { font-size: 32px; font-weight: 800; color: #f0b90b; margin-bottom: 20px; }
        .model-card { display: flex; justify-content: space-between; padding: 12px 0; border-bottom: 1px solid #2b2f36; }
        .up { color: #02c076; }
        .down { color: #f84960; }
        #chart-wrap { flex-grow: 1; position: relative; }
    </style>
</head>
<body>
    <div class="container">
        <h2 style="display:flex; justify-content:space-between;">
            <span>ALPHA ARENA</span>
            <span style="font-size:12px; color:#02c076;">LIVE</span>
        </h2>
        <div class="grid">
            <div class="panel">
                <div id="price" class="price-display">--</div>
                <div id="chart-wrap"><canvas id="arenaChart"></canvas></div>
            </div>
            <div class="panel">
                <div id="leaderboard"></div>
            </div>
        </div>
    </div>
    <script>
        let chart;
        async function refresh() {
            try {
                const r = await fetch('/data');
                const d = await r.json();
                document.getElementById('price').innerText = '$' + d.price.toLocaleString(undefined, {minimumFractionDigits: 2});
                let h = '';
                Object.keys(d.models).sort((a,b) => d.models[b].balance - d.models[a].balance).forEach((n, i) => {
                    const m = d.models[n];
                    const diff = m.balance - 1000;
                    h += `<div class="model-card"><span>${n}</span><span class="${diff>=0?'up':'down'}">$${m.balance.toFixed(2)}</span></div>`;
                    if(!chart) return;
                    let ds = chart.data.datasets.find(x => x.label === n);
                    if(!ds) chart.data.datasets.push({label:n, borderColor:m.color, data:[], pointRadius:0, borderWidth:2, tension:0.1});
                    else ds.data.push(m.balance);
                });
                document.getElementById('leaderboard').innerHTML = h;
                if(!chart) {
                    chart = new Chart(document.getElementById('arenaChart'), {
                        type:'line', data:{labels:[], datasets:[]},
                        options:{responsive:true, maintainAspectRatio:false, animation:false, plugins:{legend:{display:false}}}
                    });
                }
                chart.data.labels.push("");
                if(chart.data.labels.length > 50) { chart.data.labels.shift(); chart.data.datasets.forEach(s=>s.data.shift()); }
                chart.update('none');
            } catch(e){}
        }
        setInterval(refresh, 1000);
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    socketserver.TCPServer.allow_reuse_address = True
    threading.Thread(target=fetch_price, daemon=True).start()
    with socketserver.TCPServer(("", PORT), DashboardHandler) as httpd:
        print(f"Arena running at http://localhost:{PORT}")
        httpd.serve_forever()
