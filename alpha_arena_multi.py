import http.server
import socketserver
import json
import threading
import time
import urllib.request

PORT = 8000
DATA = {
    "assets": {"BTCUSDT": 0.0, "ETHUSDT": 0.0, "SOLUSDT": 0.0, "DOGEUSDT": 0.0},
    "models": {
        "Qwen 3 Max": {"balance": 100.00, "target": "BTCUSDT", "pos": 0.012, "color": "#00ff88"},
        "Grok 4": {"balance": 100.00, "target": "DOGEUSDT", "pos": 4.5, "color": "#ffffff"},
        "DeepSeek V3": {"balance": 100.00, "target": "ETHUSDT", "pos": 0.22, "color": "#00ccff"},
        "Llama 4": {"balance": 100.00, "target": "SOLUSDT", "pos": 0.75, "color": "#0476f1"},
        "Claude 4.5": {"balance": 100.00, "target": "BTCUSDT", "pos": -0.006, "color": "#d97757"},
        "GPT-5": {"balance": 100.00, "target": "ETHUSDT", "pos": -0.12, "color": "#f0b90b"}
    }
}

def fetch_prices():
    symbols = list(DATA["assets"].keys())
    while True:
        try:
            url = "https://api.binance.com/api/v3/ticker/price"
            with urllib.request.urlopen(url) as response:
                res = json.loads(response.read().decode())
                prices = {item['symbol']: float(item['price']) for item in res if item['symbol'] in symbols}
                for sym, new_price in prices.items():
                    old_price = DATA["assets"][sym]
                    if old_price > 0:
                        diff = new_price - old_price
                        for name, m in DATA["models"].items():
                            if m["target"] == sym:
                                m["balance"] += diff * m["pos"]
                    DATA["assets"][sym] = new_price
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
            self.wfile.write(HTML_CONTENT.encode())

class ReuseServer(socketserver.TCPServer):
    allow_reuse_address = True

HTML_CONTENT = """
<!DOCTYPE html>
<html>
<head>
    <title>Alpha Arena | Live</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body { background: #0b0e11; color: #eaecef; font-family: -apple-system, sans-serif; padding: 20px; }
        .grid { display: grid; grid-template-columns: 1fr 340px; gap: 20px; }
        .panel { background: #181a20; border: 1px solid #2b2f36; border-radius: 12px; padding: 20px; }
        .asset-bar { display: flex; gap: 15px; margin-bottom: 20px; }
        .pill { background: #0b0e11; border: 1px solid #2b2f36; padding: 8px 12px; border-radius: 6px; flex: 1; text-align: center; }
        .model-row { display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid #2b2f36; }
        .up { color: #02c076; } .down { color: #f84960; }
    </style>
</head>
<body>
    <div id="assets" class="asset-bar"></div>
    <div class="grid">
        <div class="panel"><canvas id="c" style="height:450px;"></canvas></div>
        <div class="panel"><h3>Leaderboard</h3><div id="l"></div></div>
    </div>
    <script>
        let chart;
        async function run() {
            try {
                const r = await fetch('/data');
                const d = await r.json();
                document.getElementById('assets').innerHTML = Object.entries(d.assets).map(([s,v]) => 
                    `<div class="pill"><div style="font-size:10px;color:#848e9c">${s}</div><b>$${v.toLocaleString()}</b></div>`).join('');
                
                let h = '';
                Object.keys(d.models).sort((a,b)=>d.models[b].balance-d.models[a].balance).forEach((n,i)=>{
                    const m = d.models[n];
                    const diff = m.balance-100;
                    h += `<div class="model-row"><span style="color:${m.color}">${n}</span><b class="${diff>=0?'up':'down'}">$${m.balance.toFixed(2)}</b></div>`;
                    if(!chart) return;
                    let ds = chart.data.datasets.find(x=>x.label===n);
                    if(!ds) chart.data.datasets.push({label:n, borderColor:m.color, data:[], pointRadius:0, borderWidth:2});
                    else ds.data.push(m.balance);
                });
                document.getElementById('l').innerHTML = h;
                if(!chart) chart = new Chart(document.getElementById('c'), {type:'line', data:{labels:[], datasets:[]}, options:{responsive:true, maintainAspectRatio:false, animation:false}});
                chart.data.labels.push("");
                if(chart.data.labels.length > 60) { chart.data.labels.shift(); chart.data.datasets.forEach(s=>s.data.shift()); }
                chart.update('none');
            } catch(e){}
        }
        setInterval(run, 1000);
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    threading.Thread(target=fetch_prices, daemon=True).start()
    with ReuseServer(("", PORT), DashboardHandler) as httpd:
        print(f"Arena live: http://localhost:{PORT}")
        httpd.serve_forever()
