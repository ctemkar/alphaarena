#!/usr/bin/env python3
import collections
import json
import os
import queue
import random
import math
import re
import subprocess
import shutil
import threading
import time
import hmac
import hashlib
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from execution_core import ExecutionCore, SignalRequest

HOST = "127.0.0.1"
PORT = 8000
START_BALANCE = 10_000.0
MAX_LOGS = 60
MAX_MESSAGE_CENTER = 30
TICK_SECONDS = 3.0
HARD_MAX_ORDER_USD = 1200.0
OLLAMA_URL = "http://127.0.0.1:11434"
try:
    OLLAMA_REQUEST_TIMEOUT_SECONDS = float(os.getenv("ALPHA_OLLAMA_TIMEOUT_SECONDS", "60"))
except ValueError:
    OLLAMA_REQUEST_TIMEOUT_SECONDS = 60.0
try:
    PUTER_REQUEST_TIMEOUT_SECONDS = float(os.getenv("ALPHA_PUTER_TIMEOUT_SECONDS", "75"))
except ValueError:
    PUTER_REQUEST_TIMEOUT_SECONDS = 75.0
try:
    OPENROUTER_REQUEST_TIMEOUT_SECONDS = float(os.getenv("ALPHA_OPENROUTER_TIMEOUT_SECONDS", "45"))
except ValueError:
    OPENROUTER_REQUEST_TIMEOUT_SECONDS = 45.0
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
        if not key:
            continue
        # Treat .env as defaults; explicit exported env vars should win.
        if key.startswith("ALPHA_"):
            if key not in os.environ:
                os.environ[key] = val
            continue
        if key not in os.environ:
            os.environ[key] = val


_load_dotenv()
INSECURE_SSL_ENABLED = os.getenv("ALPHA_INSECURE_SSL", "0").strip().lower() in {"1", "true", "yes", "on"}
if INSECURE_SSL_ENABLED:
    # Explicitly opt-in: allows HTTPS in environments with self-signed MITM certs.
    ssl._create_default_https_context = ssl._create_unverified_context
BINANCE_API_KEY = os.getenv("EXCH_BINANCE_API_KEY", "") or os.getenv("BINANCE_KEY", "")
BINANCE_API_SECRET = os.getenv("EXCH_BINANCE_API_SECRET", "") or os.getenv("BINANCE_SECRET", "")
PUTER_AUTH_TOKEN = os.getenv("PUTER_AUTH_TOKEN", "") or os.getenv("puterAuthToken", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "") or os.getenv("ALPHA_OPENROUTER_API_KEY", "")
OPENROUTER_URL = os.getenv("ALPHA_OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions")
LIVE_TRADING_ENABLED = os.getenv("ALPHA_LIVE_TRADING", "0").strip().lower() in {"1", "true", "yes", "on"}
PAPER_MODE_ENABLED = os.getenv("ALPHA_PAPER_MODE", "0").strip().lower() in {"1", "true", "yes", "on"}
USE_FUTURES = os.getenv("ALPHA_USE_FUTURES", "1").strip().lower() not in {"0", "false", "no", "off"}
try:
    ANALYTICS_FEE_BPS = float(os.getenv("ALPHA_ANALYTICS_FEE_BPS", "10"))
except ValueError:
    ANALYTICS_FEE_BPS = 10.0
try:
    ANALYTICS_SLIPPAGE_BPS = float(os.getenv("ALPHA_ANALYTICS_SLIPPAGE_BPS", "5"))
except ValueError:
    ANALYTICS_SLIPPAGE_BPS = 5.0

# Profitability floor: require directional setups to clear a minimum move edge
# in the short 8-tick momentum window. Values are in percent.
try:
    MIN_PROFIT_EDGE_PCT = float(os.getenv("ALPHA_MIN_PROFIT_EDGE_PCT", "0.08"))
except ValueError:
    MIN_PROFIT_EDGE_PCT = 0.08

try:
    LIVE_ORDER_USD = float(os.getenv("ALPHA_LIVE_ORDER_USD", "80"))
except ValueError:
    LIVE_ORDER_USD = 80.0
try:
    _basket_env = os.getenv("ALPHA_BASKET_ORDER_USD", "")
    BASKET_ORDER_USD = float(_basket_env) if _basket_env.strip() else LIVE_ORDER_USD
except ValueError:
    BASKET_ORDER_USD = LIVE_ORDER_USD
try:
    MAX_ORDER_USD = float(os.getenv("ALPHA_MAX_ORDER_USD", "120"))
except ValueError:
    MAX_ORDER_USD = 120.0
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
ALLOW_BTC_DESK_FALLBACK = os.getenv("ALPHA_ALLOW_BTC_DESK_FALLBACK", "0").strip().lower() in {"1", "true", "yes", "on"}
try:
    BINANCE_PNL_REFRESH_SECONDS = int(os.getenv("ALPHA_BINANCE_PNL_REFRESH_SECONDS", "10"))
except ValueError:
    BINANCE_PNL_REFRESH_SECONDS = 10
try:
    BINANCE_INCOME_LOOKBACK_HOURS = float(os.getenv("ALPHA_BINANCE_INCOME_LOOKBACK_HOURS", "2"))
except ValueError:
    BINANCE_INCOME_LOOKBACK_HOURS = 2.0
try:
    MIN_FREE_USDT_BUFFER = float(os.getenv("ALPHA_MIN_FREE_USDT_BUFFER", "2.0"))
except ValueError:
    MIN_FREE_USDT_BUFFER = 2.0
try:
    # Treat free USDT as margin, not full notional, when preflighting futures orders.
    EFFECTIVE_FUTURES_LEVERAGE = float(os.getenv("ALPHA_EFFECTIVE_FUTURES_LEVERAGE", "20"))
except ValueError:
    EFFECTIVE_FUTURES_LEVERAGE = 20.0

try:
    START_BALANCE = float(os.getenv("ALPHA_START_BALANCE_USD", str(START_BALANCE)))
except ValueError:
    START_BALANCE = 10_000.0

REQUIRE_LIVE_FEED = os.getenv("ALPHA_REQUIRE_LIVE_FEED", "1").strip().lower() in {"1", "true", "yes", "on"}
STRICT_NO_SIMULATION = True

AUTO_SELECT_ENABLED = os.getenv("ALPHA_AUTO_SELECT_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
try:
    AUTO_SELECT_TOP_N = int(os.getenv("ALPHA_AUTO_SELECT_TOP_N", "2"))
except ValueError:
    AUTO_SELECT_TOP_N = 2
try:
    AUTO_SELECT_INTERVAL_TICKS = int(os.getenv("ALPHA_AUTO_SELECT_INTERVAL_TICKS", "5"))
except ValueError:
    AUTO_SELECT_INTERVAL_TICKS = 20
try:
    HOLD_REPLACE_STREAK = int(os.getenv("ALPHA_HOLD_REPLACE_STREAK", "5"))
except ValueError:
    HOLD_REPLACE_STREAK = 5
try:
    HOLD_COOLDOWN_TICKS = int(os.getenv("ALPHA_HOLD_COOLDOWN_TICKS", "12"))
except ValueError:
    HOLD_COOLDOWN_TICKS = 12
HOLD_CLOSES_POSITION = os.getenv("ALPHA_HOLD_CLOSES_POSITION", "0").strip().lower() in {"1", "true", "yes", "on"}
SKIP_SELECTED_HOLD_ON_SIGNAL = os.getenv("ALPHA_SKIP_SELECTED_HOLD_ON_SIGNAL", "0").strip().lower() in {"1", "true", "yes", "on"}
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
EXECUTION_CORE_MODE = os.getenv("ALPHA_EXECUTION_CORE_MODE", "shadow").strip().lower()
EXECUTION_CORE_FALLBACK_ENABLED = os.getenv("ALPHA_EXECUTION_CORE_FALLBACK_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
try:
    EXECUTION_CORE_CUTOVER_THRESHOLD = float(os.getenv("ALPHA_EXECUTION_CORE_CUTOVER_THRESHOLD", "0.99"))
except ValueError:
    EXECUTION_CORE_CUTOVER_THRESHOLD = 0.99
try:
    EXECUTION_CORE_CUTOVER_MIN_COMPARES = int(os.getenv("ALPHA_EXECUTION_CORE_CUTOVER_MIN_COMPARES", "10"))
except ValueError:
    EXECUTION_CORE_CUTOVER_MIN_COMPARES = 10
try:
    EXECUTION_CORE_CUTOVER_STABILITY_CHECKS = int(os.getenv("ALPHA_EXECUTION_CORE_CUTOVER_STABILITY_CHECKS", "3"))
except ValueError:
    EXECUTION_CORE_CUTOVER_STABILITY_CHECKS = 3
EXECUTION_CORE_CUTOVER_GATE_ENABLED = os.getenv("ALPHA_EXECUTION_CORE_CUTOVER_GATE_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
EXECUTION_CORE_CUTOVER_AUTO_SWITCH = os.getenv("ALPHA_EXECUTION_CORE_CUTOVER_AUTO_SWITCH", "1").strip().lower() in {"1", "true", "yes", "on"}
try:
    AGGRESSIVE_SIGNAL_MULTIPLIER = float(os.getenv("ALPHA_AGGRESSIVE_SIGNAL_MULTIPLIER", "1.7"))
except ValueError:
    AGGRESSIVE_SIGNAL_MULTIPLIER = 1.7
try:
    AGGRESSIVE_POSITION_MULTIPLIER = float(os.getenv("ALPHA_AGGRESSIVE_POSITION_MULTIPLIER", "1.35"))
except ValueError:
    AGGRESSIVE_POSITION_MULTIPLIER = 1.35

# Minimum recent price move % required to allow a directional trade.
# This is combined with MIN_PROFIT_EDGE_PCT at runtime so entries only pass when
# recent movement is large enough to cover transaction costs and a small edge buffer.
try:
    MIN_TRADE_MOVE_PCT = float(os.getenv("ALPHA_MIN_TRADE_MOVE_PCT", "0.0"))
except ValueError:
    MIN_TRADE_MOVE_PCT = 0.0

# Minimum momentum needed to convert an AI HOLD into a directional tiebreaker.
try:
    MOMENTUM_OVERRIDE_THRESHOLD_PCT = float(os.getenv("ALPHA_MOMENTUM_OVERRIDE_THRESHOLD_PCT", "0.02"))
except ValueError:
    MOMENTUM_OVERRIDE_THRESHOLD_PCT = 0.02

HOLD_STREAK_MOMENTUM_OVERRIDE_ENABLED = os.getenv("ALPHA_HOLD_STREAK_MOMENTUM_OVERRIDE_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
try:
    HOLD_STREAK_MOMENTUM_OVERRIDE_MIN_STREAK = int(os.getenv("ALPHA_HOLD_STREAK_MOMENTUM_OVERRIDE_MIN_STREAK", "3"))
except ValueError:
    HOLD_STREAK_MOMENTUM_OVERRIDE_MIN_STREAK = 3

try:
    DIRECTIONAL_STREAK_CAP = int(os.getenv("ALPHA_DIRECTIONAL_STREAK_CAP", "4"))
except ValueError:
    DIRECTIONAL_STREAK_CAP = 4
try:
    DIRECTIONAL_STREAK_COOLDOWN_TICKS = int(os.getenv("ALPHA_DIRECTIONAL_STREAK_COOLDOWN_TICKS", "6"))
except ValueError:
    DIRECTIONAL_STREAK_COOLDOWN_TICKS = 6
try:
    DIRECTIONAL_STREAK_STRONG_MOVE_PCT = float(os.getenv("ALPHA_DIRECTIONAL_STREAK_STRONG_MOVE_PCT", "0.06"))
except ValueError:
    DIRECTIONAL_STREAK_STRONG_MOVE_PCT = 0.06

# Strategy selection for signal filtering:
# 'trend_filter' (default) = Strict trend filtering (rejects counter-trend signals)
# 'simple_prompt' = Simplified prompt (basic momentum only, no complex context)
# 'reversal' = Inverts all LLM signals (LONG->SHORT, SHORT->LONG)
# 'selective_reverse' = Invert only when the AI conflicts with strong momentum
SIGNAL_STRATEGY = os.getenv("ALPHA_SIGNAL_STRATEGY", "trend_filter").strip().lower()
try:
    STRICT_TREND_FILTER_PCT = float(os.getenv("ALPHA_STRICT_TREND_FILTER_PCT", "0.05"))
except ValueError:
    STRICT_TREND_FILTER_PCT = 0.05
try:
    SELECTIVE_REVERSE_MIN_MOVE_PCT = float(os.getenv("ALPHA_SELECTIVE_REVERSE_MIN_MOVE_PCT", "0.02"))
except ValueError:
    SELECTIVE_REVERSE_MIN_MOVE_PCT = 0.02

ALWAYS_TRADE_ENABLED = os.getenv("ALPHA_ALWAYS_TRADE_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}

DISABLE_GROK_AUTO_SELECT = os.getenv("ALPHA_DISABLE_GROK_AUTO_SELECT", "1").strip().lower() in {"1", "true", "yes", "on"}
BLOCK_CROSS_DESK_SELECT_ON_HOLD = os.getenv("ALPHA_BLOCK_CROSS_DESK_SELECT_ON_HOLD", "1").strip().lower() in {"1", "true", "yes", "on"}

CANARY_ENABLED = os.getenv("ALPHA_CANARY_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
CANARY_MODE = os.getenv("ALPHA_CANARY_MODE", "desk").strip().lower()  # desk | ratio
CANARY_CONTROL_DESK = os.getenv("ALPHA_CANARY_CONTROL_DESK", "btc").strip().lower()
CANARY_TREATMENT_DESK = os.getenv("ALPHA_CANARY_TREATMENT_DESK", "basket").strip().lower()
try:
    CANARY_TREATMENT_RATIO = float(os.getenv("ALPHA_CANARY_TREATMENT_RATIO", "0.25"))
except ValueError:
    CANARY_TREATMENT_RATIO = 0.25
OPENROUTER_CANARY_MODEL = os.getenv("ALPHA_OPENROUTER_CANARY_MODEL", "openai/gpt-4o-mini").strip()
try:
    CANARY_PROMOTION_THRESHOLD = float(os.getenv("ALPHA_CANARY_PROMOTION_THRESHOLD", "0.12"))
except ValueError:
    CANARY_PROMOTION_THRESHOLD = 0.12
try:
    CANARY_MIN_TRADES = int(os.getenv("ALPHA_CANARY_MIN_TRADES", "20"))
except ValueError:
    CANARY_MIN_TRADES = 20

# Map model name to the backing LLM target. Most are Ollama tags; Puter-backed
# models use the `puter:` prefix and are resolved through the local JS helper.
OLLAMA_MODELS: dict[str, str] = {
    "Mistral":     "mistral:latest",
    "Llama-3.2":   "llama3.1:latest",
    "Gemma-4":     "gemma4:latest",
    "DeepSeek-R1": "deepseek-r1:latest",
    "Qwen-2.5":    "qwen2.5-coder:latest",
    "Llama-3.1-8B": "llama3.1:8b",
    "DeepSeek-R1-8B": "deepseek-r1:8b",
}
GROK_MODEL_NAME = "Grok-4.1-Fast"
GROK_MODEL_TAG = "puter:x-ai/grok-4-1-fast"

PUTER_HELPER = Path(__file__).resolve().parent / "scripts" / "puter_grok_chat.mjs"


def _signal_prompt(price: float, desk: str, price_history: list[dict] | None = None) -> str:
    asset = "BTC" if desk == "btc" else "BASKET (BTC/ETH/SOL/BNB)"
    key = "btc" if desk == "btc" else "basket"

    # Use simplified prompt if strategy is 'simple_prompt'
    if SIGNAL_STRATEGY == "simple_prompt":
        return (
            f"Crypto price: {asset} ${price:,.2f}. "
            "LONG (bullish), SHORT (bearish), or HOLD (neutral)? "
            "One word: LONG, SHORT, or HOLD."
        )

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
        if len(prices_seq) >= 2:
            tick_chg = (prices_seq[-1] - prices_seq[-2]) / prices_seq[-2] * 100 if prices_seq[-2] else 0.0
            trend_lines.append(f"Last tick change: {tick_chg:+.4f}%.")

    context = " ".join(trend_lines)
    fee_hint = (
        "Round-trip trading cost is ~0.08%. "
        "Go LONG if short-term trend is up, SHORT if short-term trend is down, "
        "and use HOLD only when price action is truly flat or mixed. "
    )
    return (
        f"You are a short-term crypto trader. {asset} current price: ${price:,.2f}. "
        + (context + " " if context else "")
        + fee_hint
        + "Based on this price action, should a short-term trader go LONG, SHORT, or HOLD? "
        "Reply with exactly one word: LONG, SHORT, or HOLD."
    )


def _parse_signal_text(text: str) -> int:
    normalized = (text or "").strip().upper()
    if not normalized:
        return 0

    # Fast path for strict one-word responses.
    cleaned = normalized.strip("`\"'.,:;!?()[]{} ")
    if cleaned == "LONG":
        return 1
    if cleaned == "SHORT":
        return -1
    if cleaned == "HOLD":
        return 0

    # Parse explicit signal tokens only; avoid substring bias from echoed prompts.
    tokens = re.findall(r"\b(LONG|SHORT|HOLD)\b", normalized)
    if not tokens:
        return 0

    has_long = "LONG" in tokens
    has_short = "SHORT" in tokens
    if has_long and has_short:
        # Ambiguous output (contains both sides) should be neutral, not forced LONG.
        return 0
    if has_long:
        return 1
    if has_short:
        return -1
    return 0


def _puter_signal(model_tag: str, prompt: str) -> tuple[int, str]:
    if not PUTER_HELPER.exists():
        return 0, f"missing helper: {PUTER_HELPER.name}"
    try:
        result = subprocess.run(
            ["node", str(PUTER_HELPER), model_tag, prompt],
            capture_output=True,
            check=False,
            text=True,
            timeout=PUTER_REQUEST_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return 0, "node unavailable"
    except subprocess.TimeoutExpired:
        return 0, "timeout"

    if result.returncode != 0:
        err = (result.stderr or result.stdout or "puter helper failed").strip()
        return 0, err[:140]
    return _parse_signal_text(result.stdout), ""


def _openrouter_signal(model_tag: str, prompt: str) -> tuple[int, str]:
    if not OPENROUTER_API_KEY:
        return 0, "openrouter api key missing"
    payload = json.dumps({
        "model": model_tag,
        "messages": [
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "max_tokens": 6,
    }).encode("utf-8")
    req = urllib.request.Request(
        OPENROUTER_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=OPENROUTER_REQUEST_TIMEOUT_SECONDS) as resp:
            result = json.loads(resp.read().decode())
            choices = result.get("choices") or []
            if not choices:
                return 0, "openrouter empty choices"
            msg = choices[0].get("message") or {}
            text = msg.get("content", "")
            return _parse_signal_text(text), ""
    except urllib.error.URLError as exc:
        return 0, f"network: {exc.reason}"
    except TimeoutError:
        return 0, "timeout"
    except Exception as exc:
        return 0, str(exc)[:120]


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
    prompt = _signal_prompt(price, desk, price_history)
    if ollama_tag.startswith("puter:"):
        return _puter_signal(ollama_tag.split(":", 1)[1], prompt)
    if ollama_tag.startswith("openrouter:"):
        return _openrouter_signal(ollama_tag.split(":", 1)[1], prompt)
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
            return _parse_signal_text(result.get("response", "")), ""
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
            "DeepSeek-R1": self._mk_model("#3ccf91", 0.55),
            "Qwen-2.5":    self._mk_model("#4db6ff", 0.53),
            "Llama-3.1-8B": self._mk_model("#ffd166", 0.54),
            "DeepSeek-R1-8B": self._mk_model("#8adf5b", 0.52),
        }
        self.puter_auth_token = PUTER_AUTH_TOKEN
        if self.puter_auth_token:
            self.models[GROK_MODEL_NAME] = self._mk_model("#ff6f61", 0.58)
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
        self.paper_mode = bool(PAPER_MODE_ENABLED)
        self.live_order_usd = max(5.0, min(LIVE_ORDER_USD, MAX_ORDER_USD, HARD_MAX_ORDER_USD))
        self.basket_order_usd = max(5.0, min(BASKET_ORDER_USD, MAX_ORDER_USD, HARD_MAX_ORDER_USD))
        self.max_order_usd = max(5.0, min(MAX_ORDER_USD, HARD_MAX_ORDER_USD))
        self.daily_loss_limit_usd = max(10.0, DAILY_LOSS_LIMIT_USD)
        self.profit_lock_enabled = PROFIT_LOCK_ENABLED
        self.profit_lock_usd = max(0.0, PROFIT_LOCK_USD)
        self.profit_lock_cooldown_ticks = max(0, PROFIT_LOCK_COOLDOWN_TICKS)
        self.profit_lock_cooldown_left = 0
        self.profit_lock_anchor = 0.0
        self.profit_lock_reason = ""
        self.allow_cross_symbol_fallback = ALLOW_CROSS_SYMBOL_FALLBACK
        self.allow_btc_desk_fallback = ALLOW_BTC_DESK_FALLBACK
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
        self.live_order_queue: queue.Queue[tuple[str, str, str, str, float, str, str]] = queue.Queue()
        # Live fill ledger: keyed by (model_name, desk, symbol)
        self.live_ledger: dict[tuple[str, str, str], dict] = {}
        # Paper fill ledger: separate from live fills so paper runs are isolated.
        self.paper_ledger: dict[tuple[str, str, str], dict] = {}
        self.paper_total_fees_usd: float = 0.0
        # Binance position cache from /fapi/v2/positionRisk
        self.live_positions: dict[str, dict] = {}
        self.live_positions_last_refresh: float = 0.0
        self.live_positions_refresh_seconds: float = 15.0
        self.binance_pnl_refresh_seconds = max(5, BINANCE_PNL_REFRESH_SECONDS)
        self.binance_pnl_last_refresh = 0.0
        self.binance_lifetime_refresh_seconds = 900.0
        self.binance_lifetime_last_refresh = 0.0
        self.binance_lifetime_income_cache = {
            "realized_pnl_usd": 0.0,
            "commission_usd": 0.0,
            "funding_fee_usd": 0.0,
            "income_other_usd": 0.0,
            "transfer_usd": 0.0,
            "net_income_ex_transfer_usd": 0.0,
            "net_income_incl_transfer_usd": 0.0,
            "entries_scanned": 0,
            "updated_at": "",
            "error": "",
        }
        self.binance_margin_baseline: float | None = None
        self.binance_tracked_unrealized_baseline: float | None = None
        self.binance_pnl = {
            "available": False,
            "equity_delta_usd": 0.0,
            "unrealized_usd": 0.0,
            "tracked_delta_usd": 0.0,
            "tracked_unrealized_usd": 0.0,
            "realized_pnl_usd": 0.0,
            "commission_usd": 0.0,
            "funding_fee_usd": 0.0,
            "income_other_usd": 0.0,
            "net_income_usd": 0.0,
            "income_window_hours": 0.0,
            "day_realized_pnl_usd": 0.0,
            "day_commission_usd": 0.0,
            "day_funding_fee_usd": 0.0,
            "day_income_other_usd": 0.0,
            "day_transfer_usd": 0.0,
            "day_net_income_ex_transfer_usd": 0.0,
            "day_net_income_incl_transfer_usd": 0.0,
            "day_window_label": "",
            "lifetime_realized_pnl_usd": 0.0,
            "lifetime_commission_usd": 0.0,
            "lifetime_funding_fee_usd": 0.0,
            "lifetime_income_other_usd": 0.0,
            "lifetime_transfer_usd": 0.0,
            "lifetime_net_income_ex_transfer_usd": 0.0,
            "lifetime_net_income_incl_transfer_usd": 0.0,
            "lifetime_entries_scanned": 0,
            "lifetime_updated_at": "",
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
        self.hold_cooldown_ticks = max(0, HOLD_COOLDOWN_TICKS)
        self.skip_selected_hold_on_signal = SKIP_SELECTED_HOLD_ON_SIGNAL
        self.disable_grok_auto_select = DISABLE_GROK_AUTO_SELECT
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
        self.directional_streak_cap = max(2, DIRECTIONAL_STREAK_CAP)
        self.directional_streak_cooldown_ticks = max(0, DIRECTIONAL_STREAK_COOLDOWN_TICKS)
        self.directional_streak_strong_move_pct = max(0.0, DIRECTIONAL_STREAK_STRONG_MOVE_PCT)
        self.always_trade_enabled = ALWAYS_TRADE_ENABLED
        self.desk_direction_state = {
            "btc": {"side": "HOLD", "streak": 0, "cooldown_until_tick": 0},
            "basket": {"side": "HOLD", "streak": 0, "cooldown_until_tick": 0},
        }
        self.desk_forced_side = {"btc": "SHORT", "basket": "SHORT"}
        self.last_live_entry_tick_by_desk = {"btc": -1, "basket": -1}
        self.last_live_entry_tick_global = -1
        self.last_live_symbol_side_ts: dict[tuple[str, str], float] = {}
        self.last_live_symbol_ts: dict[str, float] = {}
        self.openrouter_canary_model = OPENROUTER_CANARY_MODEL
        self.canary_enabled = bool(CANARY_ENABLED and OPENROUTER_API_KEY and self.openrouter_canary_model)
        self.canary_mode = CANARY_MODE if CANARY_MODE in {"desk", "ratio"} else "desk"
        self.canary_control_desk = CANARY_CONTROL_DESK if CANARY_CONTROL_DESK in {"btc", "basket"} else "btc"
        self.canary_treatment_desk = CANARY_TREATMENT_DESK if CANARY_TREATMENT_DESK in {"btc", "basket"} else "basket"
        self.canary_treatment_ratio = min(max(CANARY_TREATMENT_RATIO, 0.0), 1.0)
        self.canary_promotion_threshold = max(0.0, CANARY_PROMOTION_THRESHOLD)
        self.canary_min_trades = max(1, CANARY_MIN_TRADES)
        self.canary_stats = {
            "control": {
                "signals": 0,
                "directional": 0,
                "holds": 0,
                "errors": 0,
                "latency_ms_total": 0.0,
                "latency_samples": 0,
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "net_realized_pnl": 0.0,
                "equity": 0.0,
                "peak_equity": 0.0,
                "max_drawdown_usd": 0.0,
            },
            "treatment": {
                "signals": 0,
                "directional": 0,
                "holds": 0,
                "errors": 0,
                "latency_ms_total": 0.0,
                "latency_samples": 0,
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "net_realized_pnl": 0.0,
                "equity": 0.0,
                "peak_equity": 0.0,
                "max_drawdown_usd": 0.0,
            },
        }
        self.logs = []
        self.desk_logs = {"btc": [], "basket": []}
        self.message_center: list[str] = []
        self.away_mode = False
        self.server_started_at = datetime.now().isoformat()
        self.execution_core = ExecutionCore(
            mode=EXECUTION_CORE_MODE,
            fallback_enabled=EXECUTION_CORE_FALLBACK_ENABLED,
        )
        self.execution_core.set_cutover_gate(
            enabled=EXECUTION_CORE_CUTOVER_GATE_ENABLED,
            auto_switch=EXECUTION_CORE_CUTOVER_AUTO_SWITCH,
            threshold=EXECUTION_CORE_CUTOVER_THRESHOLD,
            min_compares=EXECUTION_CORE_CUTOVER_MIN_COMPARES,
            stability_checks=EXECUTION_CORE_CUTOVER_STABILITY_CHECKS,
        )
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
        # Log active strategy on startup
        self.add_log(
            f"STARTUP: Signal strategy = {SIGNAL_STRATEGY.upper()} "
            f"(trend_threshold={STRICT_TREND_FILTER_PCT:.3f}% if trend_filter)"
        )
        if SIGNAL_STRATEGY == "selective_reverse":
            self.add_log(
                f"STARTUP: Selective reverse enabled (min_move={SELECTIVE_REVERSE_MIN_MOVE_PCT:.3f}%)"
            )

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
            self.desk_logs = {"btc": [], "basket": []}

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
            # Keep BTC fully dedicated to BTC desk to avoid correlated double-exposure.
            return ["ETHUSDT", "SOLUSDT", "BNBUSDT"]
        if desk == "btc" and self.allow_cross_symbol_fallback and self.allow_btc_desk_fallback:
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
        if self.strict_no_simulation and self.live_trading:
            if self.paper_mode:
                return self._ledger_desk_total_pnl("btc", self.paper_ledger) + self._ledger_desk_total_pnl("basket", self.paper_ledger)
            if self.binance_pnl.get("available"):
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
            halt_label = "PAPER RUN HALTED" if self.paper_mode else "LIVE TRADING HALTED"
            self.add_log(f"{halt_label}: {self.halt_reason}")

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

    @staticmethod
    def _summarize_income_rows(rows: list[dict]) -> dict:
        realized = 0.0
        commission = 0.0
        funding_fee = 0.0
        income_other = 0.0
        transfer = 0.0
        for rec in rows or []:
            income_type = str(rec.get("incomeType") or "")
            try:
                income_val = float(rec.get("income", "0") or 0.0)
            except Exception:
                continue
            if income_type == "REALIZED_PNL":
                realized += income_val
            elif income_type == "COMMISSION":
                commission += income_val
            elif income_type == "FUNDING_FEE":
                funding_fee += income_val
            elif income_type == "TRANSFER":
                transfer += income_val
            else:
                income_other += income_val
        net_ex_transfer = realized + commission + funding_fee + income_other
        net_incl_transfer = net_ex_transfer + transfer
        return {
            "realized_pnl_usd": realized,
            "commission_usd": commission,
            "funding_fee_usd": funding_fee,
            "income_other_usd": income_other,
            "transfer_usd": transfer,
            "net_income_ex_transfer_usd": net_ex_transfer,
            "net_income_incl_transfer_usd": net_incl_transfer,
            "entries_scanned": len(rows or []),
        }

    def _refresh_binance_lifetime_income_if_due(self, end_ms: int) -> dict:
        now_mono = time.monotonic()
        if now_mono - self.binance_lifetime_last_refresh < float(self.binance_lifetime_refresh_seconds):
            return dict(self.binance_lifetime_income_cache)

        self.binance_lifetime_last_refresh = now_mono
        max_pages = 20
        cursor_end = end_ms
        all_rows: list[dict] = []
        try:
            for _ in range(max_pages):
                rows = self._binance_signed_get(
                    "/fapi/v1/income",
                    {
                        "startTime": 0,
                        "endTime": cursor_end,
                        "limit": 1000,
                    },
                    futures=True,
                )
                batch = rows or []
                if not batch:
                    break
                all_rows.extend(batch)
                if len(batch) < 1000:
                    break

                min_ts = None
                for rec in batch:
                    try:
                        ts = int(rec.get("time", 0) or 0)
                    except Exception:
                        ts = 0
                    if min_ts is None or (ts > 0 and ts < min_ts):
                        min_ts = ts
                if not min_ts or min_ts <= 1:
                    break
                next_cursor = min_ts - 1
                if next_cursor >= cursor_end:
                    break
                cursor_end = next_cursor

            summary = self._summarize_income_rows(all_rows)
            summary["updated_at"] = now_ts()
            summary["error"] = ""
            self.binance_lifetime_income_cache = summary
            return dict(summary)
        except Exception as exc:
            cached = dict(self.binance_lifetime_income_cache)
            cached["error"] = str(exc)[:140]
            if not cached.get("updated_at"):
                cached["updated_at"] = now_ts()
            self.binance_lifetime_income_cache = cached
            return dict(cached)

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
                "tracked_delta_usd": 0.0,
                "tracked_unrealized_usd": 0.0,
                "realized_pnl_usd": 0.0,
                "commission_usd": 0.0,
                "funding_fee_usd": 0.0,
                "income_other_usd": 0.0,
                "net_income_usd": 0.0,
                "income_window_hours": 0.0,
                "day_realized_pnl_usd": 0.0,
                "day_commission_usd": 0.0,
                "day_funding_fee_usd": 0.0,
                "day_income_other_usd": 0.0,
                "day_transfer_usd": 0.0,
                "day_net_income_ex_transfer_usd": 0.0,
                "day_net_income_incl_transfer_usd": 0.0,
                "day_window_label": "",
                "lifetime_realized_pnl_usd": 0.0,
                "lifetime_commission_usd": 0.0,
                "lifetime_funding_fee_usd": 0.0,
                "lifetime_income_other_usd": 0.0,
                "lifetime_transfer_usd": 0.0,
                "lifetime_net_income_ex_transfer_usd": 0.0,
                "lifetime_net_income_incl_transfer_usd": 0.0,
                "lifetime_entries_scanned": 0,
                "lifetime_updated_at": "",
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
            lookback_hours = max(0.25, float(BINANCE_INCOME_LOOKBACK_HOURS))
            end_ms = int(time.time() * 1000)
            start_ms = max(0, end_ms - int(lookback_hours * 3600 * 1000))

            income_rows = self._binance_signed_get(
                "/fapi/v1/income",
                {
                    "startTime": start_ms,
                    "endTime": end_ms,
                    "limit": 1000,
                },
                futures=True,
            )
            win_summary = self._summarize_income_rows(income_rows)

            utc_day_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            day_start_ms = int(utc_day_start.timestamp() * 1000)
            day_rows = self._binance_signed_get(
                "/fapi/v1/income",
                {
                    "startTime": day_start_ms,
                    "endTime": end_ms,
                    "limit": 1000,
                },
                futures=True,
            )
            day_summary = self._summarize_income_rows(day_rows)
            lifetime_summary = self._refresh_binance_lifetime_income_if_due(end_ms)

            tracked_symbols = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"}
            tracked_unrealized = 0.0
            for p in account.get("positions", []) or []:
                symbol = str(p.get("symbol") or "")
                if symbol not in tracked_symbols:
                    continue
                try:
                    pos_amt = float(p.get("positionAmt", "0") or 0.0)
                except Exception:
                    pos_amt = 0.0
                if abs(pos_amt) <= 1e-12:
                    continue
                try:
                    tracked_unrealized += float(p.get("unrealizedProfit", "0") or 0.0)
                except Exception:
                    continue

            if self.binance_margin_baseline is None:
                self.binance_margin_baseline = margin_balance
            if self.binance_tracked_unrealized_baseline is None:
                self.binance_tracked_unrealized_baseline = tracked_unrealized

            equity_delta = margin_balance - float(self.binance_margin_baseline)
            tracked_delta = tracked_unrealized - float(self.binance_tracked_unrealized_baseline)

            self.binance_pnl = {
                "available": True,
                "equity_delta_usd": equity_delta,
                "unrealized_usd": unrealized,
                "tracked_delta_usd": tracked_delta,
                "tracked_unrealized_usd": tracked_unrealized,
                "realized_pnl_usd": float(win_summary["realized_pnl_usd"]),
                "commission_usd": float(win_summary["commission_usd"]),
                "funding_fee_usd": float(win_summary["funding_fee_usd"]),
                "income_other_usd": float(win_summary["income_other_usd"]),
                "net_income_usd": float(win_summary["net_income_ex_transfer_usd"]),
                "income_window_hours": lookback_hours,
                "day_realized_pnl_usd": float(day_summary["realized_pnl_usd"]),
                "day_commission_usd": float(day_summary["commission_usd"]),
                "day_funding_fee_usd": float(day_summary["funding_fee_usd"]),
                "day_income_other_usd": float(day_summary["income_other_usd"]),
                "day_transfer_usd": float(day_summary["transfer_usd"]),
                "day_net_income_ex_transfer_usd": float(day_summary["net_income_ex_transfer_usd"]),
                "day_net_income_incl_transfer_usd": float(day_summary["net_income_incl_transfer_usd"]),
                "day_window_label": utc_day_start.strftime("UTC %Y-%m-%d"),
                "lifetime_realized_pnl_usd": float(lifetime_summary.get("realized_pnl_usd", 0.0) or 0.0),
                "lifetime_commission_usd": float(lifetime_summary.get("commission_usd", 0.0) or 0.0),
                "lifetime_funding_fee_usd": float(lifetime_summary.get("funding_fee_usd", 0.0) or 0.0),
                "lifetime_income_other_usd": float(lifetime_summary.get("income_other_usd", 0.0) or 0.0),
                "lifetime_transfer_usd": float(lifetime_summary.get("transfer_usd", 0.0) or 0.0),
                "lifetime_net_income_ex_transfer_usd": float(lifetime_summary.get("net_income_ex_transfer_usd", 0.0) or 0.0),
                "lifetime_net_income_incl_transfer_usd": float(lifetime_summary.get("net_income_incl_transfer_usd", 0.0) or 0.0),
                "lifetime_entries_scanned": int(lifetime_summary.get("entries_scanned", 0) or 0),
                "lifetime_updated_at": str(lifetime_summary.get("updated_at") or ""),
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
                "tracked_delta_usd": 0.0,
                "tracked_unrealized_usd": 0.0,
                "realized_pnl_usd": 0.0,
                "commission_usd": 0.0,
                "funding_fee_usd": 0.0,
                "income_other_usd": 0.0,
                "net_income_usd": 0.0,
                "income_window_hours": 0.0,
                "day_realized_pnl_usd": 0.0,
                "day_commission_usd": 0.0,
                "day_funding_fee_usd": 0.0,
                "day_income_other_usd": 0.0,
                "day_transfer_usd": 0.0,
                "day_net_income_ex_transfer_usd": 0.0,
                "day_net_income_incl_transfer_usd": 0.0,
                "day_window_label": "",
                "lifetime_realized_pnl_usd": 0.0,
                "lifetime_commission_usd": 0.0,
                "lifetime_funding_fee_usd": 0.0,
                "lifetime_income_other_usd": 0.0,
                "lifetime_transfer_usd": 0.0,
                "lifetime_net_income_ex_transfer_usd": 0.0,
                "lifetime_net_income_incl_transfer_usd": 0.0,
                "lifetime_entries_scanned": 0,
                "lifetime_updated_at": "",
                "wallet_balance_usd": 0.0,
                "margin_balance_usd": 0.0,
                "updated_at": now_ts(),
                "error": str(exc)[:140],
            }
    def _place_live_market_order(self, symbol: str, side: str, quote_usd: float) -> dict:
        """Place a USDT-M Futures market order. side='BUY'=long, 'SELL'=short."""
        symbol_min_usd = self._symbol_min_notional_usd(symbol)
        effective_max_usd = max(self.max_order_usd, symbol_min_usd)
        order_usd = max(symbol_min_usd, min(quote_usd, effective_max_usd))
        price_key = _SYMBOL_PRICE_KEY.get(symbol, "btc")
        price = self.prices.get(price_key, 1.0)
        precision = _FUTURES_QTY_PRECISION.get(symbol, 3)
        raw_qty = order_usd / price
        factor = 10 ** precision
        min_qty = 1.0 / factor
        qty = math.floor(raw_qty * factor + 1e-9) / factor
        # If budget satisfies min notional, force at least one valid lot step.
        if order_usd + 1e-9 >= symbol_min_usd and qty < min_qty:
            qty = min_qty
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
                "newOrderRespType": "RESULT",
            },
            futures=True,
        )

    def _get_futures_order_status(self, symbol: str, order_id: int | str) -> dict:
        return self._binance_signed_get(
            "/fapi/v1/order",
            {
                "symbol": symbol,
                "orderId": int(order_id),
            },
            futures=True,
        )

    def _record_live_fill(
        self,
        model_name: str,
        desk: str,
        symbol: str,
        side_label: str,
        avg_price: float,
        executed_qty: float,
        signal_arm: str,
    ) -> None:
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
            est_cost = close_qty * avg_price * ((ANALYTICS_FEE_BPS + ANALYTICS_SLIPPAGE_BPS) / 10000.0)
            net_pnl = pnl - est_cost
            self._canary_on_realized_trade(signal_arm, net_pnl)
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

    def _paper_fill_qty(self, symbol: str, order_usd: float) -> tuple[float, float]:
        """Return (mark_price, executable_qty) for a paper fill at current mark."""
        price_key = _SYMBOL_PRICE_KEY.get(symbol, "btc")
        mark_price = float(self.prices.get(price_key, 0.0) or 0.0)
        if mark_price <= 0.0:
            return 0.0, 0.0
        precision = _FUTURES_QTY_PRECISION.get(symbol, 3)
        factor = 10 ** precision
        min_qty = 1.0 / factor
        raw_qty = max(float(order_usd), 0.0) / mark_price
        qty = math.floor(raw_qty * factor + 1e-9) / factor
        if order_usd + 1e-9 >= self._symbol_min_notional_usd(symbol) and qty < min_qty:
            qty = min_qty
        return mark_price, max(qty, 0.0)

    def _record_paper_fill(
        self,
        model_name: str,
        desk: str,
        symbol: str,
        side_label: str,
        avg_price: float,
        executed_qty: float,
        signal_arm: str,
    ) -> None:
        """Record a paper fill and apply fee/slippage costs on every fill."""
        key = (model_name, desk, symbol)
        if key not in self.paper_ledger:
            self.paper_ledger[key] = {
                "realized_pnl": 0.0, "open_qty": 0.0, "avg_entry": 0.0,
                "wins": 0, "losses": 0, "trades": 0,
            }
        L = self.paper_ledger[key]
        is_long = (side_label == "LONG")
        fill_qty = max(float(executed_qty), 0.0)
        open_qty = float(L.get("open_qty", 0.0) or 0.0)
        if fill_qty <= 1e-12:
            return

        cost_rate = (ANALYTICS_FEE_BPS + ANALYTICS_SLIPPAGE_BPS) / 10000.0
        fill_cost = (fill_qty * avg_price) * cost_rate
        L["realized_pnl"] -= fill_cost
        self.paper_total_fees_usd += fill_cost

        if abs(open_qty) < 1e-12:
            L["open_qty"] = fill_qty if is_long else -fill_qty
            L["avg_entry"] = avg_price
            L["trades"] += 1
            return

        if (open_qty > 0 and is_long) or (open_qty < 0 and not is_long):
            total_qty = abs(open_qty) + fill_qty
            L["avg_entry"] = (L["avg_entry"] * abs(open_qty) + avg_price * fill_qty) / max(total_qty, 1e-12)
            L["open_qty"] = total_qty if is_long else -total_qty
            return

        close_qty = min(fill_qty, abs(open_qty))
        if open_qty > 0:
            gross_pnl = (avg_price - L["avg_entry"]) * close_qty
        else:
            gross_pnl = (L["avg_entry"] - avg_price) * close_qty
        L["realized_pnl"] += gross_pnl
        est_cost = close_qty * avg_price * cost_rate
        net_pnl = gross_pnl - est_cost
        self._canary_on_realized_trade(signal_arm, net_pnl)
        if net_pnl > 0:
            L["wins"] += 1
        elif net_pnl < 0:
            L["losses"] += 1
        L["trades"] += 1

        flip_qty = fill_qty - close_qty
        if flip_qty > 1e-12:
            L["open_qty"] = flip_qty if is_long else -flip_qty
            L["avg_entry"] = avg_price
        else:
            L["open_qty"] = 0.0
            L["avg_entry"] = 0.0

    def _ledger_pnl(
        self,
        model_name: str,
        desk: str,
        symbol: str,
        ledger: dict[tuple[str, str, str], dict] | None = None,
    ) -> float:
        """Realized + mark-to-market unrealized P&L for one ledger entry. Under self.lock."""
        key = (model_name, desk, symbol)
        source = ledger if ledger is not None else (self.paper_ledger if self.paper_mode else self.live_ledger)
        L = source.get(key)
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

    def _ledger_model_desk_pnl(
        self,
        model_name: str,
        desk: str,
        ledger: dict[tuple[str, str, str], dict] | None = None,
    ) -> float:
        """Sum realized + unrealized P&L for one model on one desk. Under self.lock."""
        symbols = self._desk_symbols(desk)
        return sum(self._ledger_pnl(model_name, desk, sym, ledger=ledger) for sym in symbols)

    def _ledger_desk_total_pnl(
        self,
        desk: str,
        ledger: dict[tuple[str, str, str], dict] | None = None,
    ) -> float:
        """Sum realized + unrealized P&L across all models for a desk. Under self.lock."""
        return sum(self._ledger_model_desk_pnl(nm, desk, ledger=ledger) for nm in self.models)

    @staticmethod
    def _ledger_summary(ledger: dict[tuple[str, str, str], dict]) -> dict:
        trades = 0
        wins = 0
        losses = 0
        open_positions = 0
        for entry in ledger.values():
            trades += int(entry.get("trades", 0) or 0)
            wins += int(entry.get("wins", 0) or 0)
            losses += int(entry.get("losses", 0) or 0)
            if abs(float(entry.get("open_qty", 0.0) or 0.0)) > 1e-12:
                open_positions += 1
        decided = wins + losses
        win_rate_pct = (wins / decided * 100.0) if decided else 0.0
        return {
            "trades": trades,
            "wins": wins,
            "losses": losses,
            "open_positions": open_positions,
            "win_rate_pct": round(win_rate_pct, 2),
        }

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

    def flatten_all_futures_positions(self) -> dict:
        """Reduce-only market-close all open USDT-M futures positions."""
        if not BINANCE_API_KEY or not BINANCE_API_SECRET:
            return {"ok": False, "error": "Binance credentials missing"}

        try:
            positions = self._binance_signed_get("/fapi/v2/positionRisk", {}, futures=True)
        except Exception as exc:
            return {"ok": False, "error": f"position fetch failed: {exc}"}

        actions: list[dict] = []
        errors: list[dict] = []
        flatten_plan = self.execution_core.plan_flatten_orders(positions, dict(_FUTURES_QTY_PRECISION))
        attempted = len(flatten_plan.orders)

        for skipped in flatten_plan.skipped:
            errors.append({
                "symbol": skipped.get("symbol", ""),
                "error": skipped.get("reason", "skipped"),
            })

        for params in flatten_plan.orders:
            try:
                result = self._binance_signed_post("/fapi/v1/order", params, futures=True)
                actions.append({
                    "symbol": params.get("symbol"),
                    "side": params.get("side"),
                    "qty": params.get("quantity"),
                    "orderId": result.get("orderId"),
                    "status": result.get("status"),
                })
            except Exception as exc:
                errors.append({
                    "symbol": params.get("symbol"),
                    "side": params.get("side"),
                    "qty": params.get("quantity"),
                    "error": str(exc),
                })

        with self.lock:
            # Force-refresh caches after flatten attempt.
            self.live_positions_last_refresh = 0.0
            self.binance_pnl_last_refresh = 0.0
            self._refresh_binance_positions_if_due()
            self._refresh_binance_pnl_if_due()
            self.add_log(
                f"FLATTEN FUTURES: attempted={attempted}, success={len(actions)}, errors={len(errors)}"
            )

        return {
            "ok": len(errors) == 0,
            "attempted": attempted,
            "success": len(actions),
            "errors": errors,
            "orders": actions,
        }

    def _model_provider_map(self) -> dict[str, str]:
        providers = dict(OLLAMA_MODELS)
        if self.puter_auth_token:
            providers[GROK_MODEL_NAME] = GROK_MODEL_TAG
        return providers

    def _canary_arm_for_desk(self, desk: str) -> str:
        if not self.canary_enabled or not OPENROUTER_API_KEY or not self.openrouter_canary_model:
            return "control"
        if self.canary_mode == "desk":
            return "treatment" if desk == self.canary_treatment_desk else "control"
        if self.canary_mode == "ratio":
            return "treatment" if random.random() < self.canary_treatment_ratio else "control"
        return "control"

    def _resolve_signal_target(self, name: str, desk: str, default_tag: str) -> tuple[str, str, str]:
        arm = self._canary_arm_for_desk(desk)
        if arm == "treatment":
            return f"openrouter:{self.openrouter_canary_model}", "treatment", f"openrouter:{self.openrouter_canary_model}"
        return default_tag, "control", default_tag

    def _canary_on_signal(self, arm: str, signal: str, latency_ms: float, had_error: bool) -> None:
        stats = self.canary_stats.get(arm)
        if not stats:
            return
        stats["signals"] += 1
        stats["latency_ms_total"] += max(0.0, latency_ms)
        stats["latency_samples"] += 1
        if signal in {"LONG", "SHORT"}:
            stats["directional"] += 1
        else:
            stats["holds"] += 1
        if had_error:
            stats["errors"] += 1

    def _canary_on_realized_trade(self, arm: str, net_pnl: float) -> None:
        stats = self.canary_stats.get(arm)
        if not stats:
            return
        stats["trades"] += 1
        stats["net_realized_pnl"] += net_pnl
        stats["equity"] += net_pnl
        if net_pnl > 0:
            stats["wins"] += 1
        elif net_pnl < 0:
            stats["losses"] += 1
        if stats["equity"] > stats["peak_equity"]:
            stats["peak_equity"] = stats["equity"]
        dd = stats["peak_equity"] - stats["equity"]
        if dd > stats["max_drawdown_usd"]:
            stats["max_drawdown_usd"] = dd

    def _canary_summary(self) -> dict:
        summary: dict[str, dict | str | bool | float | int] = {
            "enabled": bool(self.canary_enabled),
            "mode": self.canary_mode,
            "treatment_desk": self.canary_treatment_desk,
            "treatment_ratio": self.canary_treatment_ratio,
            "treatment_model": self.openrouter_canary_model,
            "promotion_threshold": self.canary_promotion_threshold,
            "min_trades": self.canary_min_trades,
        }
        for arm in ("control", "treatment"):
            s = self.canary_stats[arm]
            trades = int(s["trades"])
            decisions = int(s["wins"] + s["losses"])
            summary[arm] = {
                "signals": int(s["signals"]),
                "directional": int(s["directional"]),
                "holds": int(s["holds"]),
                "errors": int(s["errors"]),
                "avg_latency_ms": round((s["latency_ms_total"] / s["latency_samples"]) if s["latency_samples"] else 0.0, 2),
                "trades": trades,
                "wins": int(s["wins"]),
                "losses": int(s["losses"]),
                "win_rate_pct": round((100.0 * s["wins"] / decisions) if decisions else 0.0, 2),
                "expectancy_usd": round((s["net_realized_pnl"] / trades) if trades else 0.0, 4),
                "net_realized_pnl_usd": round(s["net_realized_pnl"], 4),
                "max_drawdown_usd": round(s["max_drawdown_usd"], 4),
            }

        ctl = summary["control"]
        trt = summary["treatment"]
        promote = False
        reason = "insufficient data"
        if trt["trades"] >= self.canary_min_trades and ctl["trades"] >= self.canary_min_trades:
            ctl_exp = float(ctl["expectancy_usd"])
            trt_exp = float(trt["expectancy_usd"])
            exp_delta = trt_exp - ctl_exp
            base = max(abs(ctl_exp), 1e-9)
            exp_lift = exp_delta / base
            dd_ok = float(trt["max_drawdown_usd"]) <= float(ctl["max_drawdown_usd"])
            promote = exp_lift >= self.canary_promotion_threshold and dd_ok
            reason = (
                f"exp_lift={exp_lift:.3f}, ctl_exp={ctl_exp:.4f}, trt_exp={trt_exp:.4f}, "
                f"ctl_dd={float(ctl['max_drawdown_usd']):.2f}, trt_dd={float(trt['max_drawdown_usd']):.2f}"
            )
        summary["promotion"] = {
            "should_promote": promote,
            "reason": reason,
        }
        return summary

    def set_puter_auth_token(self, token: str) -> bool:
        token = (token or "").strip()
        if token == self.puter_auth_token:
            return False
        self.puter_auth_token = token
        if token:
            os.environ["PUTER_AUTH_TOKEN"] = token
            if GROK_MODEL_NAME not in self.models:
                self.models[GROK_MODEL_NAME] = self._mk_model("#ff6f61", 0.58)
            self.add_log("PUTER GROK ENABLED: auth token received")
            return True

        os.environ.pop("PUTER_AUTH_TOKEN", None)
        if GROK_MODEL_NAME in self.models:
            self._deselect_model_unlocked(GROK_MODEL_NAME)
            del self.models[GROK_MODEL_NAME]
        self.add_log("PUTER GROK DISABLED: auth token cleared")
        return True

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

    def set_execution_core_mode(self, mode: str) -> tuple[bool, str]:
        with self.lock:
            changed, msg = self.execution_core.set_mode((mode or "").strip().lower())
            if changed:
                self.add_log(f"EXECUTION CORE MODE -> {self.execution_core.mode.upper()}")
            return changed, msg

    def set_execution_core_fallback(self, enabled: bool) -> tuple[bool, str]:
        with self.lock:
            changed, msg = self.execution_core.set_fallback_enabled(bool(enabled))
            if changed:
                state = "ON" if self.execution_core.fallback_enabled else "OFF"
                self.add_log(f"EXECUTION CORE FALLBACK -> {state}")
            return changed, msg

    def set_execution_core_cutover_gate(
        self,
        *,
        enabled: bool | None = None,
        auto_switch: bool | None = None,
        threshold: float | None = None,
        min_compares: int | None = None,
        stability_checks: int | None = None,
    ) -> tuple[bool, str]:
        with self.lock:
            changed, msg = self.execution_core.set_cutover_gate(
                enabled=enabled,
                auto_switch=auto_switch,
                threshold=threshold,
                min_compares=min_compares,
                stability_checks=stability_checks,
            )
            if changed:
                gate = self.execution_core.health_snapshot().get("cutover_gate", {})
                self.add_log(
                    "EXECUTION CORE GATE -> "
                    f"enabled={gate.get('enabled')} auto_switch={gate.get('auto_switch')} "
                    f"threshold={gate.get('threshold')} min_compares={gate.get('min_compares')} "
                    f"stability_checks={gate.get('stability_checks')}"
                )
            return changed, msg

    def execution_core_gate_decision(self) -> dict:
        with self.lock:
            return self.execution_core.evaluate_cutover_gate()

    def maybe_auto_cutover(self) -> None:
        with self.lock:
            if self.execution_core.mode != "shadow":
                return
            decision = self.execution_core.evaluate_cutover_gate()
            if decision.get("allowed") and decision.get("auto_switch"):
                changed, _ = self.execution_core.set_mode("cutover")
                if changed:
                    self.execution_core.consume_cutover_gate_trigger()
                    self.add_log(
                        f"EXECUTION CORE AUTO-CUTOVER: {decision.get('reason', 'eligible')}"
                    )

    def execution_core_health(self) -> dict:
        with self.lock:
            return self.execution_core.health_snapshot()

    def _live_order_worker(self) -> None:
        """Executes live orders sequentially to avoid parallel request bursts."""
        while True:
            model_name, desk, symbol, side_label, order_usd, signal_arm, signal_provider = self.live_order_queue.get()
            try:
                side = "BUY" if side_label == "LONG" else "SELL"
                result = self._place_live_market_order(symbol, side, order_usd)
                order_id = result.get("orderId", "?")
                avg_price = float(result.get("avgPrice") or 0)
                executed_qty = float(result.get("executedQty") or 0)
                cum_quote = float(result.get("cumQuote") or 0)
                if order_id != "?" and (executed_qty <= 0 or avg_price <= 0 or cum_quote <= 0):
                    try:
                        status = self._get_futures_order_status(symbol, order_id)
                        avg_price = float(status.get("avgPrice") or avg_price or 0)
                        executed_qty = float(status.get("executedQty") or executed_qty or 0)
                        cum_quote = float(status.get("cumQuote") or cum_quote or 0)
                    except Exception:
                        pass
                if cum_quote <= 0 and executed_qty > 0 and avg_price > 0:
                    cum_quote = executed_qty * avg_price
                with self.lock:
                    self.add_log(
                        f"{model_name} LIVE {symbol} {side_label} ${cum_quote:.2f} @ {avg_price:.4f} "
                        f"(order {order_id}, arm={signal_arm}, provider={signal_provider})"
                    )
                    if avg_price > 0 and executed_qty > 0:
                        self._record_live_fill(model_name, desk, symbol, side_label, avg_price, executed_qty, signal_arm)
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
                resolved_tag, signal_arm, signal_provider = self._resolve_signal_target(name, desk_key, ollama_tag)
                started = time.perf_counter()
                action, err = _ollama_signal(resolved_tag, ref_price, desk_key, history)
                latency_ms = (time.perf_counter() - started) * 1000.0
                move_pct = self._desk_recent_move_pct(desk_key)
                entry_move_threshold = max(MIN_TRADE_MOVE_PCT, MIN_PROFIT_EDGE_PCT)
                momentum_threshold = max(MOMENTUM_OVERRIDE_THRESHOLD_PCT, entry_move_threshold)
                if err:
                    with self.lock:
                        self.add_log(
                            f"{name} [{desk_key.upper()}]: LLM error — {err} "
                            f"(provider={signal_provider}, arm={signal_arm}, using momentum fallback)"
                        )
                    # Momentum-based fallback for errors: only go directional if move beats fee break-even
                    if abs(move_pct) >= momentum_threshold:
                        action = 1 if move_pct > 0 else -1
                    else:
                        action = 0  # Flat market — stay HOLD
                # If AI is neutral (action == 0), only use momentum as tiebreaker above meaningful threshold
                elif action == 0:
                    if abs(move_pct) >= momentum_threshold:
                        action = 1 if move_pct > 0 else -1
                # Flat-market gate: suppress directional signals when market isn't moving enough to
                # overcome round-trip fees (~20 bps).  Forces HOLD in choppy/sideways conditions.
                suppressed_by_move_gate = False
                if action != 0 and abs(move_pct) < entry_move_threshold:
                    action = 0
                    suppressed_by_move_gate = True
                # Guardrail: do not take directional entries that directly conflict with
                # current desk momentum direction beyond the same override threshold.
                suppressed_by_trend_conflict = False
                if action == 1 and move_pct <= -MOMENTUM_OVERRIDE_THRESHOLD_PCT:
                    action = 0
                    suppressed_by_trend_conflict = True
                elif action == -1 and move_pct >= MOMENTUM_OVERRIDE_THRESHOLD_PCT:
                    action = 0
                    suppressed_by_trend_conflict = True
                
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
                    if suppressed_by_move_gate:
                        self.add_log(
                            f"{name} [{desk_key.upper()}]: directional signal suppressed "
                            f"(move {move_pct:+.4f}% < min {entry_move_threshold:.4f}%)"
                        )
                    if suppressed_by_trend_conflict:
                        self.add_log(
                            f"{name} [{desk_key.upper()}]: directional signal suppressed "
                            f"(signal conflicts with move {move_pct:+.4f}% trend)"
                        )
                    hard_live_block = (
                        (not self.paper_mode)
                        and self.live_blocked
                        and "Insufficient USDT" not in self.live_blocked_reason
                    )
                    if self.live_trading and hard_live_block and side in {"LONG", "SHORT"}:
                        side = "HOLD"
                    bias_throttle_reason = ""
                    if side in {"LONG", "SHORT"}:
                        side, bias_throttle_reason = self._apply_directional_bias_throttle(desk_key, side, move_pct)
                        if bias_throttle_reason:
                            self.add_log(
                                f"{name} [{desk_key.upper()}]: directional signal suppressed ({bias_throttle_reason})"
                            )
                    if (
                        side == "HOLD"
                        and self.always_trade_enabled
                        and not self.kill_switch
                        and not (self.live_trading and hard_live_block)
                        and abs(move_pct) >= entry_move_threshold
                    ):
                        side = self._always_trade_side(desk_key, move_pct)
                        self.add_log(
                            f"{name} [{desk_key.upper()}]: always-trade fallback -> {side} (move {move_pct:+.4f}%)"
                        )
                    slot["signal_source"] = "ai"
                    slot["last_signal"] = side
                    if side == "HOLD":
                        desk_state = self.desk_direction_state.get(desk_key)
                        if desk_state and int(desk_state.get("cooldown_until_tick", 0)) <= self.tick_count:
                            desk_state["streak"] = max(0, int(desk_state.get("streak", 0)) - 1)
                            if int(desk_state.get("streak", 0)) == 0:
                                desk_state["side"] = "HOLD"
                        slot["hold_streak"] = int(slot.get("hold_streak", 0)) + 1
                        if self.hold_cooldown_ticks > 0:
                            slot["hold_cooldown_until_tick"] = max(
                                int(slot.get("hold_cooldown_until_tick", 0)),
                                self.tick_count + self.hold_cooldown_ticks,
                            )
                        slot["hold_signals"] = int(slot.get("hold_signals", 0)) + 1
                        # In strict live mode, avoid simulated trade closes; real fills drive PnL.
                        if not (self.strict_no_simulation and self.live_trading):
                            self._close_trade_if_open(name, desk_key, slot, "hold_signal", ref_price)
                        slot["pos"] = 0.0
                        # Immediately trigger auto-select to replace this HOLD model
                        if self.auto_select_enabled and (
                            self.skip_selected_hold_on_signal
                            or slot["hold_streak"] >= self.hold_replace_streak
                        ):
                            self._auto_select_models()
                    else:
                        slot["hold_streak"] = 0
                        slot["hold_cooldown_until_tick"] = 0
                        slot["directional_signals"] = int(slot.get("directional_signals", 0)) + 1
                        # In strict live mode, do not open simulated trades ahead of real execution.
                        if not (self.strict_no_simulation and self.live_trading):
                            slot["pos"] = action * (slot["balance"] / max(ref_price, 1.0)) * self._position_scale(desk_key)
                            slot["entry"] = ref_price
                            self._roll_trade_on_signal(name, desk_key, slot, side, ref_price, "signal_flip")
                    desk = desk_key.upper()
                    live_side = side
                    self._canary_on_signal(signal_arm, side, latency_ms, bool(err))
                    self.add_log(
                        f"{name} [{desk}]: {side} @ ${ref_price:,.2f} [AI] "
                        f"(provider={signal_provider}, arm={signal_arm}, latency={latency_ms:.1f}ms)"
                    )
                    _log_movement({
                        "type":   "signal",
                        "model":  name,
                        "desk":   live_desk,
                        "signal": side,
                        "source": "ai",
                        "provider": signal_provider,
                        "arm": signal_arm,
                        "latency_ms": round(latency_ms, 2),
                        "price":  round(ref_price, 2),
                    })
                if live_side in {"LONG", "SHORT"}:
                    executed = self._execute_live_signal(
                        name,
                        live_desk,
                        live_side,
                        signal_arm=signal_arm,
                        signal_provider=signal_provider,
                    )
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

    def _execute_live_signal_legacy(
        self,
        model_name: str,
        desk: str,
        side_label: str,
        signal_arm: str = "control",
        signal_provider: str = "",
    ) -> bool:
        if side_label not in {"LONG", "SHORT"}:
            return False
        with self.lock:
            self._evaluate_guardrails()
            if self.pause_all_desks or self.paused_desks.get(desk, False):
                return False
            if not self.live_trading and not self.paper_mode:
                return False
            if self.require_live_feed and not self.live_feed and not self.paper_mode:
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
            total_order_usd = self.basket_order_usd if desk == "basket" else self.live_order_usd

        # Keep hard blocks for auth errors, but allow automatic recovery from
        # temporary insufficient-funds blocks once balance is sufficient.
        if self.live_blocked and "Insufficient USDT" not in self.live_blocked_reason:
            return False

        # Futures preflight: use exchange balance for live, synthetic high balance in paper.
        if self.paper_mode:
            free_usdt = 1.0e9
        else:
            try:
                free_usdt = self._get_futures_usdt_balance()
            except Exception as exc:
                with self.lock:
                    self.add_log(f"{model_name} LIVE precheck failed: {exc}")
                return False
        min_exec_usd = self._global_execution_min_notional_usd()
        effective_order_usd = max(total_order_usd, min_exec_usd)
        effective_max_order_usd = max(self.max_order_usd, min_exec_usd)
        if effective_order_usd > total_order_usd + 1e-9:
            with self.lock:
                self.add_log(
                    f"{model_name} LIVE auto-floor: configured ${total_order_usd:.2f} -> executable ${effective_order_usd:.2f}"
                )

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
            allow_fallback_here = self.allow_cross_symbol_fallback and (desk != "btc" or self.allow_btc_desk_fallback)
            if not allow_fallback_here:
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
                    allocations[symbol] = min(allocations[symbol] + per_symbol_extra, effective_max_order_usd)

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
                allocations[symbol] = min(allocations[symbol] + per_symbol_extra, effective_max_order_usd)

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
        eff_lev = max(1.0, float(EFFECTIVE_FUTURES_LEVERAGE))
        required_margin = required_usdt / eff_lev
        required_with_buffer = required_margin + max(0.0, MIN_FREE_USDT_BUFFER)
        
        # Auto-size down if free balance is tight, instead of hard-skipping.
        # This allows trading to continue at reduced size during temporary balance constraints.
        if free_usdt + 1e-9 < required_with_buffer:
            available_for_orders = max(0.0, free_usdt - max(0.0, MIN_FREE_USDT_BUFFER)) * eff_lev
            if available_for_orders < 1e-9:
                # Completely unable to trade
                with self.lock:
                    self.add_log(
                        f"LIVE SKIPPED: free ${free_usdt:.2f} below buffer ${max(0.0, MIN_FREE_USDT_BUFFER):.2f}"
                    )
                return False
            
            # Scale all allocations down proportionally to fit available balance
            scale_factor = available_for_orders / max(required_usdt, 1e-9)
            scaled_allocations: dict[str, float] = {}
            for symbol, order_usd in allocations.items():
                scaled_usd = order_usd * scale_factor
                if scaled_usd >= 1.0:  # Only keep non-trivial allocations
                    scaled_allocations[symbol] = scaled_usd
            
            if not scaled_allocations:
                with self.lock:
                    self.add_log(
                        f"LIVE SKIPPED: free ${free_usdt:.2f} vs required ${required_usdt:.2f}; "
                        f"scaled size dropped below minimum notional"
                    )
                return False
            
            with self.lock:
                self.add_log(
                    f"{model_name} LIVE auto-sized down: free ${free_usdt:.2f} < required ${required_with_buffer:.2f}; "
                    f"scaling ${required_usdt:.2f} -> ${sum(scaled_allocations.values()):.2f}"
                )
            allocations = scaled_allocations
            required_usdt = sum(allocations.values())

        with self.lock:
            self.last_live_entry_tick_by_desk[desk] = self.tick_count
            self.last_live_entry_tick_global = self.tick_count
            now_epoch = time.time()
            for symbol in allocations:
                self.last_live_symbol_side_ts[(symbol, side_label)] = now_epoch
                self.last_live_symbol_ts[symbol] = now_epoch
            if self.paper_mode:
                routed_symbols: list[str] = []
                for symbol, order_usd in allocations.items():
                    avg_price, qty = self._paper_fill_qty(symbol, order_usd)
                    if avg_price <= 0.0 or qty <= 0.0:
                        continue
                    self._record_paper_fill(model_name, desk, symbol, side_label, avg_price, qty, signal_arm)
                    routed_symbols.append(f"{symbol}:${order_usd:.2f}")
                if not routed_symbols:
                    self.add_log(f"{model_name} [PAPER] skipped: invalid mark/qty for all allocations")
                    return False
                self.add_log(
                    f"{model_name} [PAPER] {side_label} on "
                    f"{chr(44).join(routed_symbols)} "
                    f"(total ${sum(allocations.values()):.2f}) — no real order placed"
                )
            else:
                for symbol, order_usd in allocations.items():
                    self.live_order_queue.put((model_name, desk, symbol, side_label, order_usd, signal_arm, signal_provider))
                self.add_log(
                    f"{model_name} queued LIVE {side_label} on "
                    f"{chr(44).join(f'{s}:${allocations[s]:.2f}' for s in allocations)} "
                    f"(total ${sum(allocations.values()):.2f})"
                )
        return True

    def _execute_live_signal(
        self,
        model_name: str,
        desk: str,
        side_label: str,
        signal_arm: str = "control",
        signal_provider: str = "",
    ) -> bool:
        with self.lock:
            mode = self.execution_core.mode
            min_exec_usd = self._global_execution_min_notional_usd()
            effective_order_usd = max(self.live_order_usd, min_exec_usd)
            effective_max_order_usd = max(self.max_order_usd, min_exec_usd)
            if effective_order_usd > self.live_order_usd + 1e-9:
                self.add_log(
                    f"{model_name} CORE auto-floor: configured ${self.live_order_usd:.2f} -> executable ${effective_order_usd:.2f}"
                )
            req = SignalRequest(
                model_name=model_name,
                desk=desk,
                side_label=side_label,
                total_order_usd=effective_order_usd,
                max_order_usd=effective_max_order_usd,
                # In paper mode we still want execution planning and simulated fills
                # even if real live trading is disabled.
                live_trading=(self.live_trading or self.paper_mode),
                require_live_feed=(self.require_live_feed and not self.paper_mode),
                live_feed=self.live_feed,
                kill_switch=self.kill_switch,
                pause_all_desks=self.pause_all_desks,
                pause_desk=self.paused_desks.get(desk, False),
                live_blocked=self.live_blocked,
                live_blocked_reason=self.live_blocked_reason,
                one_live_entry_per_desk_per_tick=self.one_live_entry_per_desk_per_tick,
                one_live_entry_global_per_tick=self.one_live_entry_global_per_tick,
                desk_already_routed_this_tick=(self.last_live_entry_tick_by_desk.get(desk, -1) == self.tick_count),
                global_already_routed_this_tick=(self.last_live_entry_tick_global == self.tick_count),
                allow_cross_symbol_fallback=(
                    self.allow_cross_symbol_fallback and (desk != "btc" or self.allow_btc_desk_fallback)
                ),
                desk_symbols=self._desk_symbols(desk),
                universe_symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"],
                symbol_prices={
                    "BTCUSDT": float(self.prices.get("btc", 0.0) or 0.0),
                    "ETHUSDT": float(self.prices.get("eth", 0.0) or 0.0),
                    "SOLUSDT": float(self.prices.get("sol", 0.0) or 0.0),
                    "BNBUSDT": float(self.prices.get("bnb", 0.0) or 0.0),
                },
                qty_precision=dict(_FUTURES_QTY_PRECISION),
                min_notional=dict(_FUTURES_MIN_NOTIONAL_USD),
                duplicate_cooldown_seconds=self.live_duplicate_cooldown_seconds,
                symbol_cooldown_seconds=self.live_symbol_cooldown_seconds,
                last_symbol_side_ts=dict(self.last_live_symbol_side_ts),
                last_symbol_ts=dict(self.last_live_symbol_ts),
                now_epoch=time.time(),
            )
        plan = self.execution_core.plan_signal(req)

        if mode == "legacy":
            return self._execute_live_signal_legacy(model_name, desk, side_label, signal_arm, signal_provider)

        if mode == "shadow":
            q_before = self.live_order_queue.qsize()
            executed = self._execute_live_signal_legacy(model_name, desk, side_label, signal_arm, signal_provider)
            q_after = self.live_order_queue.qsize()
            queued_symbols: list[str] = []
            if q_after > q_before:
                try:
                    pending_items = list(self.live_order_queue.queue)
                    for item in pending_items[-(q_after - q_before):]:
                        queued_symbols.append(str(item[2]))
                except Exception:
                    queued_symbols = []
            with self.lock:
                self.execution_core.record_shadow_compare(plan, executed, queued_symbols)
            return executed

        if mode != "cutover":
            return self._execute_live_signal_legacy(model_name, desk, side_label, signal_arm, signal_provider)

        if not plan.allowed:
            with self.lock:
                self.add_log(f"{model_name} CORE cutover skipped: {plan.reason}")
            return False

        if self.paper_mode:
            with self.lock:
                self.last_live_entry_tick_by_desk[desk] = self.tick_count
                self.last_live_entry_tick_global = self.tick_count
                stamp = time.time()
                routed_symbols: list[str] = []
                for symbol, order_usd in plan.allocations.items():
                    self.last_live_symbol_side_ts[(symbol, side_label)] = stamp
                    self.last_live_symbol_ts[symbol] = stamp
                    avg_price, qty = self._paper_fill_qty(symbol, order_usd)
                    if avg_price <= 0.0 or qty <= 0.0:
                        continue
                    self._record_paper_fill(model_name, desk, symbol, side_label, avg_price, qty, signal_arm)
                    routed_symbols.append(f"{symbol}:${order_usd:.2f}")
                if not routed_symbols:
                    self.add_log(f"{model_name} CORE [PAPER] skipped: invalid mark/qty for all allocations")
                    return False
                route_note = " (fallback symbol set)" if plan.used_fallback_symbols else ""
                self.execution_core.record_cutover_routed(len(routed_symbols))
                self.add_log(
                    f"{model_name} CORE [PAPER] {side_label} on "
                    f"{', '.join(routed_symbols)} "
                    f"(total ${plan.required_usdt:.2f}){route_note} — no real order placed"
                )
            return True

        try:
            free_usdt = self._get_futures_usdt_balance()
        except Exception as exc:
            with self.lock:
                self.add_log(f"{model_name} CORE precheck failed: {exc}")
            if self.execution_core.fallback_enabled:
                with self.lock:
                    self.add_log(f"{model_name} CORE fallback -> LEGACY")
                return self._execute_live_signal_legacy(model_name, desk, side_label, signal_arm, signal_provider)
            return False

        eff_lev = max(1.0, float(EFFECTIVE_FUTURES_LEVERAGE))
        required_with_buffer = (plan.required_usdt / eff_lev) + max(0.0, MIN_FREE_USDT_BUFFER)
        if free_usdt + 1e-9 < required_with_buffer:
            with self.lock:
                self.add_log(
                    f"CORE SKIPPED: free ${free_usdt:.2f} below required ${plan.required_usdt:.2f} + buffer ${max(0.0, MIN_FREE_USDT_BUFFER):.2f}"
                )
            return False

        for symbol, order_usd in plan.allocations.items():
            self.live_order_queue.put((model_name, desk, symbol, side_label, order_usd, signal_arm, signal_provider))
        with self.lock:
            self.last_live_entry_tick_by_desk[desk] = self.tick_count
            self.last_live_entry_tick_global = self.tick_count
            stamp = time.time()
            for symbol in plan.allocations:
                self.last_live_symbol_side_ts[(symbol, side_label)] = stamp
                self.last_live_symbol_ts[symbol] = stamp
            self.execution_core.record_cutover_routed(len(plan.allocations))
            route_note = " (fallback symbol set)" if plan.used_fallback_symbols else ""
            self.add_log(
                f"{model_name} CORE queued LIVE {side_label} on "
                f"{', '.join(f'{s}:${plan.allocations[s]:.2f}' for s in plan.allocations)} "
                f"(total ${plan.required_usdt:.2f}){route_note}"
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
            "hold_cooldown_until_tick": 0,
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

    def _infer_log_desk(self, message: str) -> str | None:
        upper = message.upper()
        is_btc = "[BTC]" in upper or "BTC DESK" in upper
        is_basket = "[BASKET]" in upper or "BASKET DESK" in upper
        if is_btc == is_basket:
            return None
        return "btc" if is_btc else "basket"

    def add_log(self, message: str) -> None:
        stamped = f"[{now_ts()}] {message}"
        self.logs.insert(0, stamped)
        del self.logs[MAX_LOGS:]
        desk = self._infer_log_desk(message)
        if desk:
            self.desk_logs[desk].insert(0, stamped)
            del self.desk_logs[desk][MAX_LOGS:]

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
        # Basket excludes BTC so it reflects alt-coin composite behavior.
        p["basket"] = (p["eth"] * 12 + p["sol"] * 300 + p["bnb"] * 80) / 3

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

    def _apply_directional_bias_throttle(self, desk: str, side: str, move_pct: float) -> tuple[str, str]:
        if side not in {"LONG", "SHORT"}:
            return side, ""
        desk_state = self.desk_direction_state.get(desk)
        if not desk_state:
            return side, ""
        strong_move = abs(move_pct) >= self.directional_streak_strong_move_pct
        cooldown_until_tick = int(desk_state.get("cooldown_until_tick", 0))
        if cooldown_until_tick > self.tick_count and not strong_move:
            return "HOLD", f"directional cooldown until tick {cooldown_until_tick}"

        previous_side = str(desk_state.get("side", "HOLD"))
        previous_streak = int(desk_state.get("streak", 0))
        next_streak = (previous_streak + 1) if previous_side == side else 1
        if next_streak > self.directional_streak_cap and not strong_move:
            desk_state["side"] = side
            desk_state["streak"] = next_streak
            if self.directional_streak_cooldown_ticks > 0:
                desk_state["cooldown_until_tick"] = max(
                    cooldown_until_tick,
                    self.tick_count + self.directional_streak_cooldown_ticks,
                )
            return "HOLD", f"directional streak cap {self.directional_streak_cap} ({side} x{next_streak})"

        desk_state["side"] = side
        desk_state["streak"] = next_streak
        return side, ""

    def _always_trade_side(self, desk: str, move_pct: float) -> str:
        if move_pct > 0:
            side = "LONG"
        elif move_pct < 0:
            side = "SHORT"
        else:
            last_side = self.desk_forced_side.get(desk, "SHORT")
            side = "LONG" if last_side == "SHORT" else "SHORT"
        self.desk_forced_side[desk] = side
        return side

    def _model_score(self, name: str, desk: str) -> float:
        slot = self.models[name]["desk_state"][desk]
        if self.strict_no_simulation and self.live_trading:
            # Use active fill ledger for scoring (paper or live, no sim balance)
            active_ledger = self.paper_ledger if self.paper_mode else self.live_ledger
            pnl = self._ledger_model_desk_pnl(name, desk, ledger=active_ledger)
            syms = self._desk_symbols(desk)
            wins = sum(active_ledger.get((name, desk, s), {}).get("wins", 0) for s in syms)
            losses = sum(active_ledger.get((name, desk, s), {}).get("losses", 0) for s in syms)
            trades = sum(active_ledger.get((name, desk, s), {}).get("trades", 0) for s in syms)
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
        other_desk = "basket" if assigned_desk == "btc" else "btc"
        other_slot = m["desk_state"][other_desk]

        if other_slot.get("selected"):
            self.add_log(
                f"{name} selection blocked on {assigned_desk.upper()}: model already selected on {other_desk.upper()}"
            )
            return False

        if (
            BLOCK_CROSS_DESK_SELECT_ON_HOLD
            and other_slot.get("selected")
            and str(other_slot.get("last_signal") or "").upper() == "HOLD"
        ):
            self.add_log(
                f"{name} selection blocked on {assigned_desk.upper()}: model is HOLD on {other_desk.upper()}"
            )
            return False

        slot = m["desk_state"][assigned_desk]
        if slot["selected"]:
            return False
        ref_price = self.prices["btc"] if assigned_desk == "btc" else self.prices["basket"]
        slot["selected"] = True
        slot["pos"] = 0.0
        slot["entry"] = ref_price
        slot["signal_source"] = "ai"
        slot["last_signal"] = "IDLE"
        slot["trade_side"] = "FLAT"
        self.add_log(f"{name} selected to {assigned_desk.upper()} desk @ ${ref_price:,.2f} (generating first signal)")
        
        # Queue first signal for sequential Ollama processing (no concurrent inference)
        ollama_tag = self._model_provider_map().get(name)
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
        auto_select_names = [
            nm for nm in model_names
            if not (self.disable_grok_auto_select and nm == GROK_MODEL_NAME)
        ]
        if not auto_select_names:
            auto_select_names = model_names
        model_count = len(auto_select_names)
        if model_count == 0:
            return
        round_idx = self.auto_select_round % model_count
        self.auto_select_round += 1
        model_index = {nm: i for i, nm in enumerate(auto_select_names)}
        top_n = min(max(1, self.auto_select_top_n), model_count)
        
        # First pass: select models for each desk independently
        desk_selections = {}
        for desk in ("btc", "basket"):
            other_desk = "basket" if desk == "btc" else "btc"
            forced_rotate: set[str] = set()
            for nm in auto_select_names:
                slot = self.models[nm]["desk_state"][desk]
                if not slot.get("selected"):
                    continue
                signals_seen = int(slot.get("hold_signals", 0)) + int(slot.get("directional_signals", 0))
                if (
                    self.skip_selected_hold_on_signal
                    and slot.get("last_signal") == "HOLD"
                    and signals_seen > 0
                ):
                    forced_rotate.add(nm)
                    continue
                if int(slot.get("hold_streak", 0)) >= self.hold_replace_streak:
                    forced_rotate.add(nm)
                    continue
                if int(slot.get("hold_cooldown_until_tick", 0)) > self.tick_count:
                    forced_rotate.add(nm)
            ranked = sorted(
                auto_select_names,
                key=lambda nm: (
                    self._model_score(nm, desk),
                    -((model_index[nm] - round_idx) % model_count),
                ),
                reverse=True,
            )
            ranked_pool = [
                nm for nm in ranked
                if nm not in forced_rotate
                and not self.models[nm]["desk_state"][other_desk].get("selected")
                and int(self.models[nm]["desk_state"][desk].get("hold_cooldown_until_tick", 0)) <= self.tick_count
            ]
            if not ranked_pool:
                ranked_pool = [
                    nm for nm in ranked
                    if nm not in forced_rotate
                    and not self.models[nm]["desk_state"][other_desk].get("selected")
                ]
            current_selected = [
                nm for nm in auto_select_names
                if self.models[nm]["desk_state"][desk].get("selected")
                and nm not in forced_rotate
                and int(self.models[nm]["desk_state"][desk].get("hold_cooldown_until_tick", 0)) <= self.tick_count
            ]
            winners: set[str] = set()
            for nm in current_selected:
                if len(winners) >= top_n:
                    break
                winners.add(nm)
            if len(winners) < top_n:
                for nm in ranked_pool:
                    if len(winners) >= top_n:
                        break
                    winners.add(nm)
            desk_selections[desk] = {
                "ranked": ranked,
                "ranked_pool": ranked_pool,
                "forced_rotate": forced_rotate,
                "winners": winners,
            }
        
        # Second pass: enforce desk diversity by rotating overlap on BASKET only,
        # preserving BTC desk winners for BTC execution quality.
        overlap = desk_selections["btc"]["winners"] & desk_selections["basket"]["winners"]
        if overlap:
            btc_winners = desk_selections["btc"]["winners"]
            overlapping_on_basket = overlap & desk_selections["basket"]["winners"]
            for overlap_model in sorted(overlapping_on_basket):
                for candidate in desk_selections["basket"]["ranked_pool"]:
                    if (
                        candidate not in desk_selections["basket"]["winners"]
                        and candidate not in btc_winners
                    ):
                        desk_selections["basket"]["winners"].discard(overlap_model)
                        desk_selections["basket"]["winners"].add(candidate)
                        break
        
        # Apply selections
        for desk in ("btc", "basket"):
            sel = desk_selections[desk]
            for nm in list(self.models.keys()):
                selected = self.models[nm]["desk_state"][desk]["selected"]
                if selected and (nm not in sel["winners"] or nm in sel["forced_rotate"]):
                    if nm in sel["forced_rotate"]:
                        slot = self.models[nm]["desk_state"][desk]
                        cooldown_until = int(slot.get("hold_cooldown_until_tick", 0))
                        if cooldown_until > self.tick_count:
                            reason = f"HOLD cooldown until tick {cooldown_until}"
                        else:
                            reason = f"HOLD streak {slot.get('hold_streak', 0)}"
                        self.add_log(
                            f"AUTO-SELECT {desk.upper()}: rotating out {nm} ({reason})"
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

    def set_order_sizing(self, order_usd: float, max_order_usd: float | None = None) -> tuple[bool, str]:
        with self.lock:
            try:
                order_val = float(order_usd)
            except Exception:
                return False, "order_usd must be numeric"

            if max_order_usd is None:
                max_val = self.max_order_usd
            else:
                try:
                    max_val = float(max_order_usd)
                except Exception:
                    return False, "max_order_usd must be numeric"

            max_val = max(5.0, min(max_val, HARD_MAX_ORDER_USD))
            order_val = max(5.0, min(order_val, max_val, HARD_MAX_ORDER_USD))

            changed = False
            if abs(self.max_order_usd - max_val) > 1e-9:
                self.max_order_usd = max_val
                changed = True
            if abs(self.live_order_usd - order_val) > 1e-9:
                self.live_order_usd = order_val
                changed = True

            if changed:
                self.add_log(
                    f"ORDER SIZE UPDATED: order ${self.live_order_usd:.2f}, max ${self.max_order_usd:.2f} "
                    f"(hard cap ${HARD_MAX_ORDER_USD:.2f})"
                )
                return True, "order sizing updated"

            return False, "order sizing unchanged"

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
        if self.require_live_feed and not self.live_feed and not self.paper_mode:
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
                should_trade = slot["selected"] and (
                    self.always_trade_enabled or random.random() < self._signal_chance(desk)
                )
                if should_trade:
                    ollama_tag = self._model_provider_map().get(name)
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
            action = 0

        selective_reverse_used = False

        # Apply reversal strategy if configured
        if SIGNAL_STRATEGY == "reversal":
            action = -action  # Invert signal: LONG->SHORT, SHORT->LONG, HOLD->HOLD

        move_pct = self._desk_recent_move_pct(desk_key)
        entry_move_threshold = max(MIN_TRADE_MOVE_PCT, MIN_PROFIT_EDGE_PCT)
        momentum_threshold = max(MOMENTUM_OVERRIDE_THRESHOLD_PCT, entry_move_threshold)
        # Flat-market gate: suppress directional signal if market isn't moving enough
        if action != 0:
            if abs(move_pct) < entry_move_threshold:
                action = 0

        selective_reverse_threshold = max(
            SELECTIVE_REVERSE_MIN_MOVE_PCT,
            MOMENTUM_OVERRIDE_THRESHOLD_PCT,
            entry_move_threshold,
        )
        if SIGNAL_STRATEGY == "selective_reverse" and action != 0:
            if action == 1 and move_pct <= -selective_reverse_threshold:
                action = -1
                selective_reverse_used = True
            elif action == -1 and move_pct >= selective_reverse_threshold:
                action = 1
                selective_reverse_used = True

        # Apply trend filtering based on strategy
        if SIGNAL_STRATEGY == "trend_filter":
            trend_threshold = STRICT_TREND_FILTER_PCT
        elif SIGNAL_STRATEGY == "selective_reverse":
            trend_threshold = selective_reverse_threshold
        else:
            trend_threshold = MOMENTUM_OVERRIDE_THRESHOLD_PCT

        # Guardrail: suppress entries that directly oppose current desk momentum direction.
        if action == 1 and move_pct <= -trend_threshold:
            action = 0
        elif action == -1 and move_pct >= trend_threshold:
            action = 0
        live_desk = desk_key
        live_side = "HOLD"
        hold_override_used = False
        with self.lock:
            m = self.models.get(name)
            if not m:
                return
            slot = m["desk_state"][desk_key]
            if not slot["selected"]:
                return

            # Anti-stall: if a model is stuck on HOLD for multiple ticks,
            # allow a momentum tiebreaker only when move strength is meaningful.
            if (
                action == 0
                and HOLD_STREAK_MOMENTUM_OVERRIDE_ENABLED
                and int(slot.get("hold_streak", 0)) >= max(1, HOLD_STREAK_MOMENTUM_OVERRIDE_MIN_STREAK)
                and abs(move_pct) >= momentum_threshold
            ):
                action = 1 if move_pct > 0 else -1
                hold_override_used = True

            side = "LONG" if action == 1 else ("SHORT" if action == -1 else "HOLD")
            hard_live_block = (
                (not self.paper_mode)
                and self.live_blocked
                and "Insufficient USDT" not in self.live_blocked_reason
            )
            if self.live_trading and hard_live_block and side in {"LONG", "SHORT"}:
                side = "HOLD"
            slot["signal_source"] = "ai"
            slot["last_signal"] = side
            if side == "HOLD":
                slot["hold_streak"] = int(slot.get("hold_streak", 0)) + 1
                if self.hold_cooldown_ticks > 0:
                    slot["hold_cooldown_until_tick"] = max(
                        int(slot.get("hold_cooldown_until_tick", 0)),
                        self.tick_count + self.hold_cooldown_ticks,
                    )
                slot["hold_signals"] = int(slot.get("hold_signals", 0)) + 1
                # By default, do not force-close on HOLD in live mode.
                # This avoids churn losses in ranging markets; directional
                # flips and risk guardrails still close positions as needed.
                if HOLD_CLOSES_POSITION:
                    self._close_trade_if_open(name, desk_key, slot, "hold_signal", ref_price)
                    slot["pos"] = 0.0
                slot["mark_price"] = ref_price
            else:
                slot["hold_streak"] = 0
                slot["hold_cooldown_until_tick"] = 0
                slot["directional_signals"] = int(slot.get("directional_signals", 0)) + 1
                slot["pos"] = action * (slot["balance"] / max(ref_price, 1.0)) * self._position_scale(desk_key)
                slot["entry"] = ref_price
                slot["mark_price"] = ref_price
                self._roll_trade_on_signal(name, desk_key, slot, side, ref_price, "signal_flip")
            desk = desk_key.upper()
            live_side = side
            self.add_log(f"{name} [{desk}]: {side} @ ${ref_price:,.2f} [AI]")
            if hold_override_used and side in {"LONG", "SHORT"}:
                self.add_log(
                    f"{name} [{desk}]: HOLD override -> {side} (hold_streak={int(slot.get('hold_streak', 0))}, move={move_pct:+.4f}%)"
                )
            if selective_reverse_used and side in {"LONG", "SHORT"}:
                self.add_log(
                    f"{name} [{desk}]: selective reverse -> {side} (move={move_pct:+.4f}%)"
                )
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
                active_ledger = self.paper_ledger if self.paper_mode else self.live_ledger
                # Strict mode P&L from active execution ledger (paper or live).
                btc_pnl = self._ledger_desk_total_pnl("btc", ledger=active_ledger)
                basket_pnl = self._ledger_desk_total_pnl("basket", ledger=active_ledger)
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
                    active_ledger = self.paper_ledger if self.paper_mode else self.live_ledger
                    btc_r = self._ledger_model_desk_pnl(nm, "btc", ledger=active_ledger)
                    basket_r = self._ledger_model_desk_pnl(nm, "basket", ledger=active_ledger)
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
                        "PAPER" if self.paper_mode
                        else (
                            "LIVE_BLOCKED" if (self.live_trading and self.live_blocked)
                            else ("LIVE" if self.live_trading else "LIVE_BLOCKED")
                        )
                    ),
                    "no_simulation": self.strict_no_simulation,
                    "paper_mode": self.paper_mode,
                    "require_live_feed": self.require_live_feed,
                    "feed_paused": self.feed_paused,
                    "feed_pause_reason": self.feed_pause_reason,
                    "feed_error": self.feed_error,
                    "kill_switch": self.kill_switch,
                    "halt_reason": self.halt_reason,
                    "live_blocked": self.live_blocked,
                    "live_blocked_reason": self.live_blocked_reason,
                    "order_usd": self.live_order_usd,
                    "basket_order_usd": self.basket_order_usd,
                    "max_order_usd": self.max_order_usd,
                    "daily_loss_limit_usd": self.daily_loss_limit_usd,
                    "min_executable_order_usd": self._global_execution_min_notional_usd(),
                    "allow_cross_symbol_fallback": self.allow_cross_symbol_fallback,
                    "allow_btc_desk_fallback": self.allow_btc_desk_fallback,
                    "one_live_entry_per_desk_per_tick": self.one_live_entry_per_desk_per_tick,
                    "one_live_entry_global_per_tick": self.one_live_entry_global_per_tick,
                    "live_duplicate_cooldown_seconds": self.live_duplicate_cooldown_seconds,
                    "live_symbol_cooldown_seconds": self.live_symbol_cooldown_seconds,
                    "directional_streak_cap": self.directional_streak_cap,
                    "directional_streak_cooldown_ticks": self.directional_streak_cooldown_ticks,
                    "directional_streak_strong_move_pct": self.directional_streak_strong_move_pct,
                    "always_trade_enabled": self.always_trade_enabled,
                    "order_queue": self.live_order_queue.qsize(),
                    "auto_select_enabled": self.auto_select_enabled,
                    "auto_select_top_n": self.auto_select_top_n,
                    "auto_select_interval_ticks": self.auto_select_interval_ticks,
                    "skip_selected_hold_on_signal": self.skip_selected_hold_on_signal,
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
                    "ollama_models": list(self._model_provider_map().keys()),
                    "puter_grok_enabled": bool(self.puter_auth_token),
                    "canary": self._canary_summary(),
                    "execution_core": self.execution_core.health_snapshot(),
                    "compatibility_api": {
                        "version": "v1",
                        "state_route": "/api/v1/state",
                        "post_route_prefix": "/api/v1",
                    },
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
                "paper_total_fees_usd": self.paper_total_fees_usd,
                "paper_summary": self._ledger_summary(self.paper_ledger),
                "models": self.models,
                "model_stats": model_stats,
                "start_balance": START_BALANCE,
                "daily_summary": self._get_daily_summary(),
                "binance_pnl": self.binance_pnl,
                "server_started_at": self.server_started_at,
                "logs": self.logs[:],
                "desk_logs": {desk: logs[:] for desk, logs in self.desk_logs.items()},
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
            # Backfill auto-selection whenever either desk is empty.
            should_bootstrap_selection = self.selected_count("btc") == 0 or self.selected_count("basket") == 0
            if self.auto_select_enabled and (
                should_bootstrap_selection or self.tick_count % self.auto_select_interval_ticks == 0
            ):
                self._auto_select_models()
            self.step_models()
        self.maybe_auto_cutover()


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
        if path in {"/api/state", "/api/v1/state", "/data"}:
            self._json(200, self.state.snapshot())
            return
        if path == "/api/core/health":
            self._json(200, {"ok": True, "health": self.state.execution_core_health()})
            return
        if path == "/":
            self.path = "/quantplot_ai.html"
        super().do_GET()

    def do_POST(self) -> None:
        route_path = self.path
        if route_path.startswith("/api/v1/"):
            route_path = "/api/" + route_path[len("/api/v1/"):]

        if route_path not in {
            "/api/select",
            "/api/deselect",
            "/api/pause",
            "/api/order-size",
            "/api/flatten-futures",
            "/api/core/mode",
            "/api/core/fallback",
            "/api/core/cutover",
            "/api/core/gate",
            "/api/store/backup",
            "/api/store/purge",
            "/api/auto-select",
            "/api/desks/clear",
            "/api/away-mode",
            "/api/puter-token",
            "/api/message/pnl-health",
            "/api/message/recent-summary",
            "/api/paper-mode",
        }:
            self._json(404, {"ok": False, "error": "Not found"})
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        try:
            data = json.loads(self.rfile.read(content_length).decode("utf-8")) if content_length else {}
        except Exception:
            self._json(400, {"ok": False, "error": "Invalid JSON"})
            return

        if route_path == "/api/store/backup":
            info = self.state.create_store_backup()
            self._json(200, {"ok": True, **info})
            return

        if route_path == "/api/store/purge":
            backup_first = data.get("backup_first", True)
            info = self.state.purge_stores(bool(backup_first))
            self._json(200, info)
            return

        if route_path == "/api/flatten-futures":
            result = self.state.flatten_all_futures_positions()
            code = 200 if result.get("ok") else 500
            self._json(code, result)
            return

        if route_path == "/api/core/mode":
            mode = data.get("mode", "")
            if not isinstance(mode, str):
                self._json(400, {"ok": False, "error": "mode must be a string"})
                return
            changed, msg = self.state.set_execution_core_mode(mode)
            code = 200 if changed else 409
            self._json(code, {
                "ok": changed,
                "changed": changed,
                "mode": self.state.execution_core_health().get("mode"),
                "message": msg,
            })
            return

        if route_path == "/api/core/fallback":
            enabled = data.get("enabled")
            if not isinstance(enabled, bool):
                self._json(400, {"ok": False, "error": "enabled must be boolean"})
                return
            changed, msg = self.state.set_execution_core_fallback(enabled)
            code = 200 if changed else 409
            self._json(code, {
                "ok": changed,
                "changed": changed,
                "fallback_enabled": bool(enabled),
                "message": msg,
            })
            return

        if route_path == "/api/core/cutover":
            enabled = data.get("enabled")
            if not isinstance(enabled, bool):
                self._json(400, {"ok": False, "error": "enabled must be boolean"})
                return
            if enabled:
                decision = self.state.execution_core_gate_decision()
                if not decision.get("allowed"):
                    self._json(423, {
                        "ok": False,
                        "error": "cutover gate blocked",
                        "reason": decision.get("reason", "gate conditions not met"),
                    })
                    return
            target_mode = "cutover" if enabled else "shadow"
            changed, msg = self.state.set_execution_core_mode(target_mode)
            code = 200 if changed else 409
            self._json(code, {
                "ok": changed,
                "changed": changed,
                "mode": target_mode,
                "message": msg,
            })
            return

        if route_path == "/api/core/gate":
            updates: dict = {}
            if "enabled" in data:
                if not isinstance(data.get("enabled"), bool):
                    self._json(400, {"ok": False, "error": "enabled must be boolean"})
                    return
                updates["enabled"] = bool(data.get("enabled"))
            if "auto_switch" in data:
                if not isinstance(data.get("auto_switch"), bool):
                    self._json(400, {"ok": False, "error": "auto_switch must be boolean"})
                    return
                updates["auto_switch"] = bool(data.get("auto_switch"))
            if "threshold" in data:
                try:
                    updates["threshold"] = float(data.get("threshold"))
                except Exception:
                    self._json(400, {"ok": False, "error": "threshold must be numeric"})
                    return
            if "min_compares" in data:
                try:
                    updates["min_compares"] = int(data.get("min_compares"))
                except Exception:
                    self._json(400, {"ok": False, "error": "min_compares must be integer"})
                    return
            if "stability_checks" in data:
                try:
                    updates["stability_checks"] = int(data.get("stability_checks"))
                except Exception:
                    self._json(400, {"ok": False, "error": "stability_checks must be integer"})
                    return

            if not updates:
                self._json(400, {"ok": False, "error": "no gate fields supplied"})
                return

            changed, msg = self.state.set_execution_core_cutover_gate(**updates)
            code = 200 if changed else 409
            health = self.state.execution_core_health()
            self._json(code, {
                "ok": changed,
                "changed": changed,
                "message": msg,
                "cutover_gate": health.get("cutover_gate"),
            })
            return

        if route_path == "/api/pause":
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

        if route_path == "/api/order-size":
            if "order_usd" not in data:
                self._json(400, {"ok": False, "error": "order_usd is required"})
                return
            order_usd = data.get("order_usd")
            max_order_usd = data.get("max_order_usd") if "max_order_usd" in data else None
            changed, msg = self.state.set_order_sizing(order_usd, max_order_usd)
            status = self.state.snapshot().get("status", {})
            self._json(200 if changed else 409, {
                "ok": changed,
                "changed": changed,
                "message": msg,
                "order_usd": status.get("order_usd"),
                "max_order_usd": status.get("max_order_usd"),
            })
            return

        if route_path == "/api/auto-select":
            enabled_raw = data.get("enabled")
            if not isinstance(enabled_raw, bool):
                self._json(400, {"ok": False, "error": "enabled must be boolean"})
                return
            changed = self.state.set_auto_select_enabled(enabled_raw)
            self._json(200, {"ok": True, "changed": changed, "enabled": bool(enabled_raw)})
            return

        if route_path == "/api/desks/clear":
            cleared = self.state.clear_all_desks()
            self._json(200, {"ok": True, "cleared": cleared})
            return

        if route_path == "/api/away-mode":
            enabled_raw = data.get("enabled")
            if not isinstance(enabled_raw, bool):
                self._json(400, {"ok": False, "error": "enabled must be boolean"})
                return
            changed = self.state.set_away_mode(enabled_raw)
            self._json(200, {"ok": True, "changed": changed, "away_mode": bool(enabled_raw)})
            return

        if route_path == "/api/puter-token":
            token_raw = data.get("token", "")
            if token_raw is not None and not isinstance(token_raw, str):
                self._json(400, {"ok": False, "error": "token must be a string"})
                return
            changed = self.state.set_puter_auth_token(token_raw or "")
            self._json(200, {
                "ok": True,
                "changed": changed,
                "puter_grok_enabled": bool(self.state.puter_auth_token),
            })
            return

        if route_path == "/api/message/pnl-health":
            msg = self.state.push_pnl_health_message()
            self._json(200, {"ok": True, "message": msg})
            return

        if route_path == "/api/message/recent-summary":
            msg = self.state.push_recent_summary_message()
            self._json(200, {"ok": True, "message": msg})
            return

        if route_path == "/api/paper-mode":
            enabled_raw = data.get("enabled")
            if not isinstance(enabled_raw, bool):
                self._json(400, {"ok": False, "error": "enabled must be boolean"})
                return
            with self.state.lock:
                prev = self.state.paper_mode
                self.state.paper_mode = bool(enabled_raw)
                changed = prev != self.state.paper_mode
                if self.state.paper_mode and changed:
                    self.state.paper_ledger = {}
                    self.state.paper_total_fees_usd = 0.0
                    for model_state in self.state.models.values():
                        for desk_key in ("btc", "basket"):
                            slot = model_state["desk_state"][desk_key]
                            ref_price = self.state.prices["btc"] if desk_key == "btc" else self.state.prices["basket"]
                            self.state._reset_internal_slot_pnl_unlocked(slot, ref_price)
                            slot["trades"] = 0
                            slot["wins"] = 0
                            slot["losses"] = 0
                            slot["directional_signals"] = 0
                            slot["hold_signals"] = 0
                            slot["hold_streak"] = 0
                    self.state.kill_switch = False
                    self.state.halt_reason = ""
                    self.state.guardrail_day = datetime.utcnow().strftime("%Y-%m-%d")
                    self.state.profit_lock_anchor = self.state._effective_portfolio_pnl()
                    self.state.profit_lock_cooldown_left = 0
                    self.state.profit_lock_reason = ""
                    self.state.always_trade_enabled = False
                    self.state.auto_select_interval_ticks = 10
                    self.state.live_duplicate_cooldown_seconds = 45.0
                    self.state.live_symbol_cooldown_seconds = 60.0
                    self.state.one_live_entry_global_per_tick = True
                    self.state.one_live_entry_per_desk_per_tick = True
                    self.state.add_log("Paper ledger reset for fresh run")
                    self.state.add_log(
                        "Paper profit profile ENABLED (always-trade=off, auto-select=10 ticks, duplicate cooldown=45s, symbol cooldown=60s, one-entry-per-tick=on, edge filter active)"
                    )
                label = "PAPER (no real orders)" if self.state.paper_mode else "LIVE (real orders)"
                self.state.add_log(f"Paper mode {'ENABLED' if self.state.paper_mode else 'DISABLED'} \u2014 {label}")
            self._json(200, {"ok": True, "changed": changed, "paper_mode": bool(enabled_raw)})
            return

        model = data.get("model", "")
        if not isinstance(model, str) or not model:
            self._json(400, {"ok": False, "error": "model is required"})
            return

        if route_path == "/api/select":
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
