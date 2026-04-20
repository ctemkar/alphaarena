#!/usr/bin/env python3
import json
import random
import threading
import time
import urllib.request
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = "127.0.0.1"
PORT = 8000
START_BALANCE = 10_000.0
MAX_LOGS = 60
TICK_SECONDS = 2.0
OLLAMA_URL = "http://127.0.0.1:11434"

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
        self.logs = []
        self.recalc_basket()

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
        btc_before = self.prices["btc"]
        try:
            with urllib.request.urlopen(
                "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", timeout=2.0
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
                self.prices["btc"] = float(payload["price"])
                self.live_feed = True
        except Exception:
            self.live_feed = False
            self.prices["btc"] *= 1 + (random.random() - 0.5) * 0.0015

        ret = (self.prices["btc"] - btc_before) / btc_before if btc_before else 0.0
        self.prices["eth"] *= 1 + ret * 0.85 + (random.random() - 0.5) * 0.0012
        self.prices["sol"] *= 1 + ret * 1.25 + (random.random() - 0.5) * 0.0025
        self.prices["bnb"] *= 1 + ret * 0.65 + (random.random() - 0.5) * 0.0010
        self.recalc_basket()

    def step_models(self) -> None:
        for name, m in self.models.items():
            ref_price = self.prices["basket"] if m["desk"] == "basket" else self.prices["btc"]

            if m["selected"] and m["pos"] != 0:
                m["balance"] += (ref_price - m["entry"]) * m["pos"]
                m["entry"] = ref_price

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

            m["preview_pnl"] += (random.random() - 0.5) * 18
            m["preview_pnl"] = max(-500.0, min(500.0, m["preview_pnl"]))

    def _apply_ollama_signal(self, name: str, ollama_tag: str, ref_price: float) -> None:
        """Called in a background thread. Queries Ollama then applies the result."""
        action = _ollama_signal(ollama_tag, ref_price, self.models[name].get("desk", "btc"))
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
            self.add_log(f"{name} [{desk}]: {side} @ ${ref_price:,.2f} [AI]")

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
                    "mode": "PAPER",
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
