#!/usr/bin/env python3
import json
import os
import queue
import random
import threading
import time
import hmac
import hashlib
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = "127.0.0.1"
PORT = 8000
START_BALANCE = 10_000.0
MAX_LOGS = 60
TICK_SECONDS = 3.0
OLLAMA_URL = "http://127.0.0.1:11434"
BINANCE_BASE_URL = "https://api.binance.com"


def _load_dotenv(path: str = ".env") -> None:
    """Lightweight .env loader so stdlib server can read local secrets."""
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


_load_dotenv()
BINANCE_API_KEY = os.getenv("EXCH_BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("EXCH_BINANCE_API_SECRET", "")
LIVE_TRADING_ENABLED = os.getenv("ALPHA_LIVE_TRADING", "0").strip().lower() in {"1", "true", "yes", "on"}
ALLOW_SPOT_SHORT = os.getenv("ALPHA_ALLOW_SPOT_SHORT", "0").strip().lower() in {"1", "true", "yes", "on"}

try:
    LIVE_ORDER_USD = float(os.getenv("ALPHA_LIVE_ORDER_USD", "50"))
except ValueError:
    LIVE_ORDER_USD = 50.0
try:
    MAX_ORDER_USD = float(os.getenv("ALPHA_MAX_ORDER_USD", "50"))
except ValueError:
    MAX_ORDER_USD = 50.0
try:
    DAILY_LOSS_LIMIT_USD = float(os.getenv("ALPHA_DAILY_LOSS_LIMIT_USD", "100"))
except ValueError:
    DAILY_LOSS_LIMIT_USD = 100.0

try:
    START_BALANCE = float(os.getenv("ALPHA_START_BALANCE_USD", str(START_BALANCE)))
except ValueError:
    START_BALANCE = 10_000.0

# Map model name  (as it appears in ARENA_DATA) -> Ollama model tag
OLLAMA_MODELS: dict[str, str] = {
    "Qwen-3":          "qwen3.5:latest",
    "Qwen-2.5-Coder":  "qwen2.5-coder:7b",
    "Qwen3-Coder":     "qwen3-coder:latest",
    "DeepSeek-R1":     "deepseek-r1:8b",
    "Gemma-4":         "gemma4:latest",
    "Phi-3":           "phi3:latest",
    "Llama-4":         "llama3:latest",
    "Llama-3.2":       "llama3.2:3b",
    "Mistral":         "mistral:latest",
    "finance-llama-8b":"martain7r/finance-llama-8b:fp16",
}


def _ollama_signal(ollama_tag: str, price: float, desk: str) -> int:
    """Query Ollama for a trading signal. Returns +1 LONG, -1 SHORT, 0 HOLD."""
    asset = "BTC" if desk == "btc" else "BASKET (BTC/ETH/SOL/BNB)"
    prompt = (
        f"Current {asset} price: ${price:,.2f}. "
        "Should a short-term trader go LONG, SHORT, or HOLD right now? "
        "Reply with exactly one word: LONG, SHORT, or HOLD."
    )
    payload = json.dumps({"model": ollama_tag, "prompt": prompt, "stream": False}).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            result = json.loads(resp.read().decode())
            text = result.get("response", "").strip().upper()
            if "LONG" in text:
                return 1
            if "SHORT" in text:
                return -1
            return 0
    except Exception:
        return 0  # fall back to HOLD on any error


def now_ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


# ── Movement tracking ────────────────────────────────────────────────────────
MOVEMENT_LOG_FILE = "movement_log.jsonl"
_movement_log_lock = threading.Lock()


def _log_movement(record: dict) -> None:
    """Append one movement record to movement_log.jsonl (thread-safe).

    Each line is a self-contained JSON object. Fields common to all records:
      ts      – ISO-8601 timestamp
      type    – "price" | "signal" | "pnl"

    price:  btc, eth, sol, bnb, basket (all floats), feed ("live"|"sim")
    signal: model, desk ("btc"|"basket"), signal ("LONG"|"SHORT"|"HOLD"),
            source ("ai"|"sim"), price (float)
    pnl:    model, desk, price, pos (float), pnl_delta, balance
    """
    record.setdefault("ts", datetime.now().isoformat())
    line = json.dumps(record, separators=(",", ":")) + "\n"
    with _movement_log_lock:
        with open(MOVEMENT_LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line)


class ArenaState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.models = {
            "Qwen-3":          self._mk_model("#02c076", 0.56),
            "Qwen-2.5-Coder":  self._mk_model("#00e5cc", 0.53),
            "Qwen3-Coder":     self._mk_model("#00bfa5", 0.55),
            "DeepSeek-R1":     self._mk_model("#00ccff", 0.52),
            "Gemma-4":         self._mk_model("#f84960", 0.48),
            "Phi-3":           self._mk_model("#f0b90b", 0.45),
            "Llama-4":         self._mk_model("#ff7d4d", 0.50),
            "Llama-3.2":       self._mk_model("#ffaa44", 0.49),
            "Mistral":         self._mk_model("#d58bff", 0.51),
            "finance-llama-8b":self._mk_model("#a7f432", 0.54),
        }
        self.prices = {
            "btc": 84_000.0,
            "eth": 4_200.0,
            "sol": 170.0,
            "bnb": 630.0,
            "basket": 0.0,
        }
        self.live_feed = False
        self.live_trading = bool(LIVE_TRADING_ENABLED and BINANCE_API_KEY and BINANCE_API_SECRET)
        self.live_order_usd = max(5.0, min(LIVE_ORDER_USD, MAX_ORDER_USD))
        self.max_order_usd = max(5.0, MAX_ORDER_USD)
        self.daily_loss_limit_usd = max(10.0, DAILY_LOSS_LIMIT_USD)
        self.kill_switch = False
        self.halt_reason = ""
        self.guardrail_day = datetime.utcnow().strftime("%Y-%m-%d")
        self.live_order_queue: queue.Queue[tuple[str, str, str, float]] = queue.Queue()
        self.logs = []
        self.recalc_basket()
        if self.live_trading:
            threading.Thread(target=self._live_order_worker, daemon=True).start()

    def _desk_symbols(self, desk: str) -> list[str]:
        if desk == "basket":
            return ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
        return ["BTCUSDT"]

    def _portfolio_pnl(self) -> float:
        return sum((m["balance"] - START_BALANCE) for m in self.models.values() if m["selected"])

    def _evaluate_guardrails(self) -> None:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        if today != self.guardrail_day:
            self.guardrail_day = today
            self.kill_switch = False
            self.halt_reason = ""
        if not self.kill_switch and self._portfolio_pnl() <= -self.daily_loss_limit_usd:
            self.kill_switch = True
            self.halt_reason = f"Daily loss guardrail hit (${self.daily_loss_limit_usd:.2f})"
            self.add_log(f"LIVE TRADING HALTED: {self.halt_reason}")

    def _binance_signed_post(self, path: str, params: dict) -> dict:
        if not BINANCE_API_KEY or not BINANCE_API_SECRET:
            raise RuntimeError("Binance API credentials are missing")
        payload = dict(params)
        payload["timestamp"] = int(time.time() * 1000)
        payload["recvWindow"] = 5000
        query = urllib.parse.urlencode(payload)
        signature = hmac.new(BINANCE_API_SECRET.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()
        body = f"{query}&signature={signature}".encode("utf-8")
        req = urllib.request.Request(
            f"{BINANCE_BASE_URL}{path}",
            data=body,
            headers={
                "X-MBX-APIKEY": BINANCE_API_KEY,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _place_live_market_order(self, symbol: str, side: str, quote_usd: float) -> dict:
        qty = max(5.0, min(quote_usd, self.max_order_usd))
        return self._binance_signed_post(
            "/api/v3/order",
            {
                "symbol": symbol,
                "side": side,
                "type": "MARKET",
                "quoteOrderQty": f"{qty:.2f}",
            },
        )

    def _live_order_worker(self) -> None:
        """Executes live orders sequentially to avoid parallel request bursts."""
        while True:
            model_name, symbol, side_label, order_usd = self.live_order_queue.get()
            try:
                side = "BUY" if side_label == "LONG" else "SELL"
                result = self._place_live_market_order(symbol, side, order_usd)
                order_id = result.get("orderId", "?")
                with self.lock:
                    self.add_log(f"{model_name} LIVE {symbol} {side_label} ${order_usd:.2f} (order {order_id})")
            except Exception as exc:
                with self.lock:
                    self.add_log(f"{model_name} LIVE {symbol} {side_label} failed: {exc}")
            finally:
                self.live_order_queue.task_done()
                # Small delay protects against exchange rate bursts.
                time.sleep(0.2)

    def _execute_live_signal(self, model_name: str, desk: str, side_label: str) -> None:
        if side_label not in {"LONG", "SHORT"}:
            return
        if side_label == "SHORT" and not ALLOW_SPOT_SHORT:
            with self.lock:
                self.add_log(f"{model_name} LIVE SHORT skipped (spot mode only supports safe long-by-default)")
            return
        with self.lock:
            self._evaluate_guardrails()
            if not self.live_trading:
                return
            if self.kill_switch:
                return
            symbols = self._desk_symbols(desk)
            order_usd = self.live_order_usd

        for symbol in symbols:
            self.live_order_queue.put((model_name, symbol, side_label, order_usd))
        with self.lock:
            self.add_log(f"{model_name} queued LIVE {side_label} on {', '.join(symbols)} @ ${order_usd:.2f}")

    @staticmethod
    def _mk_model(color: str, bias: float) -> dict:
        return {
            "color": color,
            "bias": bias,
            "selected": False,
            "desk": None,
            "balance": START_BALANCE,
            "pos": 0.0,
            "entry": 0.0,
            "preview_pnl": 0.0,
            "signal_source": "sim",   # 'sim' | 'ai'
            "last_signal": "IDLE",    # LONG | SHORT | HOLD | IDLE
        }

    def add_log(self, message: str) -> None:
        self.logs.insert(0, f"[{now_ts()}] {message}")
        del self.logs[MAX_LOGS:]

    def recalc_basket(self) -> None:
        p = self.prices
        p["basket"] = (p["btc"] + p["eth"] * 12 + p["sol"] * 300 + p["bnb"] * 80) / 4

    def selected_count(self, desk: str) -> int:
        return sum(1 for m in self.models.values() if m["selected"] and m["desk"] == desk)

    def next_desk(self) -> str:
        return "btc" if self.selected_count("btc") <= self.selected_count("basket") else "basket"

    def select_model(self, name: str, desk: str = "btc") -> bool:
        with self.lock:
            m = self.models.get(name)
            if not m or m["selected"]:
                return False
            assigned_desk = desk if desk in ("btc", "basket") else "btc"
            ref_price = self.prices["btc"] if assigned_desk == "btc" else self.prices["basket"]
            action = 1 if random.random() < m["bias"] else -1
            m["selected"] = True
            m["desk"] = assigned_desk
            # Start with a small initial position so desk P&L moves immediately.
            m["pos"] = action * (m["balance"] / max(ref_price, 1.0)) * 0.3
            m["entry"] = ref_price
            m["signal_source"] = "sim"
            m["last_signal"] = "LONG" if action > 0 else "SHORT"
            self.add_log(f"{name} selected to {m['desk'].upper()} desk [{m['last_signal']}] @ ${ref_price:,.2f}")
            return True

    def deselect_model(self, name: str) -> bool:
        with self.lock:
            m = self.models.get(name)
            if not m or not m["selected"]:
                return False
            m["selected"] = False
            m["desk"] = None
            # Clear accumulated state so removed models do not carry P&L
            # back into totals if they are selected again later.
            m["balance"] = START_BALANCE
            m["pos"] = 0.0
            m["entry"] = 0.0
            m["signal_source"] = "sim"
            m["last_signal"] = "IDLE"
            self.add_log(f"{name} deselected")
            return True

    def refresh_prices(self) -> None:
        prev_prices = self.prices.copy()
        try:
            symbols = json.dumps(["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"])
            q = urllib.parse.urlencode({"symbols": symbols})
            req = urllib.request.Request(
                f"https://api.binance.com/api/v3/ticker/price?{q}",
                headers={"X-MBX-APIKEY": BINANCE_API_KEY} if BINANCE_API_KEY else {},
            )
            with urllib.request.urlopen(req, timeout=2.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
                px = {row["symbol"]: float(row["price"]) for row in payload if "symbol" in row and "price" in row}
                self.prices["btc"] = px.get("BTCUSDT", self.prices["btc"])
                self.prices["eth"] = px.get("ETHUSDT", self.prices["eth"])
                self.prices["sol"] = px.get("SOLUSDT", self.prices["sol"])
                self.prices["bnb"] = px.get("BNBUSDT", self.prices["bnb"])
                self.live_feed = all(sym in px for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"))
        except Exception:
            self.live_feed = False
            btc_before = prev_prices["btc"]
            self.prices["btc"] = prev_prices["btc"] * (1 + (random.random() - 0.5) * 0.0015)
            ret = (self.prices["btc"] - btc_before) / btc_before if btc_before else 0.0
            self.prices["eth"] = prev_prices["eth"] * (1 + ret * 0.85 + (random.random() - 0.5) * 0.0012)
            self.prices["sol"] = prev_prices["sol"] * (1 + ret * 1.25 + (random.random() - 0.5) * 0.0025)
            self.prices["bnb"] = prev_prices["bnb"] * (1 + ret * 0.65 + (random.random() - 0.5) * 0.0010)

        self.recalc_basket()
        # Record a price snapshot every tick for movement analysis.
        _log_movement({
            "type": "price",
            "btc":    round(self.prices["btc"], 2),
            "eth":    round(self.prices["eth"], 2),
            "sol":    round(self.prices["sol"], 4),
            "bnb":    round(self.prices["bnb"], 4),
            "basket": round(self.prices["basket"], 2),
            "feed":   "live" if self.live_feed else "sim",
        })

    def step_models(self) -> None:
        for name, m in self.models.items():
            ref_price = self.prices["basket"] if m["desk"] == "basket" else self.prices["btc"]

            if m["selected"] and m["pos"] != 0:
                pnl_delta = (ref_price - m["entry"]) * m["pos"]
                m["balance"] += pnl_delta
                m["entry"] = ref_price
                # Log every significant P&L change for movement analysis.
                if abs(pnl_delta) >= 0.01:
                    _log_movement({
                        "type":      "pnl",
                        "model":     name,
                        "desk":      m["desk"],
                        "price":     round(ref_price, 2),
                        "pos":       round(m["pos"], 8),
                        "pnl_delta": round(pnl_delta, 4),
                        "balance":   round(m["balance"], 4),
                    })

            # Decide whether to make a new trade this tick
            should_trade = m["selected"] and random.random() > 0.94
            if should_trade:
                ollama_tag = OLLAMA_MODELS.get(name)
                if ollama_tag:
                    # Real AI signal — run in a fire-and-forget thread so it
                    # never blocks the main tick loop.
                    threading.Thread(
                        target=self._apply_ollama_signal,
                        args=(name, ollama_tag, ref_price),
                        daemon=True,
                    ).start()
                else:
                    action = 1 if random.random() < m["bias"] else -1
                    m["pos"] = action * (m["balance"] / max(ref_price, 1.0)) * 0.4
                    m["entry"] = ref_price
                    side = "LONG" if action > 0 else "SHORT"
                    m["signal_source"] = "sim"
                    m["last_signal"] = side
                    self.add_log(f"{name} [{m['desk'].upper()}]: {side} @ ${ref_price:,.2f} [sim]")
                    _log_movement({
                        "type":   "signal",
                        "model":  name,
                        "desk":   m["desk"],
                        "signal": side,
                        "source": "sim",
                        "price":  round(ref_price, 2),
                    })
                    self._execute_live_signal(name, m["desk"] or "btc", side)

            m["preview_pnl"] += (random.random() - 0.5) * 18
            m["preview_pnl"] = max(-500.0, min(500.0, m["preview_pnl"]))

    def _apply_ollama_signal(self, name: str, ollama_tag: str, ref_price: float) -> None:
        """Called in a background thread. Queries Ollama then applies the result."""
        action = _ollama_signal(ollama_tag, ref_price, self.models[name].get("desk", "btc"))
        live_desk = "btc"
        live_side = "HOLD"
        with self.lock:
            m = self.models.get(name)
            if not m or not m["selected"]:
                return
            if action != 0:
                m["pos"] = action * (m["balance"] / max(ref_price, 1.0)) * 0.4
                m["entry"] = ref_price
            side = "LONG" if action == 1 else ("SHORT" if action == -1 else "HOLD")
            m["signal_source"] = "ai"
            m["last_signal"] = side
            desk = (m.get("desk") or "btc").upper()
            live_desk = (m.get("desk") or "btc")
            live_side = side
            self.add_log(f"{name} [{desk}]: {side} @ ${ref_price:,.2f} [AI]")
            _log_movement({
                "type":   "signal",
                "model":  name,
                "desk":   live_desk,
                "signal": side,
                "source": "ai",
                "price":  round(ref_price, 2),
            })
        if live_side in {"LONG", "SHORT"}:
            self._execute_live_signal(name, live_desk, live_side)

    def snapshot(self) -> dict:
        with self.lock:
            btc_equity = sum(m["balance"] for m in self.models.values() if m["selected"] and m["desk"] == "btc")
            basket_equity = sum(m["balance"] for m in self.models.values() if m["selected"] and m["desk"] == "basket")
            return {
                "prices": {
                    "btc": self.prices["btc"],
                    "basket": self.prices["basket"],
                },
                "status": {
                    "feed": "LIVE" if self.live_feed else "SIM",
                    "mode": "LIVE" if self.live_trading else "PAPER",
                    "kill_switch": self.kill_switch,
                    "halt_reason": self.halt_reason,
                    "order_usd": self.live_order_usd,
                    "order_queue": self.live_order_queue.qsize(),
                    "ollama_models": list(OLLAMA_MODELS.keys()),
                },
                "desk_equity": {
                    "btc": btc_equity,
                    "basket": basket_equity,
                },
                "models": self.models,
                "logs": self.logs[:],
                "ts": now_ts(),
            }

    def tick(self) -> None:
        with self.lock:
            self.refresh_prices()
            self.step_models()


def simulation_loop(state: ArenaState) -> None:
    while True:
        state.tick()
        time.sleep(TICK_SECONDS)


class ArenaHandler(SimpleHTTPRequestHandler):
    state: ArenaState = None

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in {"/api/state", "/data"}:
            self._json(200, self.state.snapshot())
            return
        if path == "/":
            self.path = "/alpha_arena_live.html"
        super().do_GET()

    def do_POST(self) -> None:
        if self.path not in {"/api/select", "/api/deselect"}:
            self._json(404, {"ok": False, "error": "Not found"})
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        try:
            data = json.loads(self.rfile.read(content_length).decode("utf-8")) if content_length else {}
        except Exception:
            self._json(400, {"ok": False, "error": "Invalid JSON"})
            return

        model = data.get("model", "")
        if not isinstance(model, str) or not model:
            self._json(400, {"ok": False, "error": "model is required"})
            return

        if self.path == "/api/select":
            desk = data.get("desk", "btc")
            ok = self.state.select_model(model, desk)
        else:
            ok = self.state.deselect_model(model)
        if not ok:
            self._json(409, {"ok": False, "error": "No state change"})
            return
        self._json(200, {"ok": True})


def main() -> None:
    state = ArenaState()
    ArenaHandler.state = state

    worker = threading.Thread(target=simulation_loop, args=(state,), daemon=True)
    worker.start()

    web_root = Path(__file__).resolve().parent
    server = ThreadingHTTPServer((HOST, PORT), ArenaHandler)
    print(f"Serving Alpha Arena paper backend on http://{HOST}:{PORT}")
    print(f"Web root: {web_root}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
