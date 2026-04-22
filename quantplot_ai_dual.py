import http.server
import socketserver
import json
import threading
import time
import urllib.request

PORT = 8000
DATA = {
    "assets": {"BTCUSDT": 0.0, "ETHUSDT": 0.0, "SOLUSDT": 0.0, "DOGEUSDT": 0.0},
    "arena_btc": {
        "Qwen 3 Max": {"balance": 100.00, "pos": 0.012, "color": "#00ff88"},
        "Grok 4": {"balance": 100.00, "pos": 0.008, "color": "#ffffff"},
        "GPT-5": {"balance": 100.00, "pos": -0.012, "color": "#f0b90b"}
    },
    "arena_basket": {
        "Qwen (BTC)": {"balance": 100.00, "sym": "BTCUSDT", "pos": 0.012, "color": "#00ff88"},
        "Grok (DOGE)": {"balance": 100.00, "sym": "DOGEUSDT", "pos": 5.0, "color": "#ffffff"},
        "Llama (SOL)": {"balance": 100.00, "sym": "SOLUSDT", "pos": 0.8, "color": "#0476f1"},
        "DeepSeek (ETH)": {"balance": 100.00, "sym": "ETHUSDT", "pos": 0.22, "color": "#00ccff"}
    }
}

def fetch_data():
    symbols = list(DATA["assets"].keys())
    while True:
        try:
            url = "https://api.binance.com/api/v3/ticker/price"
            with urllib.request.urlopen(url) as r:
                res = json.loads(r.read().decode())
                prices = {i['symbol']: float(i['price']) for i in res if i['symbol'] in symbols}
                for s, new_p in prices.items():
                    old_p = DATA["assets"][s]
                    if old_p > 0:
                        diff = new_p - old_p
                        # Update BTC Arena
                        if s == "BTCUSDT":
                            for name, m in DATA["arena_btc"].items():
                                m["balance"] += diff * m["pos"]
                        # Update Basket Arena
                        for name, m in DATA["arena_basket"].items():
                            if m["sym"] == s:
                                m["balance"] += diff * m["pos"]
                    DATA["assets"][s] = new_p
        except: pass
        time.sleep(1)

class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/data':
            self.send_response(200); self.send_header('Content-type','application/json'); self.end_headers()
            self.wfile.write(json.dumps(DATA).encode())
        else:
            self.send_response(200); self.send_header('Content-type','text/html'); self.end_headers()
            self.wfile.write(HTML.encode())

HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Dual Arena View</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body { background:#0b0e11; color:#fff; font-family:sans-serif; padding:20px; margin:0; }
        .section { background:#181a20; border:1px solid #2b2f36; border-radius:12px; padding:20px; margin-bottom:20px; }
        .grid { display:grid; grid-template-columns: 1fr 300px; gap:20px; }
        canvas { height:300px !important; }
        .up { color:#02c076; } .down { color:#f84960; }
        .price-row { display:flex; gap:15px; font-size:12px; color:#848e9c; margin-bottom:10px; }
    </style>
</head>
<body>
    <div class="section">
        <h3>SYSTEM A: PURE BTC ARENA</h3>
        <div id="p-btc" class="price-row"></div>
        <div class="grid">
            <canvas id="chartA"></canvas>
            <div id="listA"></div>
        </div>
    </div>
    <div class="section">
        <h3>SYSTEM B: MULTI-ASSET BASKET</h3>
        <div id="p-basket" class="price-row"></div>
        <div class="grid">
            <canvas id="chartB"></canvas>
            <div id="listB"></div>
        </div>
    </div>
    <script>
        let cA, cB;
        async function update() {
            const r = await fetch('/data'); const d = await r.json();
            
            // Prices
            document.getElementById('p-btc').innerHTML = `BTC: $${d.assets.BTCUSDT.toLocaleString()}`;
            document.getElementById('p-basket').innerHTML = Object.entries(d.assets).map(([s,v])=>`${s.replace('USDT','')}: $${v.toLocaleString()}`).join(' | ');

            const render = (arena, chart, listId) => {
                let h = '';
                Object.keys(arena).sort((a,b)=>arena[b].balance-arena[a].balance).forEach((n,i)=>{
                    const m = arena[n]; const diff = m.balance-100;
                    h += `<div style="padding:8px 0; border-bottom:1px solid #2b2f36; display:flex; justify-content:space-between;">
                        <span style="color:${m.color}">${n}</span><b class="${diff>=0?'up':'down'}">$${m.balance.toFixed(2)}</b></div>`;
                    if(chart) {
                        let ds = chart.data.datasets.find(x=>x.label===n);
                        if(!ds) chart.data.datasets.push({label:n, borderColor:m.color, data:[], pointRadius:0, borderWidth:2});
                        else ds.data.push(m.balance);
                    }
                });
                document.getElementById(listId).innerHTML = h;
            };

            if(!cA) {
                cA = new Chart(document.getElementById('chartA'), {type:'line', data:{labels:[], datasets:[]}, options:{responsive:true, maintainAspectRatio:false, animation:false}});
                cB = new Chart(document.getElementById('chartB'), {type:'line', data:{labels:[], datasets:[]}, options:{responsive:true, maintainAspectRatio:false, animation:false}});
            }

            render(d.arena_btc, cA, 'listA');
            render(d.arena_basket, cB, 'listB');

            [cA, cB].forEach(c => {
                c.data.labels.push(""); 
                if(c.data.labels.length > 50) { c.data.labels.shift(); c.data.datasets.forEach(s=>s.data.shift()); }
                c.update('none');
            });
        }
        setInterval(update, 1000);
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    socketserver.TCPServer.allow_reuse_address = True
    threading.Thread(target=fetch_data, daemon=True).start()
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print(f"Dual Arena Running: http://localhost:{PORT}")
        httpd.serve_forever()
