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
        exchange = ccxt.binance({
            'apiKey': API_KEY,
            'secret': API_SECRET,
            'enableRateLimit': True,
            'options': {'defaultType': 'future'}
        })
        exchange.set_sandbox_mode(True)
        exchange.load_markets()
        DATA["status"] = "Connected"
    except Exception as e:
        DATA["status"] = f"Conn Error: {str(e)}"
        return

    while True:
        try:
            # Tickers with fallback for Futures naming
            symbols = ['BTC/USDT', 'ETH/USDT']
            tickers = exchange.fetch_tickers(symbols)
            for s in symbols:
                if s in tickers: DATA["assets"][s] = tickers[s]['last']

            # Balance
            bal = exchange.fetch_balance()
            DATA["balances"]["Testnet Wallet"]["balance"] = float(bal['total'].get('USDT', 0.0))
            DATA["status"] = "Live Tracking: OK"
        except Exception as e:
            DATA["status"] = f"Runtime Error: {str(e)}"
        time.sleep(2)

class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/data':
            self.send_response(200); self.send_header('Content-type','application/json'); self.end_headers()
            self.wfile.write(json.dumps(DATA).encode())
        else:
            self.send_response(200); self.send_header('Content-type','text/html'); self.end_headers()
            self.wfile.write(HTML.encode())

# This custom class forces the port to be reusable immediately
class ReuseServer(socketserver.TCPServer):
    allow_reuse_address = True

HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Alpha Arena | Testnet Pro</title>
    <style>
        body { background:#0b0e11; color:#fff; font-family:sans-serif; padding:20px; text-align:center; }
        .box { background:#181a20; border:1px solid #2b2f36; border-radius:15px; padding:30px; margin:10px auto; max-width:600px; }
        .status { font-size: 20px; color: #848e9c; margin-bottom: 20px; }
        .val { font-size: 50px; font-weight: 800; color: #f0b90b; font-family: monospace; margin: 10px 0; }
        .balance { font-size: 60px; color: #02c076; font-weight: 900; }
        .label { color: #848e9c; font-size: 14px; text-transform: uppercase; }
    </style>
</head>
<body>
    <div class="box">
        <div id="stat" class="status">Starting...</div>
        <div class="label">Bitcoin Market Price</div>
        <div id="btc" class="val">--</div>
    </div>
    <div class="box">
        <div class="label">Your Live Testnet Wallet</div>
        <div id="bal" class="balance">$0.00</div>
    </div>
    <script>
        async function update() {
            try {
                const r = await fetch('/data'); const d = await r.json();
                document.getElementById('stat').innerText = d.status;
                document.getElementById('btc').innerText = "$" + (d.assets['BTC/USDT'] || 0).toLocaleString();
                document.getElementById('bal').innerText = "$" + (d.balances['Testnet Wallet'].balance).toLocaleString(undefined, {minimumFractionDigits: 2});
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
        print(f"Testnet Arena started at http://localhost:{PORT}")
        httpd.serve_forever()
