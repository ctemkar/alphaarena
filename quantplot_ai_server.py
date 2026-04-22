#!/usr/bin/env python3
import collections
import json
import os
import queue
import random
import math
import shutil
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
MAX_MESSAGE_CENTER = 30
TICK_SECONDS = 3.0
HARD_MAX_ORDER_USD = 10.0
OLLAMA_URL = "http://127.0.0.1:11434"
try:
    OLLAMA_REQUEST_TIMEOUT_SECONDS = float(os.getenv("ALPHA_OLLAMA_TIMEOUT_SECONDS", "60"))
except ValueError:
    OLLAMA_REQUEST_TIMEOUT_SECONDS = 60.0
try:
    OLLAMA_NUM_WORKERS = int(os.getenv("ALPHA_OLLAMA_NUM_WORKERS", "3"))
except ValueError:
    OLLAMA_NUM_WORKERS = 3
BINANCE_BASE_URL     = "https://api.binance.com"       # Spot (not used for trading)
BINANCE_FUTURES_URL  = "https://fapi.binance.com"      # USDT-M Perpetual Futures

# Futures quantity precision (step size) per symbol
_FUTURES_QTY_PRECISION: dict[str, int] = {
    "BTCUSDT": 3,
    "ETHUSDT": 3,
    "SOLUSDT": 1,
    "BNBUSDT": 2,
}
# Exchange notional floors observed for this account/symbol set.
_FUTURES_MIN_NOTIONAL_USD: dict[str, float] = {
    "BTCUSDT": 20.0,
    "ETHUSDT": 20.0,
    "SOLUSDT": 5.0,
    "BNBUSDT": 5.0,
}
# Symbol → key in self.prices
_SYMBOL_PRICE_KEY: dict[str, str] = {
    "BTCUSDT": "btc",
    "ETHUSDT": "eth",
    "SOLUSDT": "sol",
    "BNBUSDT": "bnb",
}


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
BINANCE_API_KEY = os.getenv("EXCH_BINANCE_API_KEY", "") or os.getenv("BINANCE_KEY", "")
BINANCE_API_SECRET = os.getenv("EXCH_BINANCE_API_SECRET", "") or os.getenv("BINANCE_SECRET", "")
LIVE_TRADING_ENABLED = os.getenv("ALPHA_LIVE_TRADING", "0").strip().lower() in {"1", "true", "yes", "on"}
USE_FUTURES = os.getenv("ALPHA_USE_FUTURES", "1").strip().lower() not in {"0", "false", "no", "off"}
try:
    ANALYTICS_FEE_BPS = float(os.getenv("ALPHA_ANALYTICS_FEE_BPS", "10"))
except ValueError:
    ANALYTICS_FEE_BPS = 10.0
try:
    ANALYTICS_SLIPPAGE_BPS = float(os.getenv("ALPHA_ANALYTICS_SLIPPAGE_BPS", "5"))
except ValueError:
    ANALYTICS_SLIPPAGE_BPS = 5.0

try:
    LIVE_ORDER_USD = float(os.getenv("ALPHA_LIVE_ORDER_USD", "10"))
except ValueError:
    LIVE_ORDER_USD = 10.0
try:
    MAX_ORDER_USD = float(os.getenv("ALPHA_MAX_ORDER_USD", "10"))
except ValueError:
    MAX_ORDER_USD = 10.0
try:
    DAILY_LOSS_LIMIT_USD = float(os.getenv("ALPHA_DAILY_LOSS_LIMIT_USD", "100"))
except ValueError:
    DAILY_LOSS_LIMIT_USD = 100.0
PROFIT_LOCK_ENABLED = os.getenv("ALPHA_PROFIT_LOCK_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
try:
    PROFIT_LOCK_USD = float(os.getenv("ALPHA_PROFIT_LOCK_USD", "10"))
except ValueError:
    PROFIT_LOCK_USD = 10.0
try:
    PROFIT_LOCK_COOLDOWN_TICKS = int(os.getenv("ALPHA_PROFIT_LOCK_COOLDOWN_TICKS", "15"))
except ValueError:
    PROFIT_LOCK_COOLDOWN_TICKS = 15
ALLOW_CROSS_SYMBOL_FALLBACK = os.getenv("ALPHA_ALLOW_CROSS_SYMBOL_FALLBACK", "1").strip().lower() in {"1", "true", "yes", "on"}
try:
    BINANCE_PNL_REFRESH_SECONDS = int(os.getenv("ALPHA_BINANCE_PNL_REFRESH_SECONDS", "10"))
except ValueError:
    BINANCE_PNL_REFRESH_SECONDS = 10

try:
    START_BALANCE = float(os.getenv("ALPHA_START_BALANCE_USD", str(START_BALANCE)))
except ValueError:
    START_BALANCE = 10_000.0

REQUIRE_LIVE_FEED = os.getenv("ALPHA_REQUIRE_LIVE_FEED", "1").strip().lower() in {"1", "true", "yes", "on"}
STRICT_NO_SIMULATION = True

AUTO_SELECT_ENABLED = os.getenv("ALPHA_AUTO_SELECT_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
try:
    AUTO_SELECT_TOP_N = int(os.getenv("ALPHA_AUTO_SELECT_TOP_N", "1"))
except ValueError:
    AUTO_SELECT_TOP_N = 1
try:
    AUTO_SELECT_INTERVAL_TICKS = int(os.getenv("ALPHA_AUTO_SELECT_INTERVAL_TICKS", "5"))
except ValueError:
    AUTO_SELECT_INTERVAL_TICKS = 20
try:
    HOLD_REPLACE_STREAK = int(os.getenv("ALPHA_HOLD_REPLACE_STREAK", "5"))
except ValueError:
    HOLD_REPLACE_STREAK = 5
try:
    HOLD_SCORE_PENALTY = float(os.getenv("ALPHA_HOLD_SCORE_PENALTY", "8.0"))
except ValueError:
    HOLD_SCORE_PENALTY = 8.0
try:
    BASE_SIGNAL_CHANCE = float(os.getenv("ALPHA_BASE_SIGNAL_CHANCE", "0.30"))
except ValueError:
    BASE_SIGNAL_CHANCE = 0.30

AGGRESSIVE_MOVEMENT_ENABLED = os.getenv("ALPHA_AGGRESSIVE_MOVEMENT_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
ONE_LIVE_ENTRY_PER_DESK_PER_TICK = os.getenv("ALPHA_ONE_LIVE_ENTRY_PER_DESK_PER_TICK", "1").strip().lower() in {"1", "true", "yes", "on"}
ONE_LIVE_ENTRY_GLOBAL_PER_TICK = os.getenv("ALPHA_ONE_LIVE_ENTRY_GLOBAL_PER_TICK", "1").strip().lower() in {"1", "true", "yes", "on"}
try:
    LIVE_DUPLICATE_COOLDOWN_SECONDS = float(os.getenv("ALPHA_LIVE_DUPLICATE_COOLDOWN_SECONDS", "8"))
except ValueError:
    LIVE_DUPLICATE_COOLDOWN_SECONDS = 8.0
try:
    LIVE_SYMBOL_COOLDOWN_SECONDS = float(os.getenv("ALPHA_LIVE_SYMBOL_COOLDOWN_SECONDS", "10"))
except ValueError:
    LIVE_SYMBOL_COOLDOWN_SECONDS = 10.0
try:
    AGGRESSIVE_MOVE_PCT = float(os.getenv("ALPHA_AGGRESSIVE_MOVE_PCT", "0.35"))
except ValueError:
    AGGRESSIVE_MOVE_PCT = 0.35
try:
    AGGRESSIVE_SIGNAL_MULTIPLIER = float(os.getenv("ALPHA_AGGRESSIVE_SIGNAL_MULTIPLIER", "1.7"))
except ValueError:
    AGGRESSIVE_SIGNAL_MULTIPLIER = 1.7
try:
    AGGRESSIVE_POSITION_MULTIPLIER = float(os.getenv("ALPHA_AGGRESSIVE_POSITION_MULTIPLIER", "1.35"))
except ValueError:
    AGGRESSIVE_POSITION_MULTIPLIER = 1.35

# Minimum recent price move % required to allow a directional trade.
# Below this threshold the market is too flat to overcome round-trip fees (~20 bps),
# so all signals are suppressed to HOLD.  Override via ALPHA_MIN_TRADE_MOVE_PCT.
try:
    MIN_TRADE_MOVE_PCT = float(os.getenv("ALPHA_MIN_TRADE_MOVE_PCT", "0.04"))
except ValueError:
    MIN_TRADE_MOVE_PCT = 0.04

# Minimum momentum needed to convert an AI HOLD into a directional tiebreaker.
MOMENTUM_OVERRIDE_THRESHOLD_PCT = 0.05

# Map model name  (as it appears in ARENA_DATA) -> Ollama model tag
OLLAMA_MODELS: dict[str, str] = {
    "Mistral":     "mistral:latest",
    "Llama-3.2":   "llama3.1:latest",
    "Gemma-4":     "gemma4:latest",
}


def _ollama_signal(
    ollama_tag: str,
    price: float,
    desk: str,
    price_history: list[dict] | None = None,
) -> tuple[int, str]:
    """Query Ollama for a trading signal.

    Returns (signal, error_str) where:
      signal   – +1 LONG, -1 SHORT, 0 HOLD
      error_str – empty string on success, reason string on failure
    """
    asset = "BTC" if desk == "btc" else "BASKET (BTC/ETH/SOL/BNB)"
    key = "btc" if desk == "btc" else "basket"

    # Build trend context from price history
    trend_lines = []
    move_pct = 0.0
    if price_history and len(price_history) >= 2:
        prices_seq = [h[key] for h in price_history if key in h]
        if len(prices_seq) >= 2:
            oldest = prices_seq[0]
            move_pct = (price - oldest) / oldest * 100 if oldest else 0.0
            direction = "up" if move_pct > 0.02 else ("down" if move_pct < -0.02 else "flat")
            trend_lines.append(
                f"Price trend over last ~{len(prices_seq) * 3}s: {direction} "
                f"({move_pct:+.3f}% from ${oldest:,.2f})."
            )
        # Tick-over-tick momentum (last 2 ticks)
        if len(prices_seq) >= 2:
            tick_chg = (prices_seq[-1] - prices_seq[-2]) / prices_seq[-2] * 100 if prices_seq[-2] else 0.0
            trend_lines.append(f"Last tick change: {tick_chg:+.4f}%.")

    context = " ".join(trend_lines)
    # Keep guidance realistic for low-volatility windows while still allowing HOLD in flat action.
    fee_hint = (
        "Round-trip trading cost is ~0.08%. "
        "Go LONG if short-term trend is up, SHORT if short-term trend is down, "
        "and use HOLD only when price action is truly flat or mixed. "
    )
    prompt = (
        f"You are a short-term crypto trader. {asset} current price: ${price:,.2f}. "
        + (context + " " if context else "")
        + fee_hint
        + "Based on this price action, should a short-term trader go LONG, SHORT, or HOLD? "
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
        with urllib.request.urlopen(req, timeout=OLLAMA_REQUEST_TIMEOUT_SECONDS) as resp:
            result = json.loads(resp.read().decode())
            text = result.get("response", "").strip().upper()
            if "LONG" in text:
                return 1, ""
            if "SHORT" in text:
                return -1, ""
            return 0, ""
    except urllib.error.URLError as exc:
        return 0, f"network: {exc.reason}"
    except TimeoutError:
        return 0, "timeout"
    except Exception as exc:
        return 0, str(exc)[:80]


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

        price:  btc, eth, sol, bnb, basket (all floats), feed ("live"|"offline")
    signal: model, desk ("btc"|"basket"), signal ("LONG"|"SHORT"|"HOLD"),
            source ("ai"), price (float)
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
            "Mistral":   self._mk_model("#d58bff", 0.60),
            "Llama-3.2": self._mk_model("#ffaa44", 0.57),
            "Gemma-4":   self._mk_model("#f84960", 0.49),
        }
        self.prices = {
            "btc": 84_000.0,
            "eth": 4_200.0,
            "sol": 170.0,
            "bnb": 630.0,
            "basket": 0.0,
        }
        # Rolling price history: last 20 ticks (~1 min at 3s/tick)
        self.price_history: collections.deque[dict] = collections.deque(maxlen=20)
        self.live_feed = False
        self.live_trading = bool(LIVE_TRADING_ENABLED and BINANCE_API_KEY and BINANCE_API_SECRET)
        self.live_order_usd = max(5.0, min(LIVE_ORDER_USD, MAX_ORDER_USD, HARD_MAX_ORDER_USD))
        self.max_order_usd = max(5.0, min(MAX_ORDER_USD, HARD_MAX_ORDER_USD))
        self.daily_loss_limit_usd = max(10.0, DAILY_LOSS_LIMIT_USD)
        self.profit_lock_enabled = PROFIT_LOCK_ENABLED
        self.profit_lock_usd = max(0.0, PROFIT_LOCK_USD)
        self.profit_lock_cooldown_ticks = max(0, PROFIT_LOCK_COOLDOWN_TICKS)
        self.profit_lock_cooldown_left = 0
        self.profit_lock_anchor = 0.0
        self.profit_lock_reason = ""
        self.allow_cross_symbol_fallback = ALLOW_CROSS_SYMBOL_FALLBACK
        self.require_live_feed = REQUIRE_LIVE_FEED
        self.strict_no_simulation = STRICT_NO_SIMULATION
        self.feed_paused = False
        self.feed_pause_reason = ""
        self.pause_all_desks = False
        self.paused_desks = {"btc": False, "basket": False}
        self.feed_error = ""
        self.kill_switch = False
        self.halt_reason = ""
        self.live_blocked = False       # True once a 401/403 from Binance is seen
        self.live_blocked_reason = ""  # human-readable reason
        self.guardrail_day = datetime.utcnow().strftime("%Y-%m-%d")
        self.live_order_queue: queue.Queue[tuple[str, str, str, str, float]] = queue.Queue()
        # Live fill ledger: keyed by (model_name, desk, symbol)
        self.live_ledger: dict[tuple[str, str, str], dict] = {}
        # Binance position cache from /fapi/v2/positionRisk
        self.live_positions: dict[str, dict] = {}
        self.live_positions_last_refresh: float = 0.0
        self.live_positions_refresh_seconds: float = 15.0
        self.binance_pnl_refresh_seconds = max(5, BINANCE_PNL_REFRESH_SECONDS)
        self.binance_pnl_last_refresh = 0.0
        self.binance_margin_baseline: float | None = None
        self.binance_pnl = {
            "available": False,
            "equity_delta_usd": 0.0,
            "unrealized_usd": 0.0,
            "wallet_balance_usd": 0.0,
            "margin_balance_usd": 0.0,
            "updated_at": "",
            "error": "",
        }
        self.summary_path = Path("daily_summary.json")
        self._summary_cache_stamp: tuple[str, int, int] | None = None
        self._summary_cache_ts: float = 0.0
        self._summary_cache_ttl: float = 30.0  # seconds between full file re-scans
        self._summary_cache = self._default_daily_summary(datetime.now().strftime("%Y-%m-%d"))
        self.tick_count = 0
        self.auto_select_enabled = AUTO_SELECT_ENABLED
        self.auto_select_top_n = max(1, AUTO_SELECT_TOP_N)
        self.auto_select_interval_ticks = max(5, AUTO_SELECT_INTERVAL_TICKS)
        self.auto_select_round = 0
        self.hold_replace_streak = max(1, HOLD_REPLACE_STREAK)
        self.hold_score_penalty = max(0.0, HOLD_SCORE_PENALTY)
        self.base_signal_chance = min(max(BASE_SIGNAL_CHANCE, 0.01), 0.95)
        self.aggressive_movement_enabled = AGGRESSIVE_MOVEMENT_ENABLED
        self.aggressive_move_pct = max(0.05, AGGRESSIVE_MOVE_PCT)
        self.aggressive_signal_multiplier = min(max(AGGRESSIVE_SIGNAL_MULTIPLIER, 1.0), 3.0)
        self.aggressive_position_multiplier = min(max(AGGRESSIVE_POSITION_MULTIPLIER, 1.0), 2.0)
        self.one_live_entry_per_desk_per_tick = ONE_LIVE_ENTRY_PER_DESK_PER_TICK
        self.one_live_entry_global_per_tick = ONE_LIVE_ENTRY_GLOBAL_PER_TICK
        self.live_duplicate_cooldown_seconds = max(0.0, LIVE_DUPLICATE_COOLDOWN_SECONDS)
        self.live_symbol_cooldown_seconds = max(0.0, LIVE_SYMBOL_COOLDOWN_SECONDS)
        self.last_live_entry_tick_by_desk = {"btc": -1, "basket": -1}
        self.last_live_entry_tick_global = -1
        self.last_live_symbol_side_ts: dict[tuple[str, str], float] = {}
        self.last_live_symbol_ts: dict[str, float] = {}
        self.logs = []
        self.message_center: list[str] = []
        self.away_mode = False
        self.server_started_at = datetime.now().isoformat()
        # Queue for Ollama signal generation (parallel workers to avoid bottleneck)
        self.ollama_signal_queue: queue.Queue[tuple[str, str, str, float]] = queue.Queue()
        self.ollama_signal_pending: set[tuple[str, str]] = set()
        self.recalc_basket()
        if self.live_trading:
            threading.Thread(target=self._live_order_startup_check, daemon=True).start()
            threading.Thread(target=self._live_order_worker, daemon=True).start()
        # Start multiple parallel Ollama signal workers (OLLAMA_NUM_WORKERS threads)
        for _ in range(OLLAMA_NUM_WORKERS):
            threading.Thread(target=self._ollama_signal_worker, daemon=True).start()

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
        now = time.monotonic()
        # Use a TTL-based cache: re-scan file at most every _summary_cache_ttl seconds.
        # Mtime-based caching is ineffective because the file is written to constantly.
        if (
            self._summary_cache.get("day") == day
            and now - self._summary_cache_ts < self._summary_cache_ttl
        ):
            return self._summary_cache

        log_path = Path(MOVEMENT_LOG_FILE)
        if not log_path.exists():
            self._summary_cache_stamp = None
            self._summary_cache_ts = now
            self._summary_cache = self._default_daily_summary(day)
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

        self._summary_cache_stamp = True
        self._summary_cache_ts = time.monotonic()
        self._summary_cache = summary
        try:
            self.summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        except Exception:
            pass
        return summary

    def _store_paths(self) -> list[Path]:
        return [Path(MOVEMENT_LOG_FILE), self.summary_path]

    def create_store_backup(self) -> dict:
        with self.lock:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_root = Path("backups") / f"store_backup_{stamp}"
            backup_root.mkdir(parents=True, exist_ok=True)
            copied: list[str] = []
            missing: list[str] = []
            for src in self._store_paths():
                if src.exists():
                    dst = backup_root / src.name
                    shutil.copy2(src, dst)
                    copied.append(src.name)
                else:
                    missing.append(src.name)
            self.add_log(f"STORE BACKUP CREATED: {backup_root}")
            return {
                "backup_dir": str(backup_root),
                "copied": copied,
                "missing": missing,
            }

    def purge_stores(self, backup_first: bool = True) -> dict:
        with self.lock:
            backup = None
            if backup_first:
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_root = Path("backups") / f"store_backup_{stamp}"
                backup_root.mkdir(parents=True, exist_ok=True)
                copied: list[str] = []
                missing: list[str] = []
                for src in self._store_paths():
                    if src.exists():
                        dst = backup_root / src.name
                        shutil.copy2(src, dst)
                        copied.append(src.name)
                    else:
                        missing.append(src.name)
                backup = {
                    "backup_dir": str(backup_root),
                    "copied": copied,
                    "missing": missing,
                }

            # Clear persisted stores.
            Path(MOVEMENT_LOG_FILE).write_text("", encoding="utf-8")
            day = datetime.now().strftime("%Y-%m-%d")
            fresh_summary = self._default_daily_summary(day)
            self.summary_path.write_text(json.dumps(fresh_summary, indent=2), encoding="utf-8")

            # Reset cached summaries and in-memory logs.
            self._summary_cache_stamp = None
            self._summary_cache_ts = 0.0
            self._summary_cache = fresh_summary
            self.logs = []

            # Reset per-model desk metrics while keeping selections/config intact.
            for m in self.models.values():
                for desk in ("btc", "basket"):
                    slot = m["desk_state"][desk]
                    slot["balance"] = START_BALANCE
                    slot["realized_pnl"] = 0.0
                    slot["trades"] = 0
                    slot["wins"] = 0
                    slot["losses"] = 0
                    slot["pos"] = 0.0
                    slot["entry"] = 0.0
                    slot["signal_source"] = "none"
                    slot["last_signal"] = "IDLE"
                    slot["hold_streak"] = 0
                    slot["hold_signals"] = 0
                    slot["directional_signals"] = 0
                    slot["trade_side"] = "FLAT"
                    slot["trade_open_balance"] = START_BALANCE

            self.add_log("STORES CLEARED: movement log, daily summary, and in-memory stats reset")
            return {
                "ok": True,
                "backup": backup,
            }

    def _desk_symbols(self, desk: str) -> list[str]:
        if desk == "basket":
            return ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
        return ["BTCUSDT"]

    def _symbol_min_notional_usd(self, symbol: str) -> float:
        price_key = _SYMBOL_PRICE_KEY.get(symbol, "btc")
        price = max(float(self.prices.get(price_key, 0.0) or 0.0), 1e-9)
        precision = _FUTURES_QTY_PRECISION.get(symbol, 3)
        step_floor = price / (10 ** precision)
        exchange_floor = _FUTURES_MIN_NOTIONAL_USD.get(symbol, 5.0)
        return max(exchange_floor, step_floor, 5.0)

    def _symbol_exchange_min_notional_usd(self, symbol: str) -> float:
        return max(float(_FUTURES_MIN_NOTIONAL_USD.get(symbol, 5.0)), 5.0)

    def _desk_min_notional_usd(self, desk: str) -> float:
        symbols = self._desk_symbols(desk)
        floors = [self._symbol_exchange_min_notional_usd(sym) for sym in symbols]
        return min(floors) if floors else 5.0

    def _global_min_notional_usd(self) -> float:
        symbols = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"}
        floors = [self._symbol_exchange_min_notional_usd(sym) for sym in symbols]
        return min(floors) if floors else 5.0

    def _global_execution_min_notional_usd(self) -> float:
        symbols = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"}
        floors = [self._symbol_min_notional_usd(sym) for sym in symbols]
        return min(floors) if floors else 5.0

    def _portfolio_pnl(self) -> float:
        total = 0.0
        for m in self.models.values():
            for desk in ("btc", "basket"):
                slot = m["desk_state"][desk]
                if slot["selected"]:
                    total += (slot["balance"] - START_BALANCE)
        return total

    def _effective_portfolio_pnl(self) -> float:
        if self.strict_no_simulation and self.binance_pnl.get("available"):
            return float(self.binance_pnl.get("equity_delta_usd", 0.0) or 0.0)
        return self._portfolio_pnl()

    def _flatten_internal_positions(self, reason: str) -> None:
        for name, model in self.models.items():
            for desk in ("btc", "basket"):
                slot = model["desk_state"][desk]
                if not slot.get("selected"):
                    continue
                ref_price = self.prices["basket"] if desk == "basket" else self.prices["btc"]
                self._close_trade_if_open(name, desk, slot, reason, ref_price)
                slot["pos"] = 0.0
                slot["mark_price"] = ref_price
                slot["last_signal"] = "HOLD"

    def _update_profit_lock(self, current_pnl: float) -> None:
        if self.profit_lock_cooldown_left > 0:
            self.profit_lock_cooldown_left -= 1
            if self.profit_lock_cooldown_left == 0:
                self.pause_all_desks = False
                self.profit_lock_anchor = current_pnl
                self.profit_lock_reason = ""
                self.add_log("PROFIT LOCK RELEASED: cooldown complete; trading resumed")
            return
        if not self.profit_lock_enabled or self.profit_lock_usd <= 0.0:
            return
        if current_pnl < (self.profit_lock_anchor + self.profit_lock_usd):
            return
        self._flatten_internal_positions("profit_lock")
        self.pause_all_desks = True
        self.profit_lock_cooldown_left = self.profit_lock_cooldown_ticks
        self.profit_lock_reason = (
            f"Profit lock engaged at ${current_pnl:.2f}; cooldown {self.profit_lock_cooldown_ticks} ticks"
        )
        self.add_log(f"LIVE TRADING PAUSED: {self.profit_lock_reason}")

    def _evaluate_guardrails(self) -> None:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        if today != self.guardrail_day:
            self.guardrail_day = today
            self.kill_switch = False
            self.halt_reason = ""
            self.profit_lock_anchor = self._effective_portfolio_pnl()
            self.profit_lock_cooldown_left = 0
            self.profit_lock_reason = ""
        current_pnl = self._effective_portfolio_pnl()
        self._update_profit_lock(current_pnl)
        if not self.kill_switch and current_pnl <= -self.daily_loss_limit_usd:
            self.kill_switch = True
            self.halt_reason = f"Daily loss guardrail hit (${self.daily_loss_limit_usd:.2f})"
            self.add_log(f"LIVE TRADING HALTED: {self.halt_reason}")

    def _binance_signed_request(self, method: str, path: str, params: dict, *, futures: bool = False) -> dict:
        if not BINANCE_API_KEY or not BINANCE_API_SECRET:
            raise RuntimeError("Binance API credentials are missing")
        base = BINANCE_FUTURES_URL if futures else BINANCE_BASE_URL
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
                f"{base}{path}?{signed_query}",
                headers=headers,
                method="GET",
            )
        else:
            req = urllib.request.Request(
                f"{base}{path}",
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

    def _binance_signed_get(self, path: str, params: dict, *, futures: bool = False) -> dict:
        return self._binance_signed_request("GET", path, params, futures=futures)

    def _binance_signed_post(self, path: str, params: dict, *, futures: bool = False) -> dict:
        return self._binance_signed_request("POST", path, params, futures=futures)

    def _get_futures_usdt_balance(self) -> float:
        """Free USDT in the USDT-M Futures wallet."""
        rows = self._binance_signed_get("/fapi/v2/balance", {}, futures=True)
        for row in rows:
            if row.get("asset") == "USDT":
                try:
                    return float(row.get("availableBalance", "0") or 0.0)
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    def _refresh_binance_pnl_if_due(self) -> None:
        now_mono = time.monotonic()
        if now_mono - self.binance_pnl_last_refresh < float(self.binance_pnl_refresh_seconds):
            return
        self.binance_pnl_last_refresh = now_mono

        if not BINANCE_API_KEY or not BINANCE_API_SECRET:
            self.binance_pnl = {
                "available": False,
                "equity_delta_usd": 0.0,
                "unrealized_usd": 0.0,
                "wallet_balance_usd": 0.0,
                "margin_balance_usd": 0.0,
                "updated_at": now_ts(),
                "error": "Binance credentials missing",
            }
            return

        try:
            account = self._binance_signed_get("/fapi/v2/account", {}, futures=True)
            margin_balance = float(account.get("totalMarginBalance", "0") or 0.0)
            wallet_balance = float(account.get("totalWalletBalance", "0") or 0.0)
            unrealized = float(account.get("totalUnrealizedProfit", "0") or 0.0)
            if self.binance_margin_baseline is None:
                self.binance_margin_baseline = margin_balance
            equity_delta = margin_balance - float(self.binance_margin_baseline)
            self.binance_pnl = {
                "available": True,
                "equity_delta_usd": equity_delta,
                "unrealized_usd": unrealized,
                "wallet_balance_usd": wallet_balance,
                "margin_balance_usd": margin_balance,
                "updated_at": now_ts(),
                "error": "",
            }
        except Exception as exc:
            self.binance_pnl = {
                "available": False,
                "equity_delta_usd": 0.0,
                "unrealized_usd": 0.0,
                "wallet_balance_usd": 0.0,
                "margin_balance_usd": 0.0,
                "updated_at": now_ts(),
                "error": str(exc)[:140],
            }

    def _place_live_market_order(self, symbol: str, side: str, quote_usd: float) -> dict:
        """Place a USDT-M Futures market order. side='BUY'=long, 'SELL'=short."""
        order_usd = max(5.0, min(quote_usd, self.max_order_usd))
        price_key = _SYMBOL_PRICE_KEY.get(symbol, "btc")
        price = self.prices.get(price_key, 1.0)
        precision = _FUTURES_QTY_PRECISION.get(symbol, 3)
        raw_qty = order_usd / price
        factor = 10 ** precision
        qty = math.floor(raw_qty * factor) / factor
        if qty <= 0:
            min_notional_for_step = price / factor
            raise RuntimeError(
                f"Calculated quantity {qty} for {symbol} is too small "
                f"(price=${price:.2f}, order=${order_usd:.2f}, min_step_notional~${min_notional_for_step:.2f})"
            )
        return self._binance_signed_post(
            "/fapi/v1/order",
            {
                "symbol": symbol,
                "side": side,
                "type": "MARKET",
                "quantity": qty,
            },
            futures=True,
        )

    def _record_live_fill(self, model_name: str, desk: str, symbol: str, side_label: str, avg_price: float, executed_qty: float) -> None:
        """Record a live fill and compute realized P&L. Must be called under self.lock."""
        key = (model_name, desk, symbol)
        if key not in self.live_ledger:
            self.live_ledger[key] = {
                "realized_pnl": 0.0, "open_qty": 0.0, "avg_entry": 0.0,
                "wins": 0, "losses": 0, "trades": 0,
            }
        L = self.live_ledger[key]
        is_long = (side_label == "LONG")
        fill_qty = executed_qty  # always positive
        open_qty = L["open_qty"]  # positive = long, negative = short

        if abs(open_qty) < 1e-12:
            # Flat → open new position
            L["open_qty"] = fill_qty if is_long else -fill_qty
            L["avg_entry"] = avg_price
            L["trades"] += 1
        elif (open_qty > 0 and is_long) or (open_qty < 0 and not is_long):
            # Same direction → add to position (average down/up)
            total_qty = abs(open_qty) + fill_qty
            L["avg_entry"] = (L["avg_entry"] * abs(open_qty) + avg_price * fill_qty) / total_qty
            L["open_qty"] = total_qty if is_long else -total_qty
        else:
            # Opposite direction → close and/or flip
            close_qty = min(fill_qty, abs(open_qty))
            if open_qty > 0:
                pnl = (avg_price - L["avg_entry"]) * close_qty
            else:
                pnl = (L["avg_entry"] - avg_price) * close_qty
            L["realized_pnl"] += pnl
            if pnl > 0:
                L["wins"] += 1
            elif pnl < 0:
                L["losses"] += 1
            L["trades"] += 1
            flip_qty = fill_qty - close_qty
            if flip_qty > 1e-12:
                # Flip: open new position in new direction
                L["open_qty"] = flip_qty if is_long else -flip_qty
                L["avg_entry"] = avg_price
            else:
                L["open_qty"] = 0.0
                L["avg_entry"] = 0.0

    def _ledger_pnl(self, model_name: str, desk: str, symbol: str) -> float:
        """Realized + mark-to-market unrealized P&L for one ledger entry. Under self.lock."""
        key = (model_name, desk, symbol)
        L = self.live_ledger.get(key)
        if not L:
            return 0.0
        realized = L["realized_pnl"]
        open_qty = L["open_qty"]
        if abs(open_qty) < 1e-12 or L["avg_entry"] <= 0:
            return realized
        price_key = _SYMBOL_PRICE_KEY.get(symbol, "btc")
        mark = float(self.prices.get(price_key, 0.0) or 0.0)
        if mark <= 0:
            return realized
        unrealized = open_qty * (mark - L["avg_entry"])  # positive = long profit, negative = long loss
        return realized + unrealized

    def _ledger_model_desk_pnl(self, model_name: str, desk: str) -> float:
        """Sum realized + unrealized P&L for one model on one desk. Under self.lock."""
        symbols = self._desk_symbols(desk)
        return sum(self._ledger_pnl(model_name, desk, sym) for sym in symbols)

    def _ledger_desk_total_pnl(self, desk: str) -> float:
        """Sum realized + unrealized P&L across all models for a desk. Under self.lock."""
        return sum(self._ledger_model_desk_pnl(nm, desk) for nm in self.models)

    def _refresh_binance_positions_if_due(self) -> None:
        """Poll /fapi/v2/positionRisk for open exchange positions (non-fatal if unavailable).
        Must be called while self.lock is already held (same pattern as _refresh_binance_pnl_if_due)."""
        if time.monotonic() - self.live_positions_last_refresh < self.live_positions_refresh_seconds:
            return
        self.live_positions_last_refresh = time.monotonic()
        if not BINANCE_API_KEY or not BINANCE_API_SECRET:
            return
        try:
            positions = self._binance_signed_get("/fapi/v2/positionRisk", {}, futures=True)
            self.live_positions = {
                p["symbol"]: p for p in positions
                if abs(float(p.get("positionAmt", "0") or "0")) > 1e-12
            }
        except Exception:
            pass  # non-fatal; stale data is acceptable

    def _live_order_startup_check(self) -> None:
        """Run once at startup: verify Binance Futures balance and API access."""
        time.sleep(2)  # let the price feed settle first
        try:
            usdt_free = self._get_futures_usdt_balance()
            min_needed = self._global_min_notional_usd()
            if usdt_free + 1e-9 < min_needed:
                with self.lock:
                    # Soft warning only: futures orders may still pass with leverage.
                    self.add_log(
                        f"LIVE FUNDS WARNING: free ${usdt_free:.2f}, est. min notional floor ~${min_needed:.2f}"
                    )
            elif usdt_free < self.live_order_usd:
                with self.lock:
                    # Informational only: runtime order sizing will adapt to free USDT.
                    self.add_log(
                        f"LIVE READY (auto-sized): free ${usdt_free:.2f} below configured order ${self.live_order_usd:.2f}"
                    )
            else:
                with self.lock:
                    self.add_log(f"\u2713 LIVE FUTURES READY (USDT available: ${usdt_free:.2f})")
        except Exception as exc:
            with self.lock:
                self.live_blocked = True
                self.live_blocked_reason = f"Binance Futures auth check failed: {exc}"
                self.add_log(f"LIVE BLOCKED: {self.live_blocked_reason}")

    def _live_order_worker(self) -> None:
        """Executes live orders sequentially to avoid parallel request bursts."""
        while True:
            model_name, desk, symbol, side_label, order_usd = self.live_order_queue.get()
            try:
                side = "BUY" if side_label == "LONG" else "SELL"
                result = self._place_live_market_order(symbol, side, order_usd)
                order_id = result.get("orderId", "?")
                avg_price = float(result.get("avgPrice") or 0)
                executed_qty = float(result.get("executedQty") or 0)
                cum_quote = float(result.get("cumQuote") or 0)
                with self.lock:
                    self.add_log(f"{model_name} LIVE {symbol} {side_label} ${cum_quote:.2f} @ {avg_price:.4f} (order {order_id})")
                    if avg_price > 0 and executed_qty > 0:
                        self._record_live_fill(model_name, desk, symbol, side_label, avg_price, executed_qty)
            except Exception as exc:
                err_str = str(exc)
                with self.lock:
                    # Auto-disable live routing on auth errors to stop repeated spam.
                    if "401" in err_str or "403" in err_str or "auth" in err_str.lower():
                        if not self.live_blocked:
                            self.live_blocked = True
                            self.live_blocked_reason = f"Binance order auth failure: {exc}"
                            self.add_log(f"LIVE BLOCKED (auto): {self.live_blocked_reason}")
                    else:
                        self.add_log(f"{model_name} LIVE {symbol} {side_label} failed: {exc}")
            finally:
                self.live_order_queue.task_done()
                # Small delay protects against exchange rate bursts.
                time.sleep(0.2)

    def _ollama_signal_worker(self) -> None:
        """Processes Ollama signal requests in parallel (multiple workers)."""
        while True:
            name, desk_key, ollama_tag, ref_price = self.ollama_signal_queue.get()
            pending_key = (name, desk_key)
            try:
                history = list(self.price_history)  # snapshot outside lock
                action, err = _ollama_signal(ollama_tag, ref_price, desk_key, history)
                move_pct = self._desk_recent_move_pct(desk_key)
                if err:
                    with self.lock:
                        self.add_log(f"{name} [{desk_key.upper()}]: LLM error — {err} (using momentum fallback)")
                    # Momentum-based fallback for errors: only go directional if move beats fee break-even
                    if abs(move_pct) >= MOMENTUM_OVERRIDE_THRESHOLD_PCT:
                        action = 1 if move_pct > 0 else -1
                    else:
                        action = 0  # Flat market — stay HOLD
                # If AI is neutral (action == 0), only use momentum as tiebreaker above meaningful threshold
                elif action == 0:
                    if abs(move_pct) >= MOMENTUM_OVERRIDE_THRESHOLD_PCT:
                        action = 1 if move_pct > 0 else -1
                # Flat-market gate: suppress directional signals when market isn't moving enough to
                # overcome round-trip fees (~20 bps).  Forces HOLD in choppy/sideways conditions.
                if action != 0 and abs(move_pct) < MIN_TRADE_MOVE_PCT:
                    action = 0
                
                live_desk = desk_key
                live_side = "HOLD"
                with self.lock:
                    m = self.models.get(name)
                    if not m:
                        continue
                    slot = m["desk_state"][desk_key]
                    if not slot["selected"]:
                        continue
                    if self.pause_all_desks or self.paused_desks.get(desk_key, False):
                        self.add_log(f"{name} [{desk_key.upper()}]: signal skipped (desk paused)")
                        continue
                    side = "LONG" if action == 1 else ("SHORT" if action == -1 else "HOLD")
                    hard_live_block = self.live_blocked and "Insufficient USDT" not in self.live_blocked_reason
                    if self.live_trading and hard_live_block and side in {"LONG", "SHORT"}:
                        side = "HOLD"
                    slot["signal_source"] = "ai"
                    slot["last_signal"] = side
                    if side == "HOLD":
                        slot["hold_streak"] = int(slot.get("hold_streak", 0)) + 1
                        slot["hold_signals"] = int(slot.get("hold_signals", 0)) + 1
                        # HOLD closes the open trade and flattens position.
                        self._close_trade_if_open(name, desk_key, slot, "hold_signal", ref_price)
                        slot["pos"] = 0.0
                        # Immediately trigger auto-select to replace this HOLD model
                        if self.auto_select_enabled and slot["hold_streak"] >= self.hold_replace_streak:
                            self._auto_select_models()
                    else:
                        slot["hold_streak"] = 0
                        slot["directional_signals"] = int(slot.get("directional_signals", 0)) + 1
                        slot["pos"] = action * (slot["balance"] / max(ref_price, 1.0)) * self._position_scale(desk_key)
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
                    executed = self._execute_live_signal(name, live_desk, live_side)
                    if self.strict_no_simulation and self.live_trading and not executed:
                        with self.lock:
                            mm = self.models.get(name)
                            if mm and mm["desk_state"][live_desk]["selected"]:
                                self._reset_internal_slot_pnl_unlocked(mm["desk_state"][live_desk], ref_price)
            except Exception as exc:
                with self.lock:
                    self.add_log(f"Ollama worker error: {exc}")
            finally:
                with self.lock:
                    self.ollama_signal_pending.discard(pending_key)
                self.ollama_signal_queue.task_done()
                # Small delay between sequential requests prevents Ollama overload
                time.sleep(0.1)

    def _execute_live_signal(self, model_name: str, desk: str, side_label: str) -> bool:
        if side_label not in {"LONG", "SHORT"}:
            return False
        with self.lock:
            self._evaluate_guardrails()
            if self.pause_all_desks or self.paused_desks.get(desk, False):
                return False
            if not self.live_trading:
                return False
            if self.require_live_feed and not self.live_feed:
                return False
            if self.kill_switch:
                return False
            if self.one_live_entry_per_desk_per_tick and self.last_live_entry_tick_by_desk.get(desk, -1) == self.tick_count:
                self.add_log(f"{model_name} LIVE throttled: {desk.upper()} already routed an entry this tick")
                return False
            if self.one_live_entry_global_per_tick and self.last_live_entry_tick_global == self.tick_count:
                self.add_log(f"{model_name} LIVE throttled: global entry already routed this tick")
                return False
            symbols = self._desk_symbols(desk)
            total_order_usd = self.live_order_usd

        # Keep hard blocks for auth errors, but allow automatic recovery from
        # temporary insufficient-funds blocks once balance is sufficient.
        if self.live_blocked and "Insufficient USDT" not in self.live_blocked_reason:
            return False

        # Futures preflight: read balance for diagnostics only.
        try:
            free_usdt = self._get_futures_usdt_balance()
        except Exception as exc:
            with self.lock:
                self.add_log(f"{model_name} LIVE precheck failed: {exc}")
            return False
        effective_order_usd = max(total_order_usd, 0.0)

        # Interpret ALPHA_LIVE_ORDER_USD as total notional per signal and
        # allocate only across symbols that can satisfy min notional/lot constraints.
        symbol_floor: dict[str, float] = {}
        for symbol in symbols:
            price_key = _SYMBOL_PRICE_KEY.get(symbol, "btc")
            price = max(float(self.prices.get(price_key, 0.0) or 0.0), 1e-9)
            precision = _FUTURES_QTY_PRECISION.get(symbol, 3)
            step_floor = price / (10 ** precision)
            exchange_floor = _FUTURES_MIN_NOTIONAL_USD.get(symbol, 5.0)
            symbol_floor[symbol] = max(exchange_floor, step_floor, 5.0)

        allocations: dict[str, float] = {}
        remaining = max(effective_order_usd, 0.0)
        for symbol, floor in sorted(symbol_floor.items(), key=lambda kv: kv[1]):
            if floor <= remaining:
                allocations[symbol] = floor
                remaining -= floor

        if not allocations:
            if not self.allow_cross_symbol_fallback:
                with self.lock:
                    self.add_log(
                        f"{model_name} LIVE skipped: desk {desk.upper()} budget ${effective_order_usd:.2f} below desk min"
                    )
                return False
            # Fallback: if the desk-specific symbol set is unaffordable (common for BTC desk
            # with small order budgets), route to any tradable symbol instead of skipping.
            all_symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
            fallback_floor: dict[str, float] = {}
            for symbol in all_symbols:
                price_key = _SYMBOL_PRICE_KEY.get(symbol, "btc")
                price = max(float(self.prices.get(price_key, 0.0) or 0.0), 1e-9)
                precision = _FUTURES_QTY_PRECISION.get(symbol, 3)
                step_floor = price / (10 ** precision)
                exchange_floor = _FUTURES_MIN_NOTIONAL_USD.get(symbol, 5.0)
                fallback_floor[symbol] = max(exchange_floor, step_floor, 5.0)

            remaining_fb = max(effective_order_usd, 0.0)
            for symbol, floor in sorted(fallback_floor.items(), key=lambda kv: kv[1]):
                if floor <= remaining_fb:
                    allocations[symbol] = floor
                    remaining_fb -= floor

            if allocations and remaining_fb > 0:
                per_symbol_extra = remaining_fb / len(allocations)
                for symbol in allocations:
                    allocations[symbol] = min(allocations[symbol] + per_symbol_extra, self.max_order_usd)

            if not allocations:
                with self.lock:
                    self.add_log(
                        f"{model_name} LIVE skipped: total ${effective_order_usd:.2f} cannot satisfy any symbol min size "
                        f"({', '.join(f'{s}~${fallback_floor[s]:.2f}' for s in all_symbols)})"
                    )
                return False
            with self.lock:
                self.add_log(
                    f"{model_name} LIVE fallback routing: desk {desk.upper()} could not meet min size; "
                    f"using {', '.join(allocations.keys())}"
                )
            remaining = 0.0

        if remaining > 0:
            per_symbol_extra = remaining / len(allocations)
            for symbol in allocations:
                allocations[symbol] = min(allocations[symbol] + per_symbol_extra, self.max_order_usd)

        now_epoch = time.time()
        filtered_allocations: dict[str, float] = {}
        for symbol, order_usd in allocations.items():
            key = (symbol, side_label)
            last_ts = self.last_live_symbol_side_ts.get(key, 0.0)
            if self.live_duplicate_cooldown_seconds > 0.0 and (now_epoch - last_ts) < self.live_duplicate_cooldown_seconds:
                continue
            last_symbol_ts = self.last_live_symbol_ts.get(symbol, 0.0)
            if self.live_symbol_cooldown_seconds > 0.0 and (now_epoch - last_symbol_ts) < self.live_symbol_cooldown_seconds:
                continue
            filtered_allocations[symbol] = order_usd
        allocations = filtered_allocations
        if not allocations:
            with self.lock:
                self.add_log(
                    f"{model_name} LIVE throttled: duplicate {side_label} within {self.live_duplicate_cooldown_seconds:.1f}s or symbol cooldown {self.live_symbol_cooldown_seconds:.1f}s"
                )
            return False

        required_usdt = sum(allocations.values())
        if free_usdt + 1e-9 < required_usdt:
            with self.lock:
                self.add_log(
                    f"LIVE FUNDS WARNING: free ${free_usdt:.2f} below est. notional ${required_usdt:.2f}; sending orders (exchange will enforce margin)"
                )

        for symbol, order_usd in allocations.items():
            self.live_order_queue.put((model_name, desk, symbol, side_label, order_usd))
        with self.lock:
            self.last_live_entry_tick_by_desk[desk] = self.tick_count
            self.last_live_entry_tick_global = self.tick_count
            now_epoch = time.time()
            for symbol in allocations:
                self.last_live_symbol_side_ts[(symbol, side_label)] = now_epoch
                self.last_live_symbol_ts[symbol] = now_epoch
            self.add_log(
                f"{model_name} queued LIVE {side_label} on "
                f"{', '.join(f'{s}:${allocations[s]:.2f}' for s in allocations)} "
                f"(total ${required_usdt:.2f})"
            )
        return True

    @staticmethod
    def _mk_model(color: str, bias: float) -> dict:
        base_slot = {
            "selected": False,
            "balance": START_BALANCE,
            "realized_pnl": 0.0,
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "pos": 0.0,
            "entry": 0.0,
            "mark_price": 0.0,
            "signal_source": "none",  # 'none' | 'ai'
            "last_signal": "IDLE",   # LONG | SHORT | HOLD | IDLE
            "hold_streak": 0,
            "hold_signals": 0,
            "directional_signals": 0,
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

    def _deduct_trading_fee(self, balance: float, amount: float, fee_bps: float) -> tuple:
        """Calculate trading fee and return (new_balance, fee_deducted)."""
        fee = amount * (fee_bps / 10000.0)
        return balance - fee, fee

    def _close_trade_if_open(self, model_name: str, desk: str, slot: dict, reason: str, price: float) -> None:
        side = slot.get("trade_side", "FLAT")
        if side not in {"LONG", "SHORT"}:
            return
        trade_pnl = slot["balance"] - slot.get("trade_open_balance", slot["balance"])
        
        # Deduct exit fee (closing trade at current price)
        pos = abs(float(slot.get("pos", 0.0) or 0.0))
        exit_value = pos * price
        if exit_value > 0:
            slot["balance"], exit_fee = self._deduct_trading_fee(slot["balance"], exit_value, ANALYTICS_FEE_BPS)
            trade_pnl -= exit_fee  # Exit fee reduces final PnL
        
        slot["trades"] = int(slot.get("trades", 0)) + 1
        if trade_pnl > 0:
            slot["wins"] = int(slot.get("wins", 0)) + 1
        elif trade_pnl < 0:
            slot["losses"] = int(slot.get("losses", 0)) + 1
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
            # Deduct entry fee (opening trade at current price)
            entry_value = slot["balance"] * self._position_scale(desk) / max(price, 1.0) * price
            slot["balance"], entry_fee = self._deduct_trading_fee(slot["balance"], entry_value, ANALYTICS_FEE_BPS)
            
            slot["trade_side"] = new_side
            slot["trade_open_balance"] = slot["balance"]

    def _reset_internal_slot_pnl_unlocked(self, slot: dict, ref_price: float) -> None:
        slot["balance"] = START_BALANCE
        slot["realized_pnl"] = 0.0
        slot["pos"] = 0.0
        slot["entry"] = ref_price
        slot["mark_price"] = ref_price
        slot["trade_side"] = "FLAT"
        slot["trade_open_balance"] = START_BALANCE

    def add_log(self, message: str) -> None:
        self.logs.insert(0, f"[{now_ts()}] {message}")
        del self.logs[MAX_LOGS:]

    def add_message(self, message: str) -> None:
        self.message_center.insert(0, f"[{now_ts()}] {message}")
        del self.message_center[MAX_MESSAGE_CENTER:]

    def _build_pnl_health_message(self) -> str:
        app_pnl = self._effective_portfolio_pnl()
        summary = self._get_daily_summary()
        expectancy = float(summary.get("expectancy_usd", 0.0) or 0.0)
        win_rate = float(summary.get("win_rate_pct", 0.0) or 0.0)
        feed = "LIVE" if self.live_feed else "OFFLINE"
        status = "healthy" if app_pnl >= 0 and expectancy >= 0 else "watch"
        return (
            f"PnL health [{status.upper()}]: total {app_pnl:+.2f} USD | "
            f"expectancy {expectancy:+.4f} | win rate {win_rate:.1f}% | feed {feed}"
        )

    def _build_recent_summary_message(self) -> str:
        summary = self._get_daily_summary()
        trades = int(summary.get("trades", 0) or 0)
        expectancy = float(summary.get("expectancy_usd", 0.0) or 0.0)
        max_dd = float(summary.get("max_drawdown_usd", 0.0) or 0.0)
        fallback_recent = sum(1 for l in self.logs[:MAX_LOGS] if "fallback routing" in l.lower())
        throttled_recent = sum(1 for l in self.logs[:MAX_LOGS] if "live throttled" in l.lower())
        return (
            f"Recent summary: trades {trades} | expectancy {expectancy:+.4f} | "
            f"max DD -${abs(max_dd):.2f} | fallback logs {fallback_recent} | throttled logs {throttled_recent}"
        )

    def recalc_basket(self) -> None:
        p = self.prices
        p["basket"] = (p["btc"] + p["eth"] * 12 + p["sol"] * 300 + p["bnb"] * 80) / 4

    def _mark_to_market_slot(self, model_name: str, desk: str, slot: dict, price: float) -> None:
        if not slot.get("selected"):
            return
        pos = float(slot.get("pos", 0.0) or 0.0)
        if abs(pos) <= 1e-12:
            slot["mark_price"] = price
            return
        prev = float(slot.get("mark_price", 0.0) or 0.0)
        if prev <= 0.0:
            slot["mark_price"] = price
            return
        pnl_delta = pos * (price - prev)
        if abs(pnl_delta) > 1e-12:
            slot["balance"] = float(slot.get("balance", START_BALANCE)) + pnl_delta
            _log_movement({
                "type": "pnl",
                "model": model_name,
                "desk": desk,
                "price": round(price, 2),
                "pos": round(pos, 8),
                "pnl_delta": round(pnl_delta, 6),
                "balance": round(slot["balance"], 6),
            })
        slot["mark_price"] = price

    def selected_count(self, desk: str) -> int:
        return sum(1 for m in self.models.values() if m["desk_state"][desk]["selected"])

    def _desk_recent_move_pct(self, desk: str) -> float:
        if len(self.price_history) < 2:
            return 0.0
        key = "btc" if desk == "btc" else "basket"
        window = min(8, len(self.price_history))
        oldest = self.price_history[-window][key]
        latest = self.price_history[-1][key]
        if not oldest:
            return 0.0
        return ((latest - oldest) / oldest) * 100.0

    def _is_aggressive_movement(self, desk: str) -> bool:
        if not self.aggressive_movement_enabled:
            return False
        return abs(self._desk_recent_move_pct(desk)) >= self.aggressive_move_pct

    def _signal_chance(self, desk: str) -> float:
        chance = self.base_signal_chance
        if self._is_aggressive_movement(desk):
            chance *= self.aggressive_signal_multiplier
        return min(max(chance, 0.01), 0.95)

    def _position_scale(self, desk: str) -> float:
        base = 0.4
        if self._is_aggressive_movement(desk):
            base *= self.aggressive_position_multiplier
        return min(max(base, 0.2), 0.85)

    def _model_score(self, name: str, desk: str) -> float:
        slot = self.models[name]["desk_state"][desk]
        if self.strict_no_simulation and self.live_trading:
            # Use real fill ledger for scoring (no sim balance)
            pnl = self._ledger_model_desk_pnl(name, desk)
            syms = self._desk_symbols(desk)
            wins = sum(self.live_ledger.get((name, desk, s), {}).get("wins", 0) for s in syms)
            losses = sum(self.live_ledger.get((name, desk, s), {}).get("losses", 0) for s in syms)
            trades = sum(self.live_ledger.get((name, desk, s), {}).get("trades", 0) for s in syms)
        else:
            pnl = float(slot.get("realized_pnl", 0.0))
            if slot.get("selected"):
                pnl += float(slot.get("balance", START_BALANCE)) - START_BALANCE
            wins = int(slot.get("wins", 0))
            losses = int(slot.get("losses", 0))
            trades = int(slot.get("trades", 0))
        decisions = wins + losses
        win_rate = (wins / decisions) if decisions else 0.5
        expectancy = pnl / trades if trades else 0.0
        continuity = 0.2 if slot.get("selected") else 0.0
        hold_streak = int(slot.get("hold_streak", 0))
        hold_signals = int(slot.get("hold_signals", 0))
        directional_signals = int(slot.get("directional_signals", 0))
        total_signals = hold_signals + directional_signals
        hold_ratio = (hold_signals / total_signals) if total_signals else 0.5
        hold_penalty = (hold_ratio * self.hold_score_penalty) + (min(hold_streak, 10) * 0.5)
        return pnl + ((win_rate - 0.5) * 20.0) + (expectancy * 0.25) + continuity - hold_penalty

    def _select_model_unlocked(self, name: str, desk: str = "btc") -> bool:
        m = self.models.get(name)
        if not m:
            return False
        assigned_desk = desk if desk in ("btc", "basket") else "btc"
        slot = m["desk_state"][assigned_desk]
        if slot["selected"]:
            return False
        ref_price = self.prices["btc"] if assigned_desk == "btc" else self.prices["basket"]
        slot["selected"] = True
        slot["pos"] = 0.0
        slot["entry"] = ref_price
        slot["signal_source"] = "ai"
        slot["last_signal"] = "HOLD"
        slot["trade_side"] = "FLAT"
        self.add_log(f"{name} selected to {assigned_desk.upper()} desk @ ${ref_price:,.2f} (generating first signal)")
        
        # Queue first signal for sequential Ollama processing (no concurrent inference)
        ollama_tag = OLLAMA_MODELS.get(name)
        pending_key = (name, assigned_desk)
        if ollama_tag and pending_key not in self.ollama_signal_pending:
            self.ollama_signal_pending.add(pending_key)
            self.ollama_signal_queue.put((name, assigned_desk, ollama_tag, ref_price))
        
        return True

    def _deselect_model_unlocked(self, name: str, desk: str | None = None) -> bool:
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
            slot["signal_source"] = "none"
            slot["last_signal"] = "IDLE"
            slot["hold_streak"] = 0
            slot["hold_signals"] = 0
            slot["directional_signals"] = 0
            slot["trade_side"] = "FLAT"
            slot["trade_open_balance"] = START_BALANCE
            self.add_log(f"{name} deselected from {d.upper()} desk")
            changed = True
        return changed

    def _auto_select_models(self) -> None:
        if not self.auto_select_enabled:
            return
        model_names = list(self.models.keys())
        model_count = len(model_names)
        if model_count == 0:
            return
        round_idx = self.auto_select_round % model_count
        self.auto_select_round += 1
        model_index = {nm: i for i, nm in enumerate(model_names)}
        top_n = min(max(1, self.auto_select_top_n), model_count)
        
        # First pass: select models for each desk independently
        desk_selections = {}
        for desk in ("btc", "basket"):
            forced_rotate: set[str] = set()
            for nm in self.models.keys():
                slot = self.models[nm]["desk_state"][desk]
                if slot.get("selected") and int(slot.get("hold_streak", 0)) >= self.hold_replace_streak:
                    forced_rotate.add(nm)
            ranked = sorted(
                model_names,
                key=lambda nm: (
                    self._model_score(nm, desk),
                    -((model_index[nm] - round_idx) % model_count),
                ),
                reverse=True,
            )
            ranked_pool = [nm for nm in ranked if nm not in forced_rotate]
            desk_selections[desk] = {
                "ranked": ranked,
                "ranked_pool": ranked_pool,
                "forced_rotate": forced_rotate,
                "winners": set(ranked_pool[:top_n]),
            }
        
        # Second pass: enforce desk diversity - if same model on both desks, swap weaker one with best alternative
        overlap = desk_selections["btc"]["winners"] & desk_selections["basket"]["winners"]
        if overlap and len(overlap) > 1:  # Only fix if there's more than just one overlapping (acceptable)
            for desk in ("btc", "basket"):
                other_desk = "basket" if desk == "btc" else "btc"
                other_winners = desk_selections[other_desk]["winners"]
                
                # For this desk, find overlapping models and try to swap them with better alternatives
                overlapping_on_desk = overlap & desk_selections[desk]["winners"]
                
                for overlap_model in sorted(overlapping_on_desk):
                    # Find the next best model not in winners of either desk
                    for candidate in desk_selections[desk]["ranked_pool"]:
                        if candidate not in desk_selections[desk]["winners"] and candidate not in other_winners:
                            # Swap: remove overlapping model, add this candidate
                            desk_selections[desk]["winners"].discard(overlap_model)
                            desk_selections[desk]["winners"].add(candidate)
                            break
        
        # Apply selections
        for desk in ("btc", "basket"):
            sel = desk_selections[desk]
            for nm in list(self.models.keys()):
                selected = self.models[nm]["desk_state"][desk]["selected"]
                if selected and (nm not in sel["winners"] or nm in sel["forced_rotate"]):
                    if nm in sel["forced_rotate"]:
                        self.add_log(
                            f"AUTO-SELECT {desk.upper()}: rotating out {nm} after HOLD streak {self.models[nm]['desk_state'][desk].get('hold_streak', 0)}"
                        )
                    self._deselect_model_unlocked(nm, desk)
            for nm in sel["ranked_pool"]:
                if nm in sel["winners"] and not self.models[nm]["desk_state"][desk]["selected"]:
                    self._select_model_unlocked(nm, desk)

            best = sel["ranked"][0] if sel["ranked"] else "-"
            move_pct = self._desk_recent_move_pct(desk)
            regime = "AGGRO" if self._is_aggressive_movement(desk) else "NORMAL"
            self.add_log(
                f"AUTO-SELECT {desk.upper()}: top {top_n} active | leader={best} | move={move_pct:+.3f}% | {regime}"
            )

    def next_desk(self) -> str:
        return "btc" if self.selected_count("btc") <= self.selected_count("basket") else "basket"

    def select_model(self, name: str, desk: str = "btc") -> bool:
        with self.lock:
            return self._select_model_unlocked(name, desk)

    def deselect_model(self, name: str, desk: str | None = None) -> bool:
        with self.lock:
            return self._deselect_model_unlocked(name, desk)

    def set_pause(self, desk: str, paused: bool) -> bool:
        with self.lock:
            if self.away_mode and paused:
                self.add_log("PAUSE BLOCKED: away mode keeps trading active")
                self.add_message("Pause request blocked while away mode is ON")
                return False
            if desk == "all":
                if self.pause_all_desks == paused:
                    return False
                self.pause_all_desks = paused
                state = "PAUSED" if paused else "RESUMED"
                self.add_log(f"DESKS {state}: BTC and BASKET")
                return True
            if desk not in ("btc", "basket"):
                return False
            if self.paused_desks.get(desk, False) == paused:
                return False
            self.paused_desks[desk] = paused
            state = "PAUSED" if paused else "RESUMED"
            self.add_log(f"{desk.upper()} DESK {state}")
            return True

    def set_auto_select_enabled(self, enabled: bool) -> bool:
        with self.lock:
            enabled = bool(enabled)
            if self.auto_select_enabled == enabled:
                return False
            self.auto_select_enabled = enabled
            state = "ENABLED" if enabled else "DISABLED"
            self.add_log(f"AUTO-SELECT {state}")
            return True

    def set_away_mode(self, enabled: bool) -> bool:
        with self.lock:
            enabled = bool(enabled)
            if self.away_mode == enabled:
                return False
            self.away_mode = enabled
            if enabled:
                self.pause_all_desks = False
                self.paused_desks["btc"] = False
                self.paused_desks["basket"] = False
                self.add_log("AWAY MODE ENABLED: trading remains active while unattended")
                self.add_message("Away mode enabled: trading stays live; pause actions are blocked")
            else:
                self.add_log("AWAY MODE DISABLED")
                self.add_message("Away mode disabled: manual pauses allowed again")
            return True

    def push_pnl_health_message(self) -> str:
        with self.lock:
            msg = self._build_pnl_health_message()
            self.add_message(msg)
            self.add_log(f"MESSAGE CENTER: {msg}")
            return msg

    def push_recent_summary_message(self) -> str:
        with self.lock:
            msg = self._build_recent_summary_message()
            self.add_message(msg)
            self.add_log(f"MESSAGE CENTER: {msg}")
            return msg

    def clear_all_desks(self) -> dict:
        with self.lock:
            cleared = {"btc": 0, "basket": 0}
            for nm in list(self.models.keys()):
                for desk in ("btc", "basket"):
                    if self.models[nm]["desk_state"][desk]["selected"]:
                        if self._deselect_model_unlocked(nm, desk):
                            cleared[desk] += 1
            return cleared

    def refresh_prices(self) -> None:
        prev_prices = self.prices.copy()
        try:
            symbols = json.dumps(["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"], separators=(",", ":"))
            q = urllib.parse.urlencode({"symbols": symbols})
            req = urllib.request.Request(
                f"https://api.binance.com/api/v3/ticker/price?{q}",
                headers={},
            )
            with urllib.request.urlopen(req, timeout=2.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
                px = {row["symbol"]: float(row["price"]) for row in payload if "symbol" in row and "price" in row}
                self.prices["btc"] = px.get("BTCUSDT", self.prices["btc"])
                self.prices["eth"] = px.get("ETHUSDT", self.prices["eth"])
                self.prices["sol"] = px.get("SOLUSDT", self.prices["sol"])
                self.prices["bnb"] = px.get("BNBUSDT", self.prices["bnb"])
                self.live_feed = all(sym in px for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"))
                if not self.live_feed:
                    self.feed_error = "Binance ticker response missing one or more required symbols"
                if self.live_feed and self.feed_paused:
                    self.feed_paused = False
                    self.feed_pause_reason = ""
                    self.feed_error = ""
                    self.add_log("LIVE FEED RESTORED: trading resumed")
        except Exception as exc:
            self.live_feed = False
            self.feed_error = str(exc)
            self.prices = prev_prices
            if self.require_live_feed and not self.feed_paused:
                self.feed_paused = True
                self.feed_pause_reason = "Binance live market data unavailable"
                self.add_log(f"LIVE FEED LOST: trading paused | cause: {self.feed_error}")

        self.recalc_basket()
        # Save to rolling price history (used to enrich LLM prompts)
        self.price_history.append({
            "btc":    self.prices["btc"],
            "eth":    self.prices["eth"],
            "sol":    self.prices["sol"],
            "bnb":    self.prices["bnb"],
            "basket": self.prices["basket"],
        })
        # Record a price snapshot every tick for movement analysis.
        _log_movement({
            "type": "price",
            "btc":    round(self.prices["btc"], 2),
            "eth":    round(self.prices["eth"], 2),
            "sol":    round(self.prices["sol"], 4),
            "bnb":    round(self.prices["bnb"], 4),
            "basket": round(self.prices["basket"], 2),
            "feed":   "live" if self.live_feed else "offline",
        })

    def step_models(self) -> None:
        if self.require_live_feed and not self.live_feed:
            return
        for name, m in self.models.items():
            for desk in ("btc", "basket"):
                if self.pause_all_desks or self.paused_desks.get(desk, False):
                    continue
                slot = m["desk_state"][desk]
                ref_price = self.prices["basket"] if desk == "basket" else self.prices["btc"]

                # Keep desk balances current with open-position price movement.
                self._mark_to_market_slot(name, desk, slot, ref_price)

                # Trade cadence adapts to movement regime (normal vs aggressive).
                should_trade = slot["selected"] and random.random() < self._signal_chance(desk)
                if should_trade:
                    ollama_tag = OLLAMA_MODELS.get(name)
                    if ollama_tag:
                        # Queue only if no request is already pending for this model/desk.
                        pending_key = (name, desk)
                        if pending_key not in self.ollama_signal_pending:
                            self.ollama_signal_pending.add(pending_key)
                            self.ollama_signal_queue.put((name, desk, ollama_tag, ref_price))
                    else:
                        self.add_log(f"{name} [{desk.upper()}]: skipped (no mapped Ollama model)")

    def _apply_ollama_signal(self, name: str, desk_key: str, ollama_tag: str, ref_price: float) -> None:
        history = list(self.price_history)  # snapshot outside lock
        action, err = _ollama_signal(ollama_tag, ref_price, desk_key, history)
        if err:
            self.add_log(f"{name} [{desk_key.upper()}]: LLM error — {err} (treated as HOLD)")
        # Flat-market gate: suppress directional signal if market isn't moving enough
        if action != 0:
            move_pct = self._desk_recent_move_pct(desk_key)
            if abs(move_pct) < MIN_TRADE_MOVE_PCT:
                action = 0
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
            hard_live_block = self.live_blocked and "Insufficient USDT" not in self.live_blocked_reason
            if self.live_trading and hard_live_block and side in {"LONG", "SHORT"}:
                side = "HOLD"
            slot["signal_source"] = "ai"
            slot["last_signal"] = side
            if side == "HOLD":
                slot["hold_streak"] = int(slot.get("hold_streak", 0)) + 1
                slot["hold_signals"] = int(slot.get("hold_signals", 0)) + 1
                # HOLD closes the open trade and flattens position.
                self._close_trade_if_open(name, desk_key, slot, "hold_signal", ref_price)
                slot["pos"] = 0.0
                slot["mark_price"] = ref_price
            else:
                slot["hold_streak"] = 0
                slot["directional_signals"] = int(slot.get("directional_signals", 0)) + 1
                slot["pos"] = action * (slot["balance"] / max(ref_price, 1.0)) * self._position_scale(desk_key)
                slot["entry"] = ref_price
                slot["mark_price"] = ref_price
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
            executed = self._execute_live_signal(name, live_desk, live_side)
            if self.strict_no_simulation and self.live_trading and not executed:
                with self.lock:
                    mm = self.models.get(name)
                    if mm and mm["desk_state"][live_desk]["selected"]:
                        self._reset_internal_slot_pnl_unlocked(mm["desk_state"][live_desk], ref_price)

    def snapshot(self) -> dict:
        with self.lock:
            strict_live_mode = self.strict_no_simulation and self.live_trading
            if strict_live_mode:
                # Real P&L from live fill ledger (realized + mark-to-market unrealized per symbol)
                btc_pnl = self._ledger_desk_total_pnl("btc")
                basket_pnl = self._ledger_desk_total_pnl("basket")
                app_total_pnl = btc_pnl + basket_pnl
                # Equity balances are not meaningful in strict live mode; use 0
                btc_equity = 0.0
                basket_equity = 0.0
            else:
                btc_equity = sum(m["desk_state"]["btc"]["balance"] for m in self.models.values() if m["desk_state"]["btc"]["selected"])
                basket_equity = sum(m["desk_state"]["basket"]["balance"] for m in self.models.values() if m["desk_state"]["basket"]["selected"])
                btc_pnl = sum(
                    m["desk_state"]["btc"].get("realized_pnl", 0.0)
                    + (m["desk_state"]["btc"]["balance"] - START_BALANCE)
                    for m in self.models.values()
                    if m["desk_state"]["btc"]["selected"]
                )
                basket_pnl = sum(
                    m["desk_state"]["basket"].get("realized_pnl", 0.0)
                    + (m["desk_state"]["basket"]["balance"] - START_BALANCE)
                    for m in self.models.values()
                    if m["desk_state"]["basket"]["selected"]
                )
                app_total_pnl = btc_pnl + basket_pnl
            # Per-model performance stats aggregated across both desks.
            model_stats = {}
            for nm, mm in self.models.items():
                if strict_live_mode:
                    btc_r = self._ledger_model_desk_pnl(nm, "btc")
                    basket_r = self._ledger_model_desk_pnl(nm, "basket")
                    total_r = btc_r + basket_r
                else:
                    btc_r = (
                        mm["desk_state"]["btc"].get("realized_pnl", 0.0)
                        + (mm["desk_state"]["btc"]["balance"] - START_BALANCE if mm["desk_state"]["btc"]["selected"] else 0.0)
                    )
                    basket_r = (
                        mm["desk_state"]["basket"].get("realized_pnl", 0.0)
                        + (mm["desk_state"]["basket"]["balance"] - START_BALANCE if mm["desk_state"]["basket"]["selected"] else 0.0)
                    )
                    total_r = btc_r + basket_r
                model_stats[nm] = {
                    "color": mm["color"],
                    "total_pnl": round(total_r, 4),
                    "btc_pnl": round(btc_r, 4),
                    "basket_pnl": round(basket_r, 4),
                    "btc_selected":    mm["desk_state"]["btc"]["selected"],
                    "basket_selected": mm["desk_state"]["basket"]["selected"],
                    "btc_signal":    mm["desk_state"]["btc"]["last_signal"],
                    "basket_signal": mm["desk_state"]["basket"]["last_signal"],
                }
            return {
                "prices": {
                    "btc": self.prices["btc"],
                    "basket": self.prices["basket"],
                },
                "status": {
                    "feed": "LIVE" if self.live_feed else "OFFLINE",
                    "mode": (
                        "LIVE_BLOCKED" if (self.live_trading and self.live_blocked)
                        else ("LIVE" if self.live_trading else "LIVE_BLOCKED")
                    ),
                    "no_simulation": self.strict_no_simulation,
                    "require_live_feed": self.require_live_feed,
                    "feed_paused": self.feed_paused,
                    "feed_pause_reason": self.feed_pause_reason,
                    "feed_error": self.feed_error,
                    "kill_switch": self.kill_switch,
                    "halt_reason": self.halt_reason,
                    "live_blocked": self.live_blocked,
                    "live_blocked_reason": self.live_blocked_reason,
                    "order_usd": self.live_order_usd,
                    "min_executable_order_usd": self._global_execution_min_notional_usd(),
                    "allow_cross_symbol_fallback": self.allow_cross_symbol_fallback,
                    "one_live_entry_per_desk_per_tick": self.one_live_entry_per_desk_per_tick,
                    "one_live_entry_global_per_tick": self.one_live_entry_global_per_tick,
                    "live_duplicate_cooldown_seconds": self.live_duplicate_cooldown_seconds,
                    "live_symbol_cooldown_seconds": self.live_symbol_cooldown_seconds,
                    "order_queue": self.live_order_queue.qsize(),
                    "auto_select_enabled": self.auto_select_enabled,
                    "auto_select_top_n": self.auto_select_top_n,
                    "auto_select_interval_ticks": self.auto_select_interval_ticks,
                    "pause_all_desks": self.pause_all_desks,
                    "profit_lock_enabled": self.profit_lock_enabled,
                    "profit_lock_usd": self.profit_lock_usd,
                    "profit_lock_cooldown_ticks": self.profit_lock_cooldown_ticks,
                    "profit_lock_cooldown_left": self.profit_lock_cooldown_left,
                    "profit_lock_anchor": self.profit_lock_anchor,
                    "profit_lock_reason": self.profit_lock_reason,
                    "pause_btc": self.paused_desks.get("btc", False),
                    "pause_basket": self.paused_desks.get("basket", False),
                    "away_mode": self.away_mode,
                    "aggressive_movement_enabled": self.aggressive_movement_enabled,
                    "aggressive_move_pct": self.aggressive_move_pct,
                    "aggressive_now_btc": self._is_aggressive_movement("btc"),
                    "aggressive_now_basket": self._is_aggressive_movement("basket"),
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
                "app_total_pnl_usd": app_total_pnl,
                "models": self.models,
                "model_stats": model_stats,
                "start_balance": START_BALANCE,
                "daily_summary": self._get_daily_summary(),
                "binance_pnl": self.binance_pnl,
                "server_started_at": self.server_started_at,
                "logs": self.logs[:],
                "message_center": self.message_center[:],
                "ts": now_ts(),
            }

    def tick(self) -> None:
        with self.lock:
            self.tick_count += 1
            self.refresh_prices()
            self._refresh_binance_pnl_if_due()
            self._refresh_binance_positions_if_due()
            self._evaluate_guardrails()
            should_bootstrap_selection = self.selected_count("btc") == 0 and self.selected_count("basket") == 0
            if self.auto_select_enabled and (
                should_bootstrap_selection or self.tick_count % self.auto_select_interval_ticks == 0
            ):
                self._auto_select_models()
            self.step_models()


def trading_loop(state: ArenaState) -> None:
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
            self.path = "/quantplot_ai.html"
        super().do_GET()

    def do_POST(self) -> None:
        if self.path not in {
            "/api/select",
            "/api/deselect",
            "/api/pause",
            "/api/store/backup",
            "/api/store/purge",
            "/api/auto-select",
            "/api/desks/clear",
            "/api/away-mode",
            "/api/message/pnl-health",
            "/api/message/recent-summary",
        }:
            self._json(404, {"ok": False, "error": "Not found"})
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        try:
            data = json.loads(self.rfile.read(content_length).decode("utf-8")) if content_length else {}
        except Exception:
            self._json(400, {"ok": False, "error": "Invalid JSON"})
            return

        if self.path == "/api/store/backup":
            info = self.state.create_store_backup()
            self._json(200, {"ok": True, **info})
            return

        if self.path == "/api/store/purge":
            backup_first = data.get("backup_first", True)
            info = self.state.purge_stores(bool(backup_first))
            self._json(200, info)
            return

        if self.path == "/api/pause":
            desk = data.get("desk", "")
            if desk not in {"btc", "basket", "all"}:
                self._json(400, {"ok": False, "error": "desk must be one of: btc, basket, all"})
                return
            paused_raw = data.get("paused")
            if isinstance(paused_raw, bool):
                paused = paused_raw
            elif desk == "all":
                paused = not self.state.pause_all_desks
            else:
                paused = not self.state.paused_desks.get(desk, False)
            ok = self.state.set_pause(desk, paused)
            if not ok:
                self._json(409, {"ok": False, "error": "No state change"})
                return
            self._json(200, {"ok": True})
            return

        if self.path == "/api/auto-select":
            enabled_raw = data.get("enabled")
            if not isinstance(enabled_raw, bool):
                self._json(400, {"ok": False, "error": "enabled must be boolean"})
                return
            changed = self.state.set_auto_select_enabled(enabled_raw)
            self._json(200, {"ok": True, "changed": changed, "enabled": bool(enabled_raw)})
            return

        if self.path == "/api/desks/clear":
            cleared = self.state.clear_all_desks()
            self._json(200, {"ok": True, "cleared": cleared})
            return

        if self.path == "/api/away-mode":
            enabled_raw = data.get("enabled")
            if not isinstance(enabled_raw, bool):
                self._json(400, {"ok": False, "error": "enabled must be boolean"})
                return
            changed = self.state.set_away_mode(enabled_raw)
            self._json(200, {"ok": True, "changed": changed, "away_mode": bool(enabled_raw)})
            return

        if self.path == "/api/message/pnl-health":
            msg = self.state.push_pnl_health_message()
            self._json(200, {"ok": True, "message": msg})
            return

        if self.path == "/api/message/recent-summary":
            msg = self.state.push_recent_summary_message()
            self._json(200, {"ok": True, "message": msg})
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

    worker = threading.Thread(target=trading_loop, args=(state,), daemon=True)
    worker.start()

    web_root = Path(__file__).resolve().parent
    server = ThreadingHTTPServer((HOST, PORT), ArenaHandler)
    print(f"Serving QuantPlot AI live backend on http://{HOST}:{PORT}")
    print(f"Web root: {web_root}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
