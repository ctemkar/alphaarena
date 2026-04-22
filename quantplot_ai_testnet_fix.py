import http.server
import socketserver
import json
import threading
import time
import ccxt
import os
from dotenv import load_dotenv

load_dotenv()

PORT = 8000
API_KEY = os.getenv('TESTNET_API_KEY')
API_SECRET = os.getenv('TESTNET_API_SECRET')

DATA = {
    "status": "Initializing...",
    "assets": {"BTC/USDT": 0.0, "ETH/USDT": 0.0},
    "balances": {
        "Testnet Wallet": {"balance": 0.0, "color": "#f0b90b"}
    }
}

def trade_logic():
    if not API_KEY or not API_SECRET:
        DATA["status"] = "Error: Missing .env keys"
        return

    try:
        # Initialize with Futures focus
        exchange = ccxt.binance({
            'apiKey': API_KEY,
            'secret': API_SECRET,
            'enableRateLimit': True,
            'options': {'defaultType': 'future'}
        })
        exchange.set_sandbox_mode(True)
        
        # FIX: Explicitly load markets before fetching tickers
        exchange.load_markets()
        DATA["status"] = "Connected & Markets Loaded"
    except Exception as e:
        DATA["status"] = f"Connection Failed: {str(e)}"
        return

    while True:
        try:
            # Fetch specifically for the symbols we want
            # We use the internal IDs if the common ones fail
            symbols = ['BTC/USDT', 'ETH/USDT']
            tickers = exchange.fetch_tickers(symbols)
            
            for s in symbols:
                if s in tickers:
                    DATA["assets"][s] = tickers[s]['last']

            # Fetch Balance
            bal = exchange.fetch_balance()
            DATA["balances"]["Testnet Wallet"]["balance"] = float(bal['total'].get('USDT', 0.0))
            
            DATA["status"] = "Live Tracking: OK"
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
    <title>Alpha Arena | Fix</title>
    <style>
        body { background:#0b0e11; color:#fff; font-family:sans-serif; padding:30px; }
        .box { background:#181a20; border:1px solid #2b2f36; border-radius:15px; padding:30px; margin-bottom:20px; }
        .status { font-size: 18px; color: #848e9c; margin-bottom: 20px; }
        .val { font-size: 40px; font-weight: 800; color: #f0b90b; font-family: monospace; }
        .row { display:flex; justify-content:space-between; font-size:28px; padding:10px 0; border-bottom:1px solid #2b2f36; }
    </style>
</head>
<body>
    <div class="box">
        <div id="stat" class="status">Connecting...</div>
        <div id="btc" class="val">BTC --</div>
        <div id="eth" class="val">ETH --</div>
    </div>
    <div class="box">
        <div id="list"></div>
    </div>
    <script>
        async function update() {
            try {
                const r = await fetch('/data'); const d = await r.json();
                document.getElementById('stat').innerText = d.status;
                document.getElementById('btc').innerText = "BTC: $" + (d.assets['BTC/USDT'] || 0).toLocaleString();
                document.getElementById('eth').innerText = "ETH: $" + (d.assets['ETH/USDT'] || 0).toLocaleString();
                document.getElementById('list').innerHTML = Object.entries(d.balances).map(([n,v]) => 
                    `<div class="row"><span>${n}</span><b>$${v.balance.toFixed(2)}</b></div>`
                ).join('');
            } catch(e) {}
        }
        setInterval(update, 2000);
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        httpd.allow_reuse_address = True
        threading.Thread(target=trade_logic, daemon=True).start()
        httpd.serve_forever()
