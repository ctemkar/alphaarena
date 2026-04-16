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
        "Qwen 3 Max": {"balance": 100.00, "pos": 0.012, "color": "#00ff88", "desc": "Aggressive Long"},
        "Grok 4": {"balance": 100.00, "pos": 0.008, "color": "#ffffff", "desc": "Sentiment-Driven"},
        "DeepSeek V3": {"balance": 100.00, "pos": 0.005, "color": "#00ccff", "desc": "Quantitative"},
        "Llama 4": {"balance": 100.00, "pos": 0.003, "color": "#0476f1", "desc": "Conservative"},
        "Claude 4.5": {"balance": 100.00, "pos": -0.005, "color": "#d97757", "desc": "Hedged Short"},
        "GPT-5": {"balance": 100.00, "pos": -0.012, "color": "#f0b90b", "desc": "Contrarian"}
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
    <title>Alpha Arena | $100 Baseline</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body { background: #0b0e11; color: #eaecef; font-family: -apple-system, sans-serif; padding: 20px; margin: 0; }
        .container { max-width: 1300px; margin: 0 auto; }
        .grid { display: grid; grid-template-columns: 1fr 360px; gap: 20px; height: 80vh; }
        .panel { background: #181a20; border: 1px solid #2b2f36; border-radius: 12px; padding: 25px; display: flex; flex-direction: column; }
        .price-display { font-size: 36px; font-weight: 800; color: #f0b90b; margin-bottom: 5px; }
        .legend { display: flex; flex-wrap: wrap; gap: 15px; margin-bottom: 20px; padding: 10px; background: #0b0e11; border-radius: 8px; }
        .legend-item { display: flex; align-items: center; font-size: 11px; color: #848e9c; }
        .dot { height: 10px; width: 10px; border-radius: 2px; margin-right: 6px; }
        .model-card { display: flex; justify-content: space-between; align-items: center; padding: 14px 0; border-bottom: 1px solid #2b2f36; }
        .model-info { display: flex; flex-direction: column; }
        .model-name { font-weight: bold; font-size: 14px; }
        .model-desc { font-size: 10px; color: #848e9c; }
        .up { color: #02c076; }
        .down { color: #f84960; }
        #chart-wrap { flex-grow: 1; position: relative; }
    </style>
</head>
<body>
    <div class="container">
        <h2 style="display:flex; justify-content:space-between; align-items:center;">
            <span>ALPHA ARENA <span style="font-weight:200; color:#848e9c;">| $100 START</span></span>
            <span style="font-size:11px; color:#848e9c;">ONE TOUCH BACKEND</span>
        </h2>
        
        <div class="grid">
            <div class="panel">
                <div id="price" class="price-display">--</div>
                <div id="legend-box" class="legend"></div>
                <div id="chart-wrap"><canvas id="arenaChart"></canvas></div>
            </div>
            
            <div class="panel">
                <h3 style="margin-top:0">Real-Time ROI</h3>
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
                let l = '';
                const sorted = Object.keys(d.models).sort((a,b) => d.models[b].balance - d.models[a].balance);
                
                sorted.forEach((n, i) => {
                    const m = d.models[n];
                    const diff = m.balance - 100;
                    const roi = diff.toFixed(2);
                    
                    h += `<div class="model-card">
                            <div class="model-info">
                                <span class="model-name" style="color:${m.color}">${n}</span>
                                <span class="model-desc">${m.desc}</span>
                            </div>
                            <div class="val ${diff>=0?'up':'down'}" style="font-family:monospace; font-weight:bold; text-align:right;">
                                $${m.balance.toFixed(2)}<br>
                                <span style="font-size:10px;">${roi}%</span>
                            </div>
                          </div>`;
                    
                    l += `<div class="legend-item"><div class="dot" style="background:${m.color}"></div>${n}</div>`;
                    
                    if(!chart) return;
                    let ds = chart.data.datasets.find(x => x.label === n);
                    if(!ds) {
                        chart.data.datasets.push({label:n, borderColor:m.color, data:[], pointRadius:0, borderWidth:2.5, tension:0.2});
                    } else {
                        ds.data.push(m.balance);
                    }
                });
                
                document.getElementById('leaderboard').innerHTML = h;
                document.getElementById('legend-box').innerHTML = l;

                if(!chart) {
                    chart = new Chart(document.getElementById('arenaChart'), {
                        type:'line', data:{labels:[], datasets:[]},
                        options:{
                            responsive:true, maintainAspectRatio:false, animation:false,
                            scales: { 
                                x: { display: false },
                                y: { grid: { color: '#2b2f36' }, ticks: { color: '#848e9c', font: { family: 'monospace' } } }
                            },
                            plugins: { legend: { display: false } }
                        }
                    });
                }
                
                chart.data.labels.push("");
                if(chart.data.labels.length > 100) { 
                    chart.data.labels.shift(); 
                    chart.data.datasets.forEach(s=>s.data.shift()); 
                }
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
