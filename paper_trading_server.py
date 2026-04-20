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


def now_ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


class ArenaState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.models = {
            "Qwen-3": self._mk_model("#02c076", 0.56),
            "DeepSeek": self._mk_model("#00ccff", 0.52),
            "Claude-4": self._mk_model("#f84960", 0.48),
            "GPT-5": self._mk_model("#f0b90b", 0.45),
            "Llama-4": self._mk_model("#ff7d4d", 0.50),
            "Mistral": self._mk_model("#d58bff", 0.51),
            "finance-llama-8b": self._mk_model("#a7f432", 0.54),
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

    def select_model(self, name: str) -> bool:
        with self.lock:
            m = self.models.get(name)
            if not m or m["selected"]:
                return False
            m["selected"] = True
            m["desk"] = self.next_desk()
            m["pos"] = 0.0
            m["entry"] = self.prices["btc"] if m["desk"] == "btc" else self.prices["basket"]
            self.add_log(f"{name} selected to {m['desk'].upper()} desk")
            return True

    def deselect_model(self, name: str) -> bool:
        with self.lock:
            m = self.models.get(name)
            if not m or not m["selected"]:
                return False
            m["selected"] = False
            m["desk"] = None
            m["pos"] = 0.0
            m["entry"] = 0.0
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

            if m["selected"] and random.random() > 0.94:
                action = 1 if random.random() < m["bias"] else -1
                m["pos"] = action * (m["balance"] / max(ref_price, 1.0)) * 0.4
                m["entry"] = ref_price
                side = "LONG" if action > 0 else "SHORT"
                self.add_log(f"{name} [{m['desk'].upper()}]: {side} @ ${ref_price:,.2f}")

            m["preview_pnl"] += (random.random() - 0.5) * 18
            m["preview_pnl"] = max(-500.0, min(500.0, m["preview_pnl"]))

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

        ok = self.state.select_model(model) if self.path == "/api/select" else self.state.deselect_model(model)
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
