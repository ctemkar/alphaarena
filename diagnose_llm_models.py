#!/usr/bin/env python3
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime

WORKSPACE = "/Users/chetantemkar/development/alphaarena"
STATE_URL = "http://127.0.0.1:8000/api/state"
SELECT_URL = "http://127.0.0.1:8000/api/select"
PAPER_MODE_URL = "http://127.0.0.1:8000/api/paper-mode"
CLEAR_URL = "http://127.0.0.1:8000/api/desks/clear"

DURATION_SECONDS = max(5, int(os.getenv("ALPHA_DIAG_DURATION_SECONDS", "10")))
CHECK_SECONDS = max(5, int(os.getenv("ALPHA_DIAG_CHECK_SECONDS", "5")))
MAX_MODELS = max(1, int(os.getenv("ALPHA_DIAG_MAX_MODELS", "7")))
TARGET_DESK = "btc"


def _post_json(url: str, payload: dict) -> tuple[int, dict]:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=6) as resp:
        body = resp.read().decode() or "{}"
        return resp.status, json.loads(body)


def _get_state() -> dict:
    with urllib.request.urlopen(urllib.request.Request(STATE_URL), timeout=6) as resp:
        return json.loads(resp.read().decode())


def _get_state_retry(tries: int = 5, delay: float = 0.8) -> dict | None:
    for _ in range(tries):
        try:
            return _get_state()
        except Exception:
            time.sleep(delay)
    return None


def _wait_ready(max_tries: int = 30) -> bool:
    for _ in range(max_tries):
        if _get_state_retry(tries=1, delay=0.0) is not None:
            return True
        time.sleep(1)
    return False


def _start_server() -> subprocess.Popen:
    env = os.environ.copy()
    env["ALPHA_SIGNAL_STRATEGY"] = "simple_prompt"
    env["ALPHA_INSECURE_SSL"] = "1"
    env["ALPHA_LIVE_TRADING"] = "0"
    env["ALPHA_PAPER_MODE"] = "1"
    env["ALPHA_AUTO_SELECT_ENABLED"] = "0"
    env["ALPHA_BASE_SIGNAL_CHANCE"] = "1.0"
    env["ALPHA_MIN_PROFIT_EDGE_PCT"] = "0.0"
    env["ALPHA_MIN_TRADE_MOVE_PCT"] = "0.0"
    env["ALPHA_MOMENTUM_OVERRIDE_THRESHOLD_PCT"] = "0.02"
    env["ALPHA_HOLD_STREAK_MOMENTUM_OVERRIDE_ENABLED"] = "0"

    return subprocess.Popen(
        [sys.executable, "quantplot_ai_server.py"],
        cwd=WORKSPACE,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _stop_server(proc: subprocess.Popen) -> None:
    if proc and proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=6)
        except subprocess.TimeoutExpired:
            proc.kill()


def run_model(model_name: str) -> dict:
    proc = _start_server()
    try:
        if not _wait_ready():
            return {"model": model_name, "error": "server_not_ready"}

        _post_json(PAPER_MODE_URL, {"enabled": True})
        _post_json(CLEAR_URL, {})
        _post_json(SELECT_URL, {"model": model_name, "desk": TARGET_DESK})

        loops = max(1, DURATION_SECONDS // CHECK_SECONDS)
        for _ in range(loops):
            time.sleep(CHECK_SECONDS)

        pre = _get_state_retry() or {}
        _post_json(CLEAR_URL, {})
        time.sleep(2)
        post = _get_state_retry() or {}

        pre_net = float(pre.get("app_total_pnl_usd", 0.0) or 0.0)
        pre_ex_fee = float(pre.get("app_total_pnl_excl_fees_usd", 0.0) or 0.0)
        post_net = float(post.get("app_total_pnl_usd", 0.0) or 0.0)
        post_ex_fee = float(post.get("app_total_pnl_excl_fees_usd", 0.0) or 0.0)
        fee_drag = post_ex_fee - post_net

        ps = post.get("paper_summary") or {}
        return {
            "model": model_name,
            "desk": TARGET_DESK,
            "duration_seconds": DURATION_SECONDS,
            "pre_clear_net_pnl": pre_net,
            "pre_clear_pnl_ex_fees": pre_ex_fee,
            "post_clear_net_pnl": post_net,
            "post_clear_pnl_ex_fees": post_ex_fee,
            "fee_drag_usd": fee_drag,
            "trades": int(ps.get("trades", 0) or 0),
            "wins": int(ps.get("wins", 0) or 0),
            "losses": int(ps.get("losses", 0) or 0),
            "open_positions": int(ps.get("open_positions", 0) or 0),
        }
    except Exception as exc:
        return {"model": model_name, "error": str(exc)}
    finally:
        _stop_server(proc)
        time.sleep(1)


def main() -> None:
    boot = _start_server()
    try:
        if not _wait_ready():
            print("ERROR: could not start bootstrap server")
            return
        st = _get_state_retry() or {}
        model_names = sorted((st.get("models") or {}).keys())[:MAX_MODELS]
    finally:
        _stop_server(boot)
        time.sleep(1)

    results = []
    print(
        f"[{datetime.now().strftime('%H:%M:%S')}] Running LLM diagnostics for "
        f"{len(model_names)} models on {TARGET_DESK.upper()} desk "
        f"(duration={DURATION_SECONDS}s, check={CHECK_SECONDS}s)"
    )
    for m in model_names:
        print(f"  -> {m}")
        results.append(run_model(m))

    valid = [r for r in results if "error" not in r]
    valid.sort(key=lambda x: x.get("post_clear_pnl_ex_fees", -1e9), reverse=True)

    print("\nMODEL_RESULTS")
    print(json.dumps(valid, indent=2))

    out_path = "/tmp/llm_diagnostic_results.json"
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(valid, fh, indent=2)
    print(f"\nSaved {out_path}")


if __name__ == "__main__":
    main()
