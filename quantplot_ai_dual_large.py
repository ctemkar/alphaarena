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
        "DeepSeek V3": {"balance": 100.00, "pos": 0.009, "color": "#00ccff"},
        "Gemini 2U": {"balance": 100.00, "pos": 0.007, "color": "#4e8cf7"},
        "Grok 4": {"balance": 100.00, "pos": 0.005, "color": "#ffffff"},
        "Llama 4": {"balance": 100.00, "pos": -0.005, "color": "#0476f1"},
        "GPT-5": {"balance": 100.00, "pos": -0.012, "color": "#f0b90b"}
    },
    "arena_basket": {
        "Qwen (BTC)": {"balance": 100.00, "sym": "BTCUSDT", "pos": 0.012, "color": "#00ff88"},
        "DeepSeek (ETH)": {"balance": 100.00, "sym": "ETHUSDT", "pos": 0.22, "color": "#00ccff"},
        "Gemini (SOL)": {"balance": 100.00, "sym": "SOLUSDT", "pos": 0.85, "color": "#4e8cf7"},
        "Llama (SOL)": {"balance": 100.00, "sym": "SOLUSDT", "pos": 0.75, "color": "#0476f1"},
        "Grok (DOGE)": {"balance": 100.00, "sym": "DOGEUSDT", "pos": 5.0, "color": "#ffffff"},
        "GPT-5 (ETH)": {"balance": 100.00, "sym": "ETHUSDT", "pos": -0.15, "color": "#f0b90b"}
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
                        if s == "BTCUSDT":
                            for name, m in DATA["arena_btc"].items():
                                m["balance"] += diff * m["pos"]
                        for name, m in DATA["arena_basket"].items():
                            if m["sym"] == s:
                                m["balance"] += diff * m["pos"]
                    DATA["assets"][s] = new_p
        except: pass
        time.sleep(1)

class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/data':
            self.send_response(200); self.send_header('Content-type', 'application/json'); self.end_headers()
            self.wfile.write(json.dumps(DATA).encode())
        else:
            self.send_response(200); self.send_header('Content-type', 'text/html'); self.end_headers()
            self.wfile.write(HTML.encode())

class ReuseServer(socketserver.TCPServer):
    allow_reuse_address = True

HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Alpha Arena | Large Text View</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body { background:#0b0e11; color:#fff; font-family:-apple-system, sans-serif; padding:20px; margin:0; overflow-x:hidden; }
        .section { background:#181a20; border:1px solid #2b2f36; border-radius:12px; padding:20px; margin-bottom:20px; }
        .grid { display:flex; gap:25px; width:100%; align-items:stretch; }
        .chart-side { flex: 1; min-width: 0; }
        .list-side { width: 380px; flex-shrink: 0; }
        canvas { height:350px !important; width:100% !important; }
        .up { color:#02c076; } .down { color:#f84960; }
        .price-row { display:flex; gap:20px; font-size:18px; color:#848e9c; margin-bottom:15px; flex-wrap:wrap; text-transform:uppercase; font-weight: bold; }
        h3 { margin: 0 0 12px 0; font-size: 22px; color: #fff; letter-spacing: 1px; border-bottom: 2px solid #2b2f36; padding-bottom: 5px; }
        .row { padding:10px 0; border-bottom:1px solid #2b2f36; display:flex; justify-content:space-between; align-items:center; font-size: 22px; }
        .agent-name { font-weight: bold; }
        .balance { font-family: 'SF Mono', monospace; font-weight: 800; }
    </style>
</head>
<body>
    <div class="section">
        <h3>System A: Pure BTC Arena</h3>
        <div id="p-btc" class="price-row"></div>
        <div class="grid">
            <div class="chart-side"><canvas id="chartA"></canvas></div>
            <div id="listA" class="list-side"></div>
        </div>
    </div>
    <div class="section">
        <h3>System B: Multi-Asset Basket</h3>
        <div id="p-basket" class="price-row"></div>
        <div class="grid">
            <div class="chart-side"><canvas id="chartB"></canvas></div>
            <div id="listB" class="list-side"></div>
        </div>
    </div>
    <script>
        let cA, cB;
        async function update() {
            try {
                const r = await fetch('/data'); const d = await r.json();
                document.getElementById('p-btc').innerHTML = `<b>BTC:</b> <span style="color:#f0b90b">$${d.assets.BTCUSDT.toLocaleString()}</span>`;
                document.getElementById('p-basket').innerHTML = Object.entries(d.assets).map(([s,v])=>`<b>${s.replace('USDT','')}</b> <span style="color:#f0b90b">$${v.toLocaleString()}</span>`).join(' | ');

                const render = (arena, chart, listId) => {
                    let h = '';
                    Object.keys(arena).sort((a,b)=>arena[b].balance-arena[a].balance).forEach((n,i)=>{
                        const m = arena[n]; const diff = m.balance-100;
                        h += `<div class="row"><span class="agent-name" style="color:${m.color}">${n}</span><b class="balance ${diff>=0?'up':'down'}">$${m.balance.toFixed(2)}</b></div>`;
                        if(chart) {
                            let ds = chart.data.datasets.find(x=>x.label===n);
                            if(!ds) chart.data.datasets.push({label:n, borderColor:m.color, data:[], pointRadius:0, borderWidth:4, tension:0.2});
                            else ds.data.push(m.balance);
                        }
                    });
                    document.getElementById(listId).innerHTML = h;
                };

                if(!cA) {
                    const opt = {
                        responsive:true, 
                        maintainAspectRatio:false, 
                        animation:false, 
                        plugins:{legend:{display:false}}, 
                        scales:{
                            x:{display:false},
                            y:{grid:{color:'#2b2f36'}, ticks:{color:'#848e9c', font:{size:16, weight:'bold'}}}
                        }
                    };
                    cA = new Chart(document.getElementById('chartA'), {type:'line', data:{labels:[], datasets:[]}, options:opt});
                    cB = new Chart(document.getElementById('chartB'), {type:'line', data:{labels:[], datasets:[]}, options:opt});
                }
                render(d.arena_btc, cA, 'listA');
                render(d.arena_basket, cB, 'listB');
                [cA, cB].forEach(c => {
                    c.data.labels.push(""); 
                    if(c.data.labels.length > 60) { c.data.labels.shift(); c.data.datasets.forEach(s=>s.data.shift()); }
                    c.update('none');
                });
            } catch(e){}
        }
        setInterval(update, 1000);
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    threading.Thread(target=fetch_data, daemon=True).start()
    with ReuseServer(("", PORT), Handler) as httpd:
        print(f"Large UI Arena live: http://localhost:{PORT}")
        httpd.serve_forever()
