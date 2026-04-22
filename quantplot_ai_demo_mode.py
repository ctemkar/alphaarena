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
# Use your REAL Binance API keys from .env
# Note: Ensure Demo Trading is enabled in your Binance account settings first
API_KEY = os.getenv('TESTNET_API_KEY')
API_SECRET = os.getenv('TESTNET_API_SECRET')

DATA = {
    "status": "Initializing Demo Mode...",
    "assets": {"BTC/USDT": 0.0, "ETH/USDT": 0.0},
    "balances": {
        "Demo Wallet": {"balance": 0.0, "color": "#02c076"}
    }
}

def trade_logic():
    if not API_KEY:
        DATA["status"] = "Error: Keys not found in .env"
        return

    try:
        # Initialize CCXT for Binance USD-M Futures
        exchange = ccxt.binanceusdm({
            'apiKey': API_KEY,
            'secret': API_SECRET,
            'enableRateLimit': True,
        })
        
        # New 2026 Protocol: Enable Sandbox/Demo mode
        exchange.set_sandbox_mode(True)
        exchange.load_markets()
        DATA["status"] = "Connected to Demo Trading"
    except Exception as e:
        DATA["status"] = f"Init Error: {str(e)}"
        return

    while True:
        try:
            # Fetch prices using the Unified CCXT symbols
            tickers = exchange.fetch_tickers(['BTC/USDT', 'ETH/USDT'])
            if 'BTC/USDT' in tickers: DATA["assets"]['BTC/USDT'] = tickers['BTC/USDT']['last']
            if 'ETH/USDT' in tickers: DATA["assets"]['ETH/USDT'] = tickers['ETH/USDT']['last']

            # Fetch Demo Balance
            bal = exchange.fetch_balance()
            # Demo funds are usually in USDT or BNBBTC
            DATA["balances"]["Demo Wallet"]["balance"] = float(bal['total'].get('USDT', 0.0))
            
            DATA["status"] = "Demo Mode: Active"
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
        body { background:#0b0e11; color:#fff; font-family:-apple-system, sans-serif; padding:40px; }
        .box { background:#181a20; border:2px solid #2b2f36; border-radius:20px; padding:40px; max-width:800px; margin:0 auto; }
        .status { font-size: 24px; color: #848e9c; margin-bottom: 30px; border-left: 4px solid #f0b90b; padding-left: 15px; }
        .label { color: #848e9c; font-size: 18px; text-transform: uppercase; letter-spacing: 2px; }
        .val { font-size: 64px; font-weight: 900; color: #f0b90b; font-family: 'Courier New', monospace; margin: 15px 0; }
        .balance-box { border-top: 2px solid #2b2f36; margin-top: 30px; padding-top: 30px; }
        .bal-val { font-size: 80px; color: #02c076; font-weight: 900; }
    </style>
</head>
<body>
    <div class="box">
        <div id="stat" class="status">Connecting to Demo...</div>
        <div class="label">Live BTC Demo Price</div>
        <div id="btc" class="val">$0.00</div>
        
        <div class="balance-box">
            <div class="label">Virtual Demo Balance</div>
            <div id="bal" class="bal-val">$0.00</div>
        </div>
    </div>
    <script>
        async function update() {
            try {
                const r = await fetch('/data'); const d = await r.json();
                document.getElementById('stat').innerText = d.status;
                document.getElementById('btc').innerText = "$" + (d.assets['BTC/USDT'] || 0).toLocaleString();
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
