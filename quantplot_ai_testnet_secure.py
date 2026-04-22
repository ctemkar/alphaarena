import http.server
import socketserver
import json
import threading
import time
import ccxt
import os
from dotenv import load_dotenv

# Load credentials from .env
load_dotenv()

PORT = 8000
API_KEY = os.getenv('TESTNET_API_KEY')
API_SECRET = os.getenv('TESTNET_API_SECRET')

DATA = {
    "status": "Initializing...",
    "assets": {"BTC/USDT": 0.0, "ETH/USDT": 0.0},
    "balances": {
        "Testnet Wallet": {"balance": 0.0, "color": "#f0b90b"},
        "Simulated Benchmark": {"balance": 100.00, "color": "#00ff88"}
    }
}

def trade_logic():
    if not API_KEY or not API_SECRET:
        DATA["status"] = "Missing .env credentials"
        return

    try:
        exchange = ccxt.binance({
            'apiKey': API_KEY,
            'secret': API_SECRET,
            'enableRateLimit': True,
            'options': {'defaultType': 'future'}
        })
        exchange.set_sandbox_mode(True)
        DATA["status"] = "Connected to Binance Testnet"
    except Exception as e:
        DATA["status"] = f"Connection Failed: {str(e)}"
        return

    while True:
        try:
            # Fetch Prices
            tickers = exchange.fetch_tickers(['BTC/USDT', 'ETH/USDT'])
            DATA["assets"]["BTC/USDT"] = tickers['BTC/USDT']['last']
            DATA["assets"]["ETH/USDT"] = tickers['ETH/USDT']['last']

            # Fetch Real Balance
            bal = exchange.fetch_balance()
            DATA["balances"]["Testnet Wallet"]["balance"] = float(bal['total'].get('USDT', 0.0))
            
        except Exception as e:
            DATA["status"] = f"Runtime Error: {str(e)}"
        time.sleep(2)

class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/data':
            self.send_response(200); self.send_header('Content-type', 'application/json'); self.end_headers()
            self.wfile.write(json.dumps(DATA).encode())
        else:
            self.send_response(200); self.send_header('Content-type', 'text/html'); self.end_headers()
            self.wfile.write(HTML.encode())

HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Alpha Arena | Secure Testnet</title>
    <style>
        body { background:#0b0e11; color:#fff; font-family:-apple-system, sans-serif; padding:30px; }
        .box { background:#181a20; border:1px solid #2b2f36; border-radius:15px; padding:30px; margin-bottom:20px; }
        .status { font-size: 20px; color: #848e9c; margin-bottom: 20px; font-weight: bold; }
        .price-row { display: flex; gap: 40px; margin-bottom: 20px; }
        .price-item { flex: 1; }
        .label { color: #848e9c; font-size: 16px; text-transform: uppercase; margin-bottom: 5px; }
        .val { font-size: 44px; font-weight: 800; color: #f0b90b; font-family: monospace; }
        .row { display:flex; justify-content:space-between; font-size:32px; padding:15px 0; border-bottom:1px solid #2b2f36; }
    </style>
</head>
<body>
    <div class="box">
        <div id="stat" class="status">Loading...</div>
        <div class="price-row">
            <div class="price-item">
                <div class="label">BTC/USDT</div>
                <div id="btc" class="val">--</div>
            </div>
            <div class="price-item">
                <div class="label">ETH/USDT</div>
                <div id="eth" class="val">--</div>
            </div>
        </div>
    </div>
    <div class="box">
        <div class="label">Live Testnet Wallet (Large View)</div>
        <div id="list"></div>
    </div>
    <script>
        async function update() {
            try {
                const r = await fetch('/data'); const d = await r.json();
                document.getElementById('stat').innerText = d.status;
                document.getElementById('btc').innerText = "$" + d.assets['BTC/USDT'].toLocaleString();
                document.getElementById('eth').innerText = "$" + d.assets['ETH/USDT'].toLocaleString();
                
                document.getElementById('list').innerHTML = Object.entries(d.balances).map(([n,v]) => 
                    `<div class="row"><span style="color:${v.color}">${n}</span><b>$${v.balance.toFixed(2)}</b></div>`
                ).join('');
            } catch(e) {}
        }
        setInterval(update, 2000);
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    threading.Thread(target=trade_logic, daemon=True).start()
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        httpd.allow_reuse_address = True
        httpd.serve_forever()
