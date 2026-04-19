import http.server, socketserver, json, threading, time, urllib.request, os

def load_env():
    env = {}
    if os.path.exists('.env'):
        with open('.env') as f:
            for line in f:
                if '=' in line and not line.startswith('#'):
                    k, v = line.strip().split('=', 1)
                    env[k.strip()] = v.strip().replace('"', '').replace("'", "")
    return env

ENV = load_env()
OR_KEY = ENV.get('OPENROUTER_API_KEY', '')
PORT = 8000
OLLAMA_TIMEOUT = 8
ARENA_CYCLE_SECONDS = 1
MAX_LEVERAGE = 20
TRADE_NOTIONAL_PCT = 1.5
MIN_TRADE_NOTIONAL = 80.0
SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT", "BNB": "BNBUSDT", "DOGE": "DOGEUSDT"}
LIVE_PRICES = {s: 0.0 for s in SYMBOLS}
START_PRICES = {s: 0.0 for s in SYMBOLS}
LAST_PRICE_UPDATE_TS = 0.0
PRICE_SEQ = 0
HISTORY = []
LOGS = []
OLLAMA_MODELS = []
OLLAMA_MODELS_TS = 0.0
MODEL_SEMAPHORE = threading.Semaphore(1)

ARENA_DATA = {
    "Qwen2.5-Coder (7B)": {"bal": 100.0, "pos": 0, "color": "#6366f1", "provider": "ollama", "model": "phi3:latest", "temperature": 0.25, "busy": False},
    "DeepSeek-R1 (8B)": {"bal": 100.0, "pos": 0, "color": "#67e8f9", "provider": "ollama", "model": "deepseek-r1:8b", "temperature": 0.45, "busy": False},
    "Llama 3.2 (3B)": {"bal": 100.0, "pos": 0, "color": "#10a37f", "provider": "ollama", "model": "llama3.2:3b", "temperature": 0.60, "busy": False},
    "Mistral (7B)": {"bal": 100.0, "pos": 0, "color": "#f0b90b", "provider": "ollama", "model": "llama3:latest", "temperature": 0.35, "busy": False},
    "Gemma 4": {"bal": 100.0, "pos": 0, "color": "#34d399", "provider": "ollama", "model": "phi3:latest", "temperature": 0.55, "busy": False}
}

BASKET_HISTORY = {s: [] for s in SYMBOLS}
BASKET_DATA = {
    name: {"bal": 100.0, "pos": {s: 0.0 for s in SYMBOLS}, "color": d["color"], "provider": d["provider"], "model": d["model"], "temperature": d.get("temperature", 0.35), "busy": False}
    for name, d in ARENA_DATA.items()
}
ACTIVE_ARENA_MODELS = []
ACTIVE_BASKET_MODELS = []
ARENA_ORDER = list(ARENA_DATA.keys())
BASKET_ORDER = list(BASKET_DATA.keys())
ARENA_IDX = 0
BASKET_IDX = 0

def set_active_models(names, system):
    global ACTIVE_ARENA_MODELS, ACTIVE_BASKET_MODELS
    allowed = set(ARENA_DATA.keys())
    selected = [n for n in names if n in allowed]
    if system == "arena":
        ACTIVE_ARENA_MODELS = selected
        for n in ARENA_DATA:
            if n not in ACTIVE_ARENA_MODELS:
                ARENA_DATA[n]["busy"] = False
    elif system == "basket":
        ACTIVE_BASKET_MODELS = selected
        for n in BASKET_DATA:
            if n not in ACTIVE_BASKET_MODELS:
                BASKET_DATA[n]["busy"] = False

def toggle_active_model(name, system):
    if system == "arena":
        if name in ACTIVE_ARENA_MODELS:
            set_active_models([n for n in ACTIVE_ARENA_MODELS if n != name], "arena")
        else:
            set_active_models(ACTIVE_ARENA_MODELS + [name], "arena")
    elif system == "basket":
        if name in ACTIVE_BASKET_MODELS:
            set_active_models([n for n in ACTIVE_BASKET_MODELS if n != name], "basket")
        else:
            set_active_models(ACTIVE_BASKET_MODELS + [name], "basket")

def set_all_models(system, active):
    if active:
        set_active_models(list(ARENA_DATA.keys()), system)
    else:
        set_active_models([], system)

def sync_model_selections(payload):
    if "arena_models" in payload:
        names = payload.get("arena_models", [])
        if not isinstance(names, list):
            raise ValueError("arena_models must be a list")
        set_active_models(names, "arena")

    if "basket_models" in payload:
        names = payload.get("basket_models", [])
        if not isinstance(names, list):
            raise ValueError("basket_models must be a list")
        set_active_models(names, "basket")

    if "toggle" in payload:
        t = payload.get("toggle") or {}
        name = t.get("model")
        system = t.get("system")
        if name not in ARENA_DATA:
            raise ValueError("unknown model")
        if system not in ("arena", "basket"):
            raise ValueError("system must be arena or basket")
        toggle_active_model(name, system)

    if "set_all" in payload:
        s = payload.get("set_all") or {}
        system = s.get("system")
        active = bool(s.get("active"))
        if system not in ("arena", "basket"):
            raise ValueError("system must be arena or basket")
        set_all_models(system, active)

    for n in ARENA_DATA:
        if n not in ACTIVE_ARENA_MODELS:
            ARENA_DATA[n]["busy"] = False
    for n in BASKET_DATA:
        if n not in ACTIVE_BASKET_MODELS:
            BASKET_DATA[n]["busy"] = False

def extract_decision(text):
    upper = (text or "").strip().upper()
    if "BUY" in upper:
        return "BUY"
    if "SELL" in upper:
        return "SELL"
    if "LONG" in upper:
        return "BUY"
    if "SHORT" in upper:
        return "SELL"
    return None

def request_model_text(bot, prompt):
    with MODEL_SEMAPHORE:
        if bot["provider"] == "ollama":
            model = resolve_ollama_model(bot["model"])
            if not model:
                raise RuntimeError(f"model missing ({bot['model']})")
            url = "http://localhost:11434/api/generate"
            payload = json.dumps({
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": 6, "temperature": bot.get("temperature", 0.35)}
            }).encode()
        else:
            if not OR_KEY:
                raise RuntimeError("missing OPENROUTER_API_KEY")
            url = "https://openrouter.ai/api/v1/chat/completions"
            payload = json.dumps({
                "model": bot["model"],
                "messages": [{"role": "user", "content": prompt}],
                "temperature": bot.get("temperature", 0.35)
            }).encode()

        req = urllib.request.Request(url, data=payload)
        req.add_header("Content-Type", "application/json")
        if bot["provider"] == "openrouter":
            req.add_header("Authorization", f"Bearer {OR_KEY}")

        with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT if bot["provider"] == "ollama" else 10) as r:
            res = json.loads(r.read().decode())
            return res["response"] if bot["provider"] == "ollama" else res["choices"][0]["message"]["content"]

def get_model_decision(bot, prompt):
    attempts = [
        prompt,
        prompt
        + "\nYou must choose exactly one token from this set: BUY, SELL."
        + "\nDo not output HOLD or explanation.",
        prompt
        + "\nFinal attempt: output exactly BUY or SELL (single token, uppercase).",
    ]

    last_text = ""
    for attempt_prompt in attempts:
        last_text = request_model_text(bot, attempt_prompt)
        decision = extract_decision(last_text)
        if decision:
            return decision

    # Keep decision LLM-driven by inferring intent words from the final model response.
    upper = (last_text or "").upper()
    if any(w in upper for w in ["BULL", "UP", "LONG", "RISE"]):
        return "BUY"
    if any(w in upper for w in ["BEAR", "DOWN", "SHORT", "DROP"]):
        return "SELL"
    return None

def reset_all_state():
    for b in ARENA_DATA.values():
        b["bal"] = 100.0
        b["pos"] = 0
        b["busy"] = False

    for b in BASKET_DATA.values():
        b["bal"] = 100.0
        b["pos"] = {s: 0.0 for s in SYMBOLS}
        b["busy"] = False

    LOGS.clear()
    LOGS.append("SYSTEM RESET: all bots back to $100")

def refresh_ollama_models():
    global OLLAMA_MODELS, OLLAMA_MODELS_TS
    now = time.time()
    if now - OLLAMA_MODELS_TS < 30 and OLLAMA_MODELS:
        return OLLAMA_MODELS
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2) as r:
            data = json.loads(r.read().decode())
            OLLAMA_MODELS = [m.get("name", "") for m in data.get("models", []) if m.get("name")]
            OLLAMA_MODELS_TS = now
    except:
        pass
    return OLLAMA_MODELS

def resolve_ollama_model(target):
    available = refresh_ollama_models()
    if not available:
        return target
    if target in available:
        return target

    aliases = {
        "qwen2.5-coder": ["qwen2.5-coder:latest", "qwen3.5:latest"],
        "deepseek-r1": ["deepseek-r1:latest", "deepseek-r1:8b", "qwen3.5:latest", "phi3:latest"],
        "llama3.2": ["llama3.2:latest", "llama3:latest"],
        "mistral": ["mistral:latest"],
        "gemma4": ["gemma4:latest", "gemma3:latest"]
    }

    base = target.split(":", 1)[0]
    if base in aliases:
        for candidate in aliases[base]:
            if candidate in available:
                return candidate

    family = [m for m in available if m == base or m.startswith(base + ":")]
    if not family:
        return None

    for m in family:
        if m.endswith(":latest"):
            return m
    return family[0]

def fetch_binance():
    global START_PRICES, LAST_PRICE_UPDATE_TS, PRICE_SEQ
    while True:
        try:
            url = "https://api.binance.com/api/v3/ticker/price"
            with urllib.request.urlopen(url) as r:
                data = json.loads(r.read().decode())
                temp = {i['symbol']: float(i['price']) for i in data if 'symbol' in i}
                for s, pair in SYMBOLS.items():
                    if pair in temp:
                        LIVE_PRICES[s] = temp[pair]
                        if START_PRICES[s] == 0: START_PRICES[s] = LIVE_PRICES[s]
                HISTORY.append(LIVE_PRICES["BTC"])
                if len(HISTORY) > 30: HISTORY.pop(0)
                for s in SYMBOLS:
                    BASKET_HISTORY[s].append(LIVE_PRICES[s])
                    if len(BASKET_HISTORY[s]) > 30: BASKET_HISTORY[s].pop(0)
                LAST_PRICE_UPDATE_TS = time.time()
                PRICE_SEQ += 1
        except: pass
        time.sleep(1)

def call_model(name):
    b = ARENA_DATA[name]
    try:
        prompt = (
            f"BTC values: {HISTORY[-10:]}. "
            "Choose direction for the next tick. "
            "Respond with exactly one token: BUY or SELL."
        )
        decision = get_model_decision(b, prompt)
        if not decision:
            LOGS.append(f"{name}: HOLD")
            return

        p = LIVE_PRICES["BTC"]
        equity = b["bal"] + (b["pos"] * p)
        if equity <= 0 or p <= 0:
            LOGS.append(f"{name}: HOLD")
            return

        current_notional = abs(b["pos"]) * p
        max_notional = max(20.0, equity * MAX_LEVERAGE)
        trade_notional = max(MIN_TRADE_NOTIONAL, equity * TRADE_NOTIONAL_PCT)
        open_notional = min(trade_notional, max_notional - current_notional)
        trade_qty = open_notional / p if open_notional > 0 else 0.0

        if decision == "BUY":
            if b["pos"] < 0:
                cover_qty = min(abs(b["pos"]), trade_notional / p)
                if cover_qty > 0:
                    b["bal"] -= cover_qty * p
                    b["pos"] += cover_qty
                    LOGS.append(f"{name}: COVERED")
                else:
                    LOGS.append(f"{name}: HOLD")
            elif trade_qty > 0:
                b["bal"] -= open_notional
                b["pos"] += trade_qty
                LOGS.append(f"{name}: BOUGHT")
            else:
                LOGS.append(f"{name}: HOLD")
        elif decision == "SELL":
            if b["pos"] > 0:
                sell_qty = min(b["pos"], trade_notional / p)
                if sell_qty > 0:
                    b["bal"] += sell_qty * p
                    b["pos"] -= sell_qty
                    LOGS.append(f"{name}: SOLD")
                else:
                    LOGS.append(f"{name}: HOLD")
            elif trade_qty > 0:
                b["bal"] += open_notional
                b["pos"] -= trade_qty
                LOGS.append(f"{name}: SHORTED")
            else:
                LOGS.append(f"{name}: HOLD")
    except Exception as e:
        LOGS.append(f"{name}: error {str(e)[:40]}")

def run_bot_cycle(name):
    try:
        call_model(name)
    finally:
        ARENA_DATA[name]["busy"] = False

def arena_loop():
    global ARENA_IDX
    while True:
        if ARENA_ORDER:
            name = ARENA_ORDER[ARENA_IDX % len(ARENA_ORDER)]
            ARENA_IDX += 1
            if name in ACTIVE_ARENA_MODELS and not ARENA_DATA[name]["busy"]:
                ARENA_DATA[name]["busy"] = True
                run_bot_cycle(name)
        if len(LOGS) > 120: LOGS.pop(0)
        time.sleep(ARENA_CYCLE_SECONDS)

def call_basket_model(name):
    b = BASKET_DATA[name]
    try:
        hist_summary = ", ".join(f"{s}: {BASKET_HISTORY[s][-5:]}" for s in SYMBOLS if BASKET_HISTORY[s])
        prompt = (
            f"Crypto basket prices (last 5 ticks): {hist_summary}. "
            "Choose basket direction for the next tick. "
            "Respond with exactly one token: BUY or SELL."
        )
        decision = get_model_decision(b, prompt)
        if not decision:
            LOGS.append(f"BASKET {name}: HOLD")
            return

        equity = b["bal"] + sum(b["pos"][s] * LIVE_PRICES[s] for s in SYMBOLS)
        if equity <= 0: return
        trade_notional = max(MIN_TRADE_NOTIONAL, equity * TRADE_NOTIONAL_PCT)
        per_sym_notional = trade_notional / len(SYMBOLS)

        action = "HOLD"
        for s in SYMBOLS:
            p = LIVE_PRICES[s]
            if p <= 0: continue
            max_notional = max(20.0 / len(SYMBOLS), equity * MAX_LEVERAGE / len(SYMBOLS))
            open_notional = min(per_sym_notional, max_notional - abs(b["pos"][s]) * p)
            trade_qty = open_notional / p if open_notional > 0 else 0.0
            if decision == "BUY":
                if b["pos"][s] < 0:
                    cover_qty = min(abs(b["pos"][s]), per_sym_notional / p)
                    if cover_qty > 0:
                        b["bal"] -= cover_qty * p
                        b["pos"][s] += cover_qty
                        action = "COVERED"
                elif trade_qty > 0:
                    b["bal"] -= open_notional
                    b["pos"][s] += trade_qty
                    action = "BOUGHT"
            elif decision == "SELL":
                if b["pos"][s] > 0:
                    sell_qty = min(b["pos"][s], per_sym_notional / p)
                    if sell_qty > 0:
                        b["bal"] += sell_qty * p
                        b["pos"][s] -= sell_qty
                        action = "SOLD"
                elif trade_qty > 0:
                    b["bal"] += open_notional
                    b["pos"][s] -= trade_qty
                    action = "SHORTED"
        LOGS.append(f"BASKET {name}: {action}")
    except Exception as e:
        LOGS.append(f"BASKET {name}: error {str(e)[:40]}")
    finally:
        b["busy"] = False

def basket_loop():
    global BASKET_IDX
    while True:
        if BASKET_ORDER:
            name = BASKET_ORDER[BASKET_IDX % len(BASKET_ORDER)]
            BASKET_IDX += 1
            if name in ACTIVE_BASKET_MODELS and not BASKET_DATA[name]["busy"]:
                BASKET_DATA[name]["busy"] = True
                call_basket_model(name)
        time.sleep(ARENA_CYCLE_SECONDS)

class H(http.server.SimpleHTTPRequestHandler):
    def do_POST(self):
        if self.path == '/reset':
            reset_all_state()
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode())
        elif self.path == '/active-models':
            length = int(self.headers.get('Content-Length', '0'))
            raw = self.rfile.read(length).decode() if length > 0 else '{}'
            try:
                payload = json.loads(raw)
                sync_model_selections(payload)
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    "ok": True,
                    "active_arena_models": ACTIVE_ARENA_MODELS,
                    "active_basket_models": ACTIVE_BASKET_MODELS
                }).encode())
            except Exception as e:
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        route = self.path.split('?', 1)[0]
        if route == '/data':
            for b in ARENA_DATA.values(): b["total"] = b["bal"] + (b["pos"] * LIVE_PRICES["BTC"])
            for b in BASKET_DATA.values(): b["total"] = b["bal"] + sum(b["pos"][s] * LIVE_PRICES[s] for s in SYMBOLS)
            self.send_response(200)
            self.send_header('Content-type','application/json')
            self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
            self.end_headers()
            self.wfile.write(json.dumps({"prices":LIVE_PRICES, "bots":ARENA_DATA, "basket_bots":BASKET_DATA, "active_arena_models":ACTIVE_ARENA_MODELS, "active_basket_models":ACTIVE_BASKET_MODELS, "model_names":list(ARENA_DATA.keys()), "last_price_update_ts":LAST_PRICE_UPDATE_TS, "price_seq":PRICE_SEQ, "server_time":time.time(), "logs":LOGS}).encode())
        else:
            self.send_response(200)
            self.send_header('Content-type','text/html')
            self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
            self.end_headers()
            self.wfile.write(HTML.encode())

class ReuseTCPServer(socketserver.TCPServer):
    allow_reuse_address = True

HTML = """
<!DOCTYPE html><html><head><style>
    body { background:#0b0e11; color:#fff; font-family:sans-serif; text-align:center; padding:10px; margin:0; }
    .sidebar { position:fixed; left:0; top:0; height:100vh; width:180px; background:#11151a; border-right:1px solid #2b2f36; padding:16px; box-sizing:border-box; text-align:left; }
    .content { margin-left:190px; }
    .btn { width:100%; background:#f0b90b; color:#111; border:0; border-radius:8px; padding:10px; font-weight:700; cursor:pointer; }
    .btn:active { transform:translateY(1px); }
    .side-note { margin-top:10px; font-size:11px; color:#848e9c; line-height:1.4; }
    .meta { font-size:11px; color:#9aa4b2; margin:-4px 0 10px; }
    .grid { display:flex; justify-content:center; gap:8px; flex-wrap:wrap; margin-bottom:20px; }
    .card { background:#181a20; border-radius:10px; padding:12px; border-bottom:4px solid; width:170px; }
    .total { font-size:24px; font-weight:900; font-family:monospace; color:#f0b90b; }
    .summary { background:#1e2329; border-radius:10px; padding:15px; border:2px solid #02c076; width:220px; }
</style></head><body>
    <aside class="sidebar">
        <div style="font-size:13px; font-weight:700; margin:14px 0 10px; color:#f0b90b;">CONTROLS</div>
        <div class="side-note">Click any model card to move it between paused (top) and selected (active).</div>
        <div id="heartbeat" class="side-note">Heartbeat: --</div>
        <button class="btn" onclick="resetArena()">Reset To $100</button>
        <div id="resetStatus" class="side-note"></div>
    </aside>
    <main class="content">
        <h3 style="color:#f0b90b">SYSTEM 1: AI ARENA (OLLAMA + CLOUD)</h3>
        <div id="s1meta" class="meta">BTC: -- | Last update: --</div>
        <div id="ba" class="grid"></div>
        <hr style="border:0; border-top:1px solid #2b2f36; margin:20px 0;">
        <h3 style="color:#02c076">SYSTEM 2: CRYPTO BASKET</h3>
        <div id="s2meta" class="meta">BTC/ETH/SOL/XRP/BNB/DOGE: -- | Last update: --</div>
        <div id="ca" class="grid"></div>
        <div id="logs" style="font-size:10px; color:#848e9c; margin-top:10px;"></div>
    </main>
    <script>
        let renderTick = 0;

        function updateHeartbeat() {
            const t = new Date();
            const dot = (t.getSeconds() % 2 === 0) ? '●' : '○';
            document.getElementById('heartbeat').innerText = `Heartbeat ${dot} ${t.toLocaleTimeString()}`;
        }

        async function postActiveModels(payload) {
            try {
                const r = await fetch('/active-models', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                const d = await r.json();
                document.getElementById('resetStatus').innerText = d.ok ? 'Model selection updated' : 'Update failed';
                await update();
            } catch(e) {
                document.getElementById('resetStatus').innerText = 'Update failed';
            }
        }

        async function toggleModel(system, model) {
            await postActiveModels({toggle: {system, model}});
        }

        async function setAllModels(system, active) {
            await postActiveModels({set_all: {system, active}});
        }

        async function resetArena() {
            try {
                const r = await fetch('/reset', { method: 'POST' });
                if (r.ok) {
                    document.getElementById('resetStatus').innerText = 'Reset complete';
                    await update();
                } else {
                    document.getElementById('resetStatus').innerText = 'Reset failed';
                }
            } catch(e) {
                document.getElementById('resetStatus').innerText = 'Reset failed';
            }
        }

        async function update() {
            try {
                const r = await fetch(`/data?ts=${Date.now()}`, { cache: 'no-store' });
                const d = await r.json();
                renderTick += 1;
                const px = d.prices || {};
                const btc = Number(px.BTC || 0);
                const basketSymbols = ['BTC', 'ETH', 'SOL', 'XRP', 'BNB', 'DOGE'];
                const basketText = basketSymbols.map(s => `${s} ${Number(px[s] || 0).toFixed(4)}`).join(' | ');
                const now = new Date().toLocaleTimeString();
                const priceTs = Number(d.last_price_update_ts || 0);
                const seq = Number(d.price_seq || 0);
                const serverTs = Number(d.server_time || 0);
                const age = priceTs > 0 && serverTs > 0 ? Math.max(0, serverTs - priceTs) : NaN;
                const ageText = Number.isFinite(age) ? `${age.toFixed(1)}s ago` : '--';
                document.getElementById('s1meta').innerText = `BTC: ${btc ? btc.toFixed(4) : '--'} | Feed #${seq} | Market refresh: ${ageText} | Last render: ${now} | Tick: ${renderTick}`;
                document.getElementById('s2meta').innerText = `${basketText} | Feed #${seq} | Market refresh: ${ageText} | Last render: ${now} | Tick: ${renderTick}`;
                let bActiveH="", bPausedH="", bS=0;
                let bPnl=0, bCount=0;
                Object.entries(d.bots).forEach(([n,b])=>{
                    const active = (d.active_arena_models || []).includes(n);
                    const state = active ? 'ACTIVE' : 'PAUSED';
                    const pnl = (b.total - 100);
                    const card = `<div class="card" style="border-color:${b.color}; cursor:pointer;${active ? '' : ' width:150px; padding:9px; opacity:0.92;'}" onclick="toggleModel('arena','${n.replace(/'/g, "&#39;")}')">
                        <div style="font-size:9px;opacity:0.5">${b.provider}</div>
                        <div style="color:${b.color};font-weight:bold">${n}</div>
                        <div style="font-size:9px;color:${active ? '#02c076' : '#848e9c'}">${state}</div>
                        <div class="total">$${b.total.toFixed(2)}</div>
                        <div style="font-size:10px;color:${pnl >= 0 ? '#02c076' : '#f6465d'}">P&L: ${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}</div>
                    </div>`;
                    if (active) {
                        bActiveH += card;
                        bS += b.total;
                        bPnl += (b.total - 100);
                        bCount += 1;
                    } else {
                        bPausedH += card;
                    }
                });
                const bAvgPnl = bCount ? (bPnl / bCount) : 0;
                let bH = "";
                if (bPausedH) {
                    bH += `<div style="width:100%; max-width:1100px; margin:0 auto 4px; font-size:11px; color:#848e9c; text-align:left;">PAUSED (TOP ROW, NOT IN TOTAL)</div>`;
                    bH += `<div style="width:100%; max-width:1100px; margin:0 auto 8px; display:flex; justify-content:center; gap:8px; flex-wrap:wrap;">${bPausedH}</div>`;
                }
                bH += `<div style="width:100%; max-width:1100px; margin:0 auto 4px; font-size:11px; color:#9aa4b2; text-align:left;">SELECTED (IN TOTAL)</div>`;
                bH += `<div style="width:100%; max-width:1100px; margin:0 auto 8px; display:flex; justify-content:center; gap:8px; flex-wrap:wrap; min-height:86px;">${bActiveH || '<div style="font-size:11px; color:#848e9c; align-self:center;">Click a paused card to select it</div>'}</div>`;
                bH+=`<div class="summary"><div style="font-size:24px;color:#02c076">$${bS.toFixed(2)}</div><div>ARENA TOTAL (ACTIVE)</div><div style="font-size:11px;color:#848e9c;margin-top:6px;">AVG P&L: $${bAvgPnl.toFixed(2)}</div></div>`;
                document.getElementById('ba').innerHTML=bH;

                let cActiveH="", cPausedH="", cS=0;
                let cPnl=0, cCount=0;
                Object.entries(d.basket_bots).forEach(([n,b])=>{
                    const active = (d.active_basket_models || []).includes(n);
                    const state = active ? 'ACTIVE' : 'PAUSED';
                    const pnl = (b.total - 100);
                    const card = `<div class="card" style="border-color:${b.color}; cursor:pointer;${active ? '' : ' width:150px; padding:9px; opacity:0.92;'}" onclick="toggleModel('basket','${n.replace(/'/g, "&#39;")}')">
                        <div style="font-size:9px;opacity:0.5">${b.provider}</div>
                        <div style="color:${b.color};font-weight:bold">${n}</div>
                        <div style="font-size:9px;color:${active ? '#02c076' : '#848e9c'}">${state}</div>
                        <div class="total">$${b.total.toFixed(2)}</div>
                        <div style="font-size:10px;color:${pnl >= 0 ? '#02c076' : '#f6465d'}">P&L: ${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}</div>
                    </div>`;
                    if (active) {
                        cActiveH += card;
                        cS += b.total;
                        cPnl += (b.total - 100);
                        cCount += 1;
                    } else {
                        cPausedH += card;
                    }
                });
                const cAvgPnl = cCount ? (cPnl / cCount) : 0;
                let cH = "";
                if (cPausedH) {
                    cH += `<div style="width:100%; max-width:1100px; margin:0 auto 4px; font-size:11px; color:#848e9c; text-align:left;">PAUSED (TOP ROW, NOT IN TOTAL)</div>`;
                    cH += `<div style="width:100%; max-width:1100px; margin:0 auto 8px; display:flex; justify-content:center; gap:8px; flex-wrap:wrap;">${cPausedH}</div>`;
                }
                cH += `<div style="width:100%; max-width:1100px; margin:0 auto 4px; font-size:11px; color:#9aa4b2; text-align:left;">SELECTED (IN TOTAL)</div>`;
                cH += `<div style="width:100%; max-width:1100px; margin:0 auto 8px; display:flex; justify-content:center; gap:8px; flex-wrap:wrap; min-height:86px;">${cActiveH || '<div style="font-size:11px; color:#848e9c; align-self:center;">Click a paused card to select it</div>'}</div>`;
                cH+=`<div class="summary"><div style="font-size:24px;color:#02c076">$${cS.toFixed(2)}</div><div>BASKET TOTAL (ACTIVE)</div><div style="font-size:11px;color:#848e9c;margin-top:6px;">AVG P&L: $${cAvgPnl.toFixed(2)}</div></div>`;
                document.getElementById('ca').innerHTML=cH;
                document.getElementById('logs').innerText = d.logs.join(" | ");
            } catch(e) {}
        }
        // Use a Worker for the timer so the browser never throttles it
        const timerWorker = new Worker(URL.createObjectURL(new Blob(
            ['setInterval(()=>postMessage(1),1000)'],
            {type:'application/javascript'}
        )));
        timerWorker.onmessage = () => {
            updateHeartbeat();
            update();
        };
        updateHeartbeat();
        document.addEventListener('visibilitychange', () => { if (!document.hidden) update(); });
    </script></body></html>
"""
threading.Thread(target=fetch_binance, daemon=True).start()
threading.Thread(target=arena_loop, daemon=True).start()
threading.Thread(target=basket_loop, daemon=True).start()
ReuseTCPServer(("", PORT), H).serve_forever()
