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
    "assets": {"BTC": 0.0, "ETH": 0.0},
    "balances": {"Demo Wallet": {"balance": 0.0}}
}

def trade_logic():
    if not API_KEY:
        DATA["status"] = "Error: Check .env keys"
        return

    try:
        # 2026 Standard for USD-M Futures
        exchange = ccxt.binanceusdm({
            'apiKey': API_KEY,
            'secret': API_SECRET,
            'enableRateLimit': True,
        })
        
        # FIX: The new method for Demo Trading
        # This replaces set_sandbox_mode(True)
        exchange.enable_demo_trading(True)
        
        exchange.load_markets()
        DATA["status"] = "Connected to Binance Demo"
    except Exception as e:
        DATA["status"] = f"Init Error: {str(e)}"
        return

    while True:
        try:
            # Ticker naming for 2026 CCXT Unified symbols
            symbols = ['BTC/USDT:USDT', 'ETH/USDT:USDT']
            t = exchange.fetch_tickers(symbols)
            
            DATA["assets"]["BTC"] = t['BTC/USDT:USDT']['last'] if 'BTC/USDT:USDT' in t else 0.0
            DATA["assets"]["ETH"] = t['ETH/USDT:USDT']['last'] if 'ETH/USDT:USDT' in t else 0.0

            # Balance check from virtual Demo pool
            bal = exchange.fetch_balance()
            DATA["balances"]["Demo Wallet"]["balance"] = float(bal['total'].get('USDT', 0.0))
            
            DATA["status"] = "Demo Tracking: LIVE"
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
    <title>Alpha Arena | Demo 2026</title>
    <style>
        body { background:#0b0e11; color:#fff; font-family:sans-serif; padding:50px; text-align:center; }
        .box { background:#181a20; border:4px solid #02c076; border-radius:30px; padding:60px; max-width:900px; margin:0 auto; }
        .status { font-size: 28px; color: #848e9c; margin-bottom: 40px; }
        .label { color: #848e9c; font-size: 24px; text-transform: uppercase; font-weight: bold; margin-top: 30px;}
        .val { font-size: 90px; font-weight: 900; color: #f0b90b; font-family: monospace; }
        .bal-val { font-size: 110px; color: #02c076; font-weight: 900; }
    </style>
</head>
<body>
    <div class="box">
        <div id="stat" class="status">Connecting...</div>
        <div class="label">BTC PERPETUAL</div>
        <div id="btc" class="val">--</div>
        <div class="label">DEMO WALLET BALANCE</div>
        <div id="bal" class="bal-val">$0.00</div>
    </div>
    <script>
        async function update() {
            try {
                const r = await fetch('/data'); const d = await r.json();
                document.getElementById('stat').innerText = d.status;
                document.getElementById('btc').innerText = "$" + (d.assets['BTC'] || 0).toLocaleString();
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
        print(f"Demo Arena 2026 Online: http://localhost:{PORT}")
        httpd.serve_forever()
