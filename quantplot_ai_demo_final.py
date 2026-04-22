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
    "status": "Initializing Demo...",
    "assets": {"BTC/USDT:USDT": 0.0, "ETH/USDT:USDT": 0.0},
    "balances": {"Demo Wallet": {"balance": 0.0, "color": "#02c076"}}
}

def trade_logic():
    if not API_KEY or not API_SECRET:
        DATA["status"] = "Error: .env keys missing"
        return

    try:
        # Use binanceusdm for USD-Margined Futures
        exchange = ccxt.binanceusdm({
            'apiKey': API_KEY,
            'secret': API_SECRET,
            'enableRateLimit': True,
        })
        
        # Point specifically to the Demo Trading environment
        exchange.urls['api']['fapiPublic'] = 'https://fapi.binance.com'
        exchange.urls['api']['fapiPrivate'] = 'https://fapi.binance.com'
        # Crucial for Demo Mode:
        exchange.set_sandbox_mode(True) 
        
        exchange.load_markets()
        DATA["status"] = "Connected to Demo"
    except Exception as e:
        DATA["status"] = f"Conn Error: {str(e)}"
        return

    while True:
        try:
            # Use specific Futures symbol naming
            symbols = ['BTC/USDT:USDT', 'ETH/USDT:USDT']
            tickers = exchange.fetch_tickers(symbols)
            for s in symbols:
                if s in tickers: DATA["assets"][s] = tickers[s]['last']

            # Fetch Balance from Demo Account
            bal = exchange.fetch_balance()
            DATA["balances"]["Demo Wallet"]["balance"] = float(bal['total'].get('USDT', 0.0))
            DATA["status"] = "Demo Mode Active"
        except Exception as e:
            DATA["status"] = f"Update Error: {str(e)}"
        time.sleep(2)

class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/data':
            self.send_response(200); self.send_header('Content-type','application/json'); self.end_headers()
            self.wfile.write(json.dumps(DATA).encode())
        else:
            self.send_response(200); self.send_header('Content-type','text/html'); self.end_headers()
            self.wfile.write(HTML.encode())

class ReuseServer(socketserver.TCPServer):
    allow_reuse_address = True

HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Alpha Arena | Demo Mode</title>
    <style>
        body { background:#0b0e11; color:#fff; font-family:sans-serif; padding:40px; }
        .box { background:#181a20; border:2px solid #2b2f36; border-radius:15px; padding:40px; margin:0 auto; max-width:800px; }
        .status { font-size: 24px; color: #848e9c; margin-bottom: 25px; font-weight: bold; }
        .val { font-size: 80px; font-weight: 900; color: #f0b90b; font-family: monospace; margin: 15px 0; }
        .bal-val { font-size: 100px; color: #02c076; font-weight: 900; }
        .label { color: #848e9c; font-size: 18px; text-transform: uppercase; letter-spacing: 2px; }
    </style>
</head>
<body>
    <div class="box">
        <div id="stat" class="status">Syncing...</div>
        <div class="label">BTC PERPETUAL</div>
        <div id="btc" class="val">--</div>
        <div style="height:40px;"></div>
        <div class="label">DEMO WALLET</div>
        <div id="bal" class="bal-val">$0.00</div>
    </div>
    <script>
        async function update() {
            try {
                const r = await fetch('/data'); const d = await r.json();
                document.getElementById('stat').innerText = d.status;
                document.getElementById('btc').innerText = "$" + (d.assets['BTC/USDT:USDT'] || 0).toLocaleString();
                document.getElementById('bal').innerText = "$" + (d.balances['Demo Wallet'].balance).toLocaleString(undefined, {minimumFractionDigits: 2});
            } catch(e) {}
        }
        setInterval(update, 2000);
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    threading.Thread(target=trade_logic, daemon=True).start()
    with ReuseServer(("", PORT), Handler) as httpd:
        print(f"Demo Arena Online: http://localhost:{PORT}")
        httpd.serve_forever()
