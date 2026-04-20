import http.server
import socketserver
import json
import threading
import time
import urllib.request
import urllib.parse
import math
import statistics
import copy

PORT = 8000
START_BALANCE = 100.0
LOSS_GUARDRAIL_USD = 100.0
TRADING_FEE_RATE = 0.0004
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "martain7r/finance-llama-8b:fp16"

DATA_LOCK = threading.Lock()
DATA = {
    "assets": {"BTCUSDT": 0.0, "ETHUSDT": 0.0, "SOLUSDT": 0.0, "DOGEUSDT": 0.0},
    "models": {
        "Qwen 3 Max": {"balance": START_BALANCE, "target": "BTCUSDT", "base_pos": 0.012, "pos": 0.012, "color": "#00ff88"},
        "Grok 4": {"balance": START_BALANCE, "target": "DOGEUSDT", "base_pos": 4.5, "pos": 4.5, "color": "#ffffff"},
        "DeepSeek V3": {"balance": START_BALANCE, "target": "ETHUSDT", "base_pos": 0.22, "pos": 0.22, "color": "#00ccff"},
        "Llama 4": {"balance": START_BALANCE, "target": "SOLUSDT", "base_pos": 0.75, "pos": 0.75, "color": "#0476f1"},
        "Claude 4.5": {"balance": START_BALANCE, "target": "BTCUSDT", "base_pos": -0.006, "pos": -0.006, "color": "#d97757"},
        "GPT-5": {"balance": START_BALANCE, "target": "ETHUSDT", "base_pos": -0.12, "pos": -0.12, "color": "#f0b90b"},
        "FinancialLlama": {"balance": START_BALANCE, "target": "BTCUSDT", "base_pos": 0.008, "pos": 0.0, "color": "#a855f7", "ollama": True}
    },
    "active_assets": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT"],
    "risk": {"halt_trading": False, "reason": "", "daily_loss_limit_usd": LOSS_GUARDRAIL_USD},
    "ollama_status": {"connected": False, "last_signal": "waiting...", "last_decision": "—", "model": OLLAMA_MODEL},
    "metrics": {"baseline": {}, "optimized": {}, "assumptions": ""}
}

PRICE_HISTORY = {sym: [] for sym in DATA["assets"]}
TOTAL_START_BALANCE = START_BALANCE * len(DATA["models"])


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def adaptive_position(base_pos, history):
    if len(history) < 12:
        return base_pos

    returns = []
    for i in range(1, len(history)):
        prev = history[i - 1]
        if prev <= 0:
            continue
        returns.append((history[i] - prev) / prev)

    if not returns:
        return base_pos

    recent_returns = returns[-12:]
    vol = statistics.pstdev(recent_returns) if len(recent_returns) > 1 else 0.0
    sma = sum(history[-8:]) / 8
    trend = (history[-1] / sma) - 1 if sma > 0 else 0.0

    vol_scale = clamp(0.0015 / (vol + 1e-9), 0.50, 1.20)
    trend_scale = 1.15 if (base_pos > 0 and trend > 0) or (base_pos < 0 and trend < 0) else 0.85
    return base_pos * clamp(vol_scale * trend_scale, 0.45, 1.35)


def apply_loss_guardrail(total_balance):
    pnl = total_balance - TOTAL_START_BALANCE
    if pnl <= -LOSS_GUARDRAIL_USD and not DATA["risk"]["halt_trading"]:
        DATA["risk"]["halt_trading"] = True
        DATA["risk"]["reason"] = f"Daily loss limit reached: ${pnl:.2f}"
        for m in DATA["models"].values():
            m["pos"] = 0.0


def calc_metrics(balance_series, step_pnls, start_balance):
    if not balance_series:
        return {
            "net_pnl": 0.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
            "win_rate": 0.0,
            "expectancy": 0.0,
            "samples": 0,
        }

    peak = balance_series[0]
    max_drawdown = 0.0
    for bal in balance_series:
        peak = max(peak, bal)
        drawdown = (peak - bal) / peak if peak > 0 else 0.0
        max_drawdown = max(max_drawdown, drawdown)

    returns = [p / start_balance for p in step_pnls] if start_balance > 0 else []
    if len(returns) > 1 and statistics.pstdev(returns) > 0:
        sharpe = (statistics.mean(returns) / statistics.pstdev(returns)) * math.sqrt(525600)
    else:
        sharpe = 0.0

    wins = sum(1 for p in step_pnls if p > 0)
    win_rate = (wins / len(step_pnls)) if step_pnls else 0.0
    expectancy = statistics.mean(step_pnls) if step_pnls else 0.0

    return {
        "net_pnl": round(balance_series[-1] - start_balance, 4),
        "sharpe": round(sharpe, 4),
        "max_drawdown": round(max_drawdown, 4),
        "win_rate": round(win_rate, 4),
        "expectancy": round(expectancy, 6),
        "samples": len(step_pnls),
    }


def fetch_klines(symbol, limit=360):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1m&limit={limit}"
    with urllib.request.urlopen(url, timeout=8) as response:
        rows = json.loads(response.read().decode())
        return [float(r[4]) for r in rows]


def run_replay(prices_by_symbol, use_adaptive):
    models = copy.deepcopy(DATA["models"])
    total_start = START_BALANCE * len(models)
    series = [total_start]
    step_pnls = []
    halted = False

    min_len = min(len(v) for v in prices_by_symbol.values())
    if min_len < 30:
        return calc_metrics(series, step_pnls, total_start)

    for i in range(1, min_len):
        step_pnl = 0.0
        for m in models.values():
            sym = m["target"]
            prev = prices_by_symbol[sym][i - 1]
            curr = prices_by_symbol[sym][i]
            diff = curr - prev

            base_pos = m["base_pos"]
            if use_adaptive and not halted:
                pos = adaptive_position(base_pos, prices_by_symbol[sym][: i + 1])
            elif halted:
                pos = 0.0
            else:
                pos = base_pos

            fee = abs(diff * pos) * TRADING_FEE_RATE
            pnl = (diff * pos) - fee
            m["balance"] += pnl
            step_pnl += pnl

        total_balance = sum(x["balance"] for x in models.values())
        if use_adaptive and (total_balance - total_start) <= -LOSS_GUARDRAIL_USD:
            halted = True

        step_pnls.append(step_pnl)
        series.append(total_balance)

    return calc_metrics(series, step_pnls, total_start)


def call_ollama_signal():
    history = PRICE_HISTORY.get("BTCUSDT", [])
    if len(history) < 10:
        return None
    recent = history[-20:]
    change_pct = ((recent[-1] - recent[0]) / recent[0] * 100) if recent[0] > 0 else 0
    prompt = (
        f"You are a crypto trading assistant. "
        f"BTC/USDT last {len(recent)} prices (1s intervals): {[round(p, 2) for p in recent]}. "
        f"20-bar price change: {change_pct:.2f}%. "
        f"Reply with exactly one word: BUY, SELL, or HOLD."
    )
    payload = json.dumps({"model": OLLAMA_MODEL, "prompt": prompt, "stream": False}).encode()
    req = urllib.request.Request(OLLAMA_URL, data=payload)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        res = json.loads(r.read().decode())
    text = res.get("response", "").strip().upper()
    if "BUY" in text:
        return "BUY"
    elif "SELL" in text:
        return "SELL"
    return "HOLD"


def ollama_loop():
    while True:
        try:
            decision = call_ollama_signal()
            if decision is None:
                time.sleep(5)
                continue
            with DATA_LOCK:
                m = DATA["models"]["FinancialLlama"]
                base = abs(m["base_pos"]) or 0.008
                if decision == "BUY":
                    m["base_pos"] = base
                elif decision == "SELL":
                    m["base_pos"] = -base
                else:
                    m["base_pos"] = 0.0
                DATA["ollama_status"]["connected"] = True
                DATA["ollama_status"]["last_signal"] = decision
                DATA["ollama_status"]["last_decision"] = decision
            time.sleep(20)
        except Exception as e:
            with DATA_LOCK:
                DATA["ollama_status"]["connected"] = False
                DATA["ollama_status"]["last_signal"] = f"offline"
            time.sleep(10)


def run_replay_analysis():
    try:
        symbols = sorted({m["target"] for m in DATA["models"].values()})
        prices = {s: fetch_klines(s) for s in symbols}

        baseline = run_replay(prices, use_adaptive=False)
        optimized = run_replay(prices, use_adaptive=True)

        with DATA_LOCK:
            DATA["metrics"]["baseline"] = baseline
            DATA["metrics"]["optimized"] = optimized
            DATA["metrics"]["assumptions"] = "Binance 1m close replay, 360 bars, fee rate 0.04%"
    except Exception as e:
        with DATA_LOCK:
            DATA["metrics"]["assumptions"] = f"Replay unavailable: {e}"

def fetch_prices():
    symbols = set(DATA["assets"].keys())
    url = "https://api.binance.com/api/v3/ticker/price"
    while True:
        try:
            with urllib.request.urlopen(url, timeout=6) as response:
                res = json.loads(response.read().decode())
                prices = {item["symbol"]: float(item["price"]) for item in res if item["symbol"] in symbols}

            with DATA_LOCK:
                for sym, new_price in prices.items():
                    old_price = DATA["assets"][sym]
                    PRICE_HISTORY[sym].append(new_price)
                    if len(PRICE_HISTORY[sym]) > 80:
                        PRICE_HISTORY[sym].pop(0)

                    if old_price > 0:
                        diff = new_price - old_price
                        for m in DATA["models"].values():
                            if m["target"] != sym:
                                continue

                            if sym not in DATA["active_assets"]:
                                m["pos"] = 0.0
                                continue

                            if DATA["risk"]["halt_trading"]:
                                m["pos"] = 0.0
                            else:
                                m["pos"] = adaptive_position(m["base_pos"], PRICE_HISTORY[sym])

                            fee = abs(diff * m["pos"]) * TRADING_FEE_RATE
                            m["balance"] += (diff * m["pos"]) - fee

                    DATA["assets"][sym] = new_price

                total_balance = sum(m["balance"] for m in DATA["models"].values())
                apply_loss_guardrail(total_balance)
        except:
            pass
        time.sleep(1)

class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == '/data':
            with DATA_LOCK:
                payload = json.dumps(DATA)
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(payload.encode())
            return

        if parsed.path == '/toggle':
            params = urllib.parse.parse_qs(parsed.query)
            symbol = params.get('symbol', [None])[0]

            with DATA_LOCK:
                if symbol in DATA["assets"]:
                    if symbol in DATA["active_assets"]:
                        DATA["active_assets"].remove(symbol)
                        for m in DATA["models"].values():
                            if m["target"] == symbol:
                                m["pos"] = 0.0
                    else:
                        DATA["active_assets"].append(symbol)

                payload = json.dumps({"ok": True, "active_assets": DATA["active_assets"]})

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(payload.encode())
            return

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
    <title>Alpha Arena | Multi Model Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {
            --bg: #0b0e11;
            --panel: #181a20;
            --border: #2b2f36;
            --text: #eaecef;
            --muted: #848e9c;
            --up: #02c076;
            --down: #f84960;
            --warn: #f0b90b;
        }
        body {
            background: var(--bg);
            color: var(--text);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            padding: 20px;
            margin: 0;
        }
        .container {
            max-width: 1280px;
            margin: 0 auto;
        }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 16px;
        }
        .title {
            margin: 0;
            font-size: 24px;
            letter-spacing: 0.2px;
        }
        .subtitle {
            color: var(--muted);
            font-size: 12px;
        }
        .cards {
            display: grid;
            grid-template-columns: repeat(4, minmax(170px, 1fr));
            gap: 10px;
            margin-bottom: 14px;
        }
        .card {
            background: var(--panel);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 10px 12px;
        }
        .asset-card {
            cursor: pointer;
            transition: border-color 0.15s ease, opacity 0.15s ease;
        }
        .asset-card.active {
            border-color: var(--up);
            opacity: 1;
        }
        .asset-card.inactive {
            opacity: 0.55;
        }
        .asset-state {
            margin-top: 6px;
            font-size: 11px;
            color: var(--muted);
        }
        .label {
            font-size: 11px;
            color: var(--muted);
            text-transform: uppercase;
            letter-spacing: 0.4px;
        }
        .value {
            margin-top: 6px;
            font-size: 18px;
            font-weight: 700;
        }
        .layout {
            display: grid;
            grid-template-columns: 1.2fr 1fr;
            gap: 16px;
        }
        .panel {
            background: var(--panel);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 14px;
        }
        .panel h3 {
            margin: 0 0 12px;
            font-size: 15px;
        }
        .chart-wrap {
            height: 370px;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }
        th, td {
            border-bottom: 1px solid var(--border);
            padding: 9px 6px;
            text-align: left;
        }
        th {
            color: var(--muted);
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.4px;
            font-weight: 600;
        }
        .num {
            text-align: right;
            font-variant-numeric: tabular-nums;
        }
        .up { color: var(--up); }
        .down { color: var(--down); }
        .status {
            margin-top: 10px;
            font-size: 12px;
            color: var(--muted);
        }
        .status strong {
            color: var(--text);
        }
        .metrics {
            margin-top: 10px;
            padding-top: 10px;
            border-top: 1px solid var(--border);
            font-size: 12px;
            color: var(--muted);
            line-height: 1.5;
        }
        @media (max-width: 980px) {
            .cards {
                grid-template-columns: repeat(2, minmax(160px, 1fr));
            }
            .layout {
                grid-template-columns: 1fr;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1 class="title">Alpha Arena</h1>
            <div class="subtitle">Multi-asset model tracker with adaptive risk sizing</div>
        </div>

        <div id="cards" class="cards"></div>

        <div class="layout">
            <div class="panel">
                <h3>Model Equity Curves</h3>
                <div class="chart-wrap"><canvas id="c"></canvas></div>
            </div>

            <div class="panel">
                <h3>Model Standings</h3>
                <div id="table"></div>
                <div id="status" class="status"></div>
                <div id="metrics" class="metrics"></div>
            </div>
        </div>
    </div>

    <script>
        let chart;

        function formatUsd(v) {
            return '$' + Number(v).toLocaleString(undefined, {maximumFractionDigits: 2});
        }

        function metricLine(title, m) {
            if (!m || Object.keys(m).length === 0) return `<div><strong>${title}:</strong> pending</div>`;
            return `<div><strong>${title}:</strong> PnL ${m.net_pnl}, Sharpe ${m.sharpe}, MDD ${m.max_drawdown}, Win ${m.win_rate}</div>`;
        }

        async function toggleAsset(symbol) {
            try {
                await fetch('/toggle?symbol=' + encodeURIComponent(symbol));
                await run();
            } catch (e) {}
        }

        async function run() {
            try {
                const r = await fetch('/data');
                const d = await r.json();
                const total = Object.values(d.models).reduce((s, m) => s + m.balance, 0);

                const assetCards = Object.entries(d.assets).map(([s, v]) => {
                    const isActive = d.active_assets.includes(s);
                    return `
                        <div class="card asset-card ${isActive ? 'active' : 'inactive'}" onclick="toggleAsset('${s}')" title="Click to ${isActive ? 'disable' : 'enable'} ${s}">
                            <div class="label">${s}</div>
                            <div class="value">${formatUsd(v)}</div>
                            <div class="asset-state">${isActive ? 'Selected' : 'Unselected'}</div>
                        </div>
                    `;
                });

                const stateCard = `
                    <div class="card">
                        <div class="label">Risk State</div>
                        <div class="value ${d.risk.halt_trading ? 'down' : 'up'}">${d.risk.halt_trading ? 'HALTED' : 'ACTIVE'}</div>
                    </div>
                `;

                const totalCard = `
                    <div class="card">
                        <div class="label">Total Balance</div>
                        <div class="value">${formatUsd(total)}</div>
                    </div>
                `;

                const ol = d.ollama_status || {};
                const ollamaCard = `
                    <div class="card">
                        <div class="label">FinancialLlama 8B</div>
                        <div class="value ${ol.connected ? 'up' : 'down'}" style="font-size:14px;">${ol.connected ? ol.last_signal : 'Offline'}</div>
                        <div class="asset-state">${ol.connected ? 'finance-llama-8b connected' : 'Waiting for Ollama...'}</div>
                    </div>
                `;

                document.getElementById('cards').innerHTML = [...assetCards, stateCard, totalCard, ollamaCard].join('');

                const rows = Object.entries(d.models)
                    .sort((a, b) => b[1].balance - a[1].balance)
                    .map(([name, m]) => {
                        const pnl = m.balance - 100;
                        const cls = pnl >= 0 ? 'up' : 'down';
                        return `
                            <tr>
                                <td><span style="color:${m.color};font-weight:600;">${name}</span></td>
                                <td>${m.target}</td>
                                <td class="num">${m.pos.toFixed(4)}</td>
                                <td class="num">${formatUsd(m.balance)}</td>
                                <td class="num ${cls}">${pnl.toFixed(2)}</td>
                            </tr>
                        `;
                    })
                    .join('');

                document.getElementById('table').innerHTML = `
                    <table>
                        <thead>
                            <tr>
                                <th>Model</th>
                                <th>Asset</th>
                                <th class="num">Position</th>
                                <th class="num">Balance</th>
                                <th class="num">PnL</th>
                            </tr>
                        </thead>
                        <tbody>${rows}</tbody>
                    </table>
                `;

                document.getElementById('status').innerHTML = d.risk.halt_trading
                    ? `<strong>Trading Halted:</strong> ${d.risk.reason}`
                    : `<strong>Trading Active:</strong> Daily loss limit ${formatUsd(d.risk.daily_loss_limit_usd)} | Selected coins: ${d.active_assets.join(', ')}`;

                const b = d.metrics.baseline || {};
                const o = d.metrics.optimized || {};
                document.getElementById('metrics').innerHTML = `
                    <div><strong>Replay Assumptions:</strong> ${d.metrics.assumptions || 'pending'}</div>
                    ${metricLine('Baseline', b)}
                    ${metricLine('Optimized', o)}
                `;

                const modelEntries = Object.entries(d.models);
                if (!chart) {
                    chart = new Chart(document.getElementById('c'), {
                        type: 'line',
                        data: {
                            labels: [],
                            datasets: modelEntries.map(([name, m]) => ({
                                label: name,
                                borderColor: m.color,
                                data: [],
                                pointRadius: 0,
                                borderWidth: 2
                            }))
                        },
                        options: {
                            responsive: true,
                            maintainAspectRatio: false,
                            animation: false,
                            plugins: { legend: { labels: { color: '#eaecef' } } },
                            scales: {
                                x: { ticks: { color: '#848e9c' }, grid: { color: '#1e2329' } },
                                y: { ticks: { color: '#848e9c' }, grid: { color: '#1e2329' } }
                            }
                        }
                    });
                }

                chart.data.labels.push('');
                modelEntries.forEach(([name, m]) => {
                    const ds = chart.data.datasets.find(x => x.label === name);
                    if (ds) {
                        ds.data.push(m.balance);
                    }
                });

                if (chart.data.labels.length > 120) {
                    chart.data.labels.shift();
                    chart.data.datasets.forEach(s => s.data.shift());
                }
                chart.update('none');
            } catch (e) {}
        }
        run();
        setInterval(run, 1000);
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    run_replay_analysis()
    threading.Thread(target=fetch_prices, daemon=True).start()
    threading.Thread(target=ollama_loop, daemon=True).start()
    with ReuseServer(("", PORT), DashboardHandler) as httpd:
        print(f"Arena live: http://localhost:{PORT}")
        httpd.serve_forever()
