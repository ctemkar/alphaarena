#!/usr/bin/env python3
import json
import os
import queue
import random
import threading
import time
import hmac
import hashlib
import urllib.error
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
    ANALYTICS_FEE_BPS = float(os.getenv("ALPHA_ANALYTICS_FEE_BPS", "10"))
except ValueError:
    ANALYTICS_FEE_BPS = 10.0
try:
    ANALYTICS_SLIPPAGE_BPS = float(os.getenv("ALPHA_ANALYTICS_SLIPPAGE_BPS", "5"))
except ValueError:
    ANALYTICS_SLIPPAGE_BPS = 5.0

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
        self.summary_path = Path("daily_summary.json")
        self._summary_cache_stamp: tuple[str, int, int] | None = None
        self._summary_cache = self._default_daily_summary(datetime.now().strftime("%Y-%m-%d"))
        self.logs = []
        self.recalc_basket()
        if self.live_trading:
            threading.Thread(target=self._live_order_worker, daemon=True).start()

    @staticmethod
    def _default_daily_summary(day: str) -> dict:
        return {
            "day": day,
            "samples": 0,
            "signals": 0,
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate_pct": 0.0,
            "expectancy_usd": 0.0,
            "max_drawdown_usd": 0.0,
            "total_pnl_usd": 0.0,
            "best_model": "-",
            "best_model_pnl_usd": 0.0,
            "worst_model": "-",
            "worst_model_pnl_usd": 0.0,
            "fee_bps": ANALYTICS_FEE_BPS,
            "slippage_bps": ANALYTICS_SLIPPAGE_BPS,
        }

    def _get_daily_summary(self) -> dict:
        day = datetime.now().strftime("%Y-%m-%d")
        log_path = Path(MOVEMENT_LOG_FILE)
        if not log_path.exists():
            self._summary_cache_stamp = None
            self._summary_cache = self._default_daily_summary(day)
            return self._summary_cache

        stat = log_path.stat()
        stamp = (day, stat.st_mtime_ns, stat.st_size)
        if self._summary_cache_stamp == stamp:
            return self._summary_cache

        summary = self._default_daily_summary(day)
        model_pnl: dict[str, float] = {}
        trade_pnls: list[float] = []
        wins = 0
        losses = 0
        signals = 0
        samples = 0
        trades = 0

        for raw in log_path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                rec = json.loads(raw)
            except Exception:
                continue

            ts = str(rec.get("ts", ""))
            if not ts.startswith(day):
                continue

            rtype = rec.get("type")
            if rtype == "price":
                samples += 1
                continue

            if rtype == "signal" and rec.get("signal") in {"LONG", "SHORT"}:
                signals += 1
                continue

            if rtype == "trade":
                try:
                    delta = float(rec.get("trade_pnl", 0.0))
                except (TypeError, ValueError):
                    continue
                trades += 1
                trade_pnls.append(delta)
                if delta > 0:
                    wins += 1
                elif delta < 0:
                    losses += 1
                model = str(rec.get("model", ""))
                if model:
                    model_pnl[model] = model_pnl.get(model, 0.0) + delta

        total_pnl = sum(trade_pnls)
        expectancy = (total_pnl / trades) if trades else 0.0
        decision_trades = wins + losses
        win_rate = (wins / decision_trades * 100.0) if decision_trades else 0.0

        # Drawdown on cumulative realized P&L trajectory.
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for d in trade_pnls:
            equity += d
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > max_dd:
                max_dd = dd

        best_model = "-"
        worst_model = "-"
        best_pnl = 0.0
        worst_pnl = 0.0
        if model_pnl:
            best_model, best_pnl = max(model_pnl.items(), key=lambda kv: kv[1])
            worst_model, worst_pnl = min(model_pnl.items(), key=lambda kv: kv[1])

        summary.update({
            "samples": samples,
            "signals": signals,
            "trades": trades,
            "wins": wins,
            "losses": losses,
            "win_rate_pct": round(win_rate, 2),
            "expectancy_usd": round(expectancy, 4),
            "max_drawdown_usd": round(max_dd, 4),
            "total_pnl_usd": round(total_pnl, 4),
            "best_model": best_model,
            "best_model_pnl_usd": round(best_pnl, 4),
            "worst_model": worst_model,
            "worst_model_pnl_usd": round(worst_pnl, 4),
        })

        self._summary_cache_stamp = stamp
        self._summary_cache = summary
        try:
            self.summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        except Exception:
            pass
        return summary

    def _desk_symbols(self, desk: str) -> list[str]:
        if desk == "basket":
            return ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
        return ["BTCUSDT"]

    def _portfolio_pnl(self) -> float:
        total = 0.0
        for m in self.models.values():
            for desk in ("btc", "basket"):
                slot = m["desk_state"][desk]
                if slot["selected"]:
                    total += (slot["balance"] - START_BALANCE)
        return total

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

    def _binance_signed_request(self, method: str, path: str, params: dict) -> dict:
        if not BINANCE_API_KEY or not BINANCE_API_SECRET:
            raise RuntimeError("Binance API credentials are missing")
        payload = dict(params)
        payload["timestamp"] = int(time.time() * 1000)
        payload["recvWindow"] = 5000
        query = urllib.parse.urlencode(payload)
        signature = hmac.new(BINANCE_API_SECRET.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()
        signed_query = f"{query}&signature={signature}"
        headers = {
            "X-MBX-APIKEY": BINANCE_API_KEY,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        if method == "GET":
            req = urllib.request.Request(
                f"{BINANCE_BASE_URL}{path}?{signed_query}",
                headers=headers,
                method="GET",
            )
        else:
            req = urllib.request.Request(
                f"{BINANCE_BASE_URL}{path}",
                data=signed_query.encode("utf-8"),
                headers=headers,
                method=method,
            )
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw)
                msg = payload.get("msg") or raw
                code = payload.get("code")
                detail = f"Binance HTTP {exc.code}"
                if code is not None:
                    detail += f" code {code}"
                raise RuntimeError(f"{detail}: {msg}") from None
            except json.JSONDecodeError:
                raise RuntimeError(f"Binance HTTP {exc.code}: {raw}") from None

    def _binance_signed_get(self, path: str, params: dict) -> dict:
        return self._binance_signed_request("GET", path, params)

    def _binance_signed_post(self, path: str, params: dict) -> dict:
        return self._binance_signed_request("POST", path, params)

    def _get_spot_free_balance(self, asset: str) -> float:
        account = self._binance_signed_get("/api/v3/account", {})
        for row in account.get("balances", []):
            if row.get("asset") == asset:
                try:
                    return float(row.get("free", "0") or 0.0)
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

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

        if side_label == "LONG":
            required_usdt = order_usd * len(symbols)
            try:
                free_usdt = self._get_spot_free_balance("USDT")
            except Exception as exc:
                with self.lock:
                    self.add_log(f"{model_name} LIVE precheck failed: {exc}")
                return
            if free_usdt + 1e-9 < required_usdt:
                with self.lock:
                    self.add_log(
                        f"{model_name} LIVE LONG skipped (insufficient USDT: need ${required_usdt:.2f}, free ${free_usdt:.2f})"
                    )
                return

        for symbol in symbols:
            self.live_order_queue.put((model_name, symbol, side_label, order_usd))
        with self.lock:
            self.add_log(f"{model_name} queued LIVE {side_label} on {', '.join(symbols)} @ ${order_usd:.2f}")

    @staticmethod
    def _mk_model(color: str, bias: float) -> dict:
        base_slot = {
            "selected": False,
            "balance": START_BALANCE,
            "realized_pnl": 0.0,
            "pos": 0.0,
            "entry": 0.0,
            "preview_pnl": 0.0,
            "signal_source": "sim",  # 'sim' | 'ai'
            "last_signal": "IDLE",   # LONG | SHORT | HOLD | IDLE
            "trade_side": "FLAT",    # FLAT | LONG | SHORT
            "trade_open_balance": START_BALANCE,
        }
        return {
            "color": color,
            "bias": bias,
            "desk_state": {
                "btc": dict(base_slot),
                "basket": dict(base_slot),
            },
        }

    def _close_trade_if_open(self, model_name: str, desk: str, slot: dict, reason: str, price: float) -> None:
        side = slot.get("trade_side", "FLAT")
        if side not in {"LONG", "SHORT"}:
            return
        trade_pnl = slot["balance"] - slot.get("trade_open_balance", slot["balance"])
        _log_movement({
            "type": "trade",
            "model": model_name,
            "desk": desk,
            "side": side,
            "close_reason": reason,
            "price": round(price, 2),
            "trade_pnl": round(trade_pnl, 4),
            "win": trade_pnl > 0,
        })
        slot["trade_side"] = "FLAT"
        slot["trade_open_balance"] = slot["balance"]

    def _roll_trade_on_signal(self, model_name: str, desk: str, slot: dict, new_side: str, price: float, reason: str) -> None:
        old_side = slot.get("trade_side", "FLAT")
        if old_side in {"LONG", "SHORT"} and old_side != new_side:
            self._close_trade_if_open(model_name, desk, slot, reason, price)
        if new_side in {"LONG", "SHORT"} and old_side != new_side:
            slot["trade_side"] = new_side
            slot["trade_open_balance"] = slot["balance"]

    def add_log(self, message: str) -> None:
        self.logs.insert(0, f"[{now_ts()}] {message}")
        del self.logs[MAX_LOGS:]

    def recalc_basket(self) -> None:
        p = self.prices
        p["basket"] = (p["btc"] + p["eth"] * 12 + p["sol"] * 300 + p["bnb"] * 80) / 4

    def selected_count(self, desk: str) -> int:
        return sum(1 for m in self.models.values() if m["desk_state"][desk]["selected"])

    def next_desk(self) -> str:
        return "btc" if self.selected_count("btc") <= self.selected_count("basket") else "basket"

    def select_model(self, name: str, desk: str = "btc") -> bool:
        with self.lock:
            m = self.models.get(name)
            if not m:
                return False
            assigned_desk = desk if desk in ("btc", "basket") else "btc"
            slot = m["desk_state"][assigned_desk]
            if slot["selected"]:
                return False
            ref_price = self.prices["btc"] if assigned_desk == "btc" else self.prices["basket"]
            action = 1 if random.random() < m["bias"] else -1
            slot["selected"] = True
            # Start with a small initial position so desk P&L moves immediately.
            slot["pos"] = action * (slot["balance"] / max(ref_price, 1.0)) * 0.3
            slot["entry"] = ref_price
            slot["signal_source"] = "sim"
            slot["last_signal"] = "LONG" if action > 0 else "SHORT"
            self._roll_trade_on_signal(name, assigned_desk, slot, slot["last_signal"], ref_price, "select")
            self.add_log(f"{name} selected to {assigned_desk.upper()} desk [{slot['last_signal']}] @ ${ref_price:,.2f}")
            return True

    def deselect_model(self, name: str, desk: str | None = None) -> bool:
        with self.lock:
            m = self.models.get(name)
            if not m:
                return False
            desks = [desk] if desk in ("btc", "basket") else ["btc", "basket"]
            changed = False
            for d in desks:
                slot = m["desk_state"][d]
                if not slot["selected"]:
                    continue
                ref_price = self.prices["btc"] if d == "btc" else self.prices["basket"]
                self._close_trade_if_open(name, d, slot, "deselect", ref_price)
                slot["realized_pnl"] += slot["balance"] - START_BALANCE
                slot["selected"] = False
                slot["balance"] = START_BALANCE
                slot["pos"] = 0.0
                slot["entry"] = 0.0
                slot["signal_source"] = "sim"
                slot["last_signal"] = "IDLE"
                slot["trade_side"] = "FLAT"
                slot["trade_open_balance"] = START_BALANCE
                self.add_log(f"{name} deselected from {d.upper()} desk")
                changed = True
            return changed

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
            for desk in ("btc", "basket"):
                slot = m["desk_state"][desk]
                ref_price = self.prices["basket"] if desk == "basket" else self.prices["btc"]

                if slot["selected"] and slot["pos"] != 0:
                    pnl_delta = (ref_price - slot["entry"]) * slot["pos"]
                    slot["balance"] += pnl_delta
                    slot["entry"] = ref_price
                    # Log every significant P&L change for movement analysis.
                    if abs(pnl_delta) >= 0.01:
                        _log_movement({
                            "type":      "pnl",
                            "model":     name,
                            "desk":      desk,
                            "price":     round(ref_price, 2),
                            "pos":       round(slot["pos"], 8),
                            "pnl_delta": round(pnl_delta, 4),
                            "balance":   round(slot["balance"], 4),
                        })

                # Decide whether to make a new trade this tick (6% chance per tick = ~1 per 50s)
                should_trade = slot["selected"] and random.random() > 0.88
                if should_trade:
                    ollama_tag = OLLAMA_MODELS.get(name)
                    if ollama_tag:
                        # Real AI signal — run in a fire-and-forget thread so it
                        # never blocks the main tick loop.
                        threading.Thread(
                            target=self._apply_ollama_signal,
                            args=(name, desk, ollama_tag, ref_price),
                            daemon=True,
                        ).start()
                    else:
                        # 6% HOLD probability closes the trade and flattens position.
                        rand = random.random()
                        if rand > 0.94:
                            action = 1 if random.random() < m["bias"] else -1
                            side = "LONG" if action > 0 else "SHORT"
                        elif rand > 0.88:
                            action = 0
                            side = "HOLD"
                        else:
                            action = 1 if random.random() < m["bias"] else -1
                            side = "LONG" if action > 0 else "SHORT"
                        slot["signal_source"] = "sim"
                        slot["last_signal"] = side
                        if side == "HOLD":
                            # Close any open trade and flatten position.
                            self._close_trade_if_open(name, desk, slot, "hold_signal", ref_price)
                            slot["pos"] = 0.0
                        else:
                            slot["pos"] = action * (slot["balance"] / max(ref_price, 1.0)) * 0.4
                            slot["entry"] = ref_price
                            self._roll_trade_on_signal(name, desk, slot, side, ref_price, "signal_flip")
                            self._execute_live_signal(name, desk, side)
                        self.add_log(f"{name} [{desk.upper()}]: {side} @ ${ref_price:,.2f} [sim]")
                        _log_movement({
                            "type":   "signal",
                            "model":  name,
                            "desk":   desk,
                            "signal": side,
                            "source": "sim",
                            "price":  round(ref_price, 2),
                        })

                slot["preview_pnl"] += (random.random() - 0.5) * 18

    def _apply_ollama_signal(self, name: str, desk_key: str, ollama_tag: str, ref_price: float) -> None:
        action = _ollama_signal(ollama_tag, ref_price, desk_key)
        live_desk = desk_key
        live_side = "HOLD"
        with self.lock:
            m = self.models.get(name)
            if not m:
                return
            slot = m["desk_state"][desk_key]
            if not slot["selected"]:
                return
            side = "LONG" if action == 1 else ("SHORT" if action == -1 else "HOLD")
            slot["signal_source"] = "ai"
            slot["last_signal"] = side
            if side == "HOLD":
                # HOLD closes the open trade and flattens position.
                self._close_trade_if_open(name, desk_key, slot, "hold_signal", ref_price)
                slot["pos"] = 0.0
            else:
                slot["pos"] = action * (slot["balance"] / max(ref_price, 1.0)) * 0.4
                slot["entry"] = ref_price
                self._roll_trade_on_signal(name, desk_key, slot, side, ref_price, "signal_flip")
            desk = desk_key.upper()
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
            btc_equity = sum(m["desk_state"]["btc"]["balance"] for m in self.models.values() if m["desk_state"]["btc"]["selected"])
            basket_equity = sum(m["desk_state"]["basket"]["balance"] for m in self.models.values() if m["desk_state"]["basket"]["selected"])
            btc_pnl = sum(
                m["desk_state"]["btc"].get("realized_pnl", 0.0)
                + (m["desk_state"]["btc"]["balance"] - START_BALANCE if m["desk_state"]["btc"]["selected"] else 0.0)
                for m in self.models.values()
            )
            basket_pnl = sum(
                m["desk_state"]["basket"].get("realized_pnl", 0.0)
                + (m["desk_state"]["basket"]["balance"] - START_BALANCE if m["desk_state"]["basket"]["selected"] else 0.0)
                for m in self.models.values()
            )
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
                "desk_pnl": {
                    "btc": btc_pnl,
                    "basket": basket_pnl,
                },
                "models": self.models,
                "start_balance": START_BALANCE,
                "daily_summary": self._get_daily_summary(),
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
            desk = data.get("desk")
            ok = self.state.deselect_model(model, desk if isinstance(desk, str) else None)
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
