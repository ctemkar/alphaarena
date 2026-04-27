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

STATE_URL = "http://127.0.0.1:8000/api/state"
SELECT_URL = "http://127.0.0.1:8000/api/select"
PAPER_MODE_URL = "http://127.0.0.1:8000/api/paper-mode"
CLEAR_URL = "http://127.0.0.1:8000/api/desks/clear"
WORKSPACE = "/Users/chetantemkar/development/alphaarena"

MODELS = {
    "btc": "Qwen-2.5",
    "basket": "DeepSeek-R1",
}
DURATION_SECONDS = 60
CHECK_SECONDS = 10


def _post_json(url: str, payload: dict):
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


def _get_state_with_retry(max_tries: int = 5, sleep_seconds: float = 1.0) -> dict | None:
    for _ in range(max_tries):
        try:
            return _get_state()
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(sleep_seconds)
        except Exception:
            time.sleep(sleep_seconds)
    return None


def _wait_ready(max_tries: int = 30) -> bool:
    for _ in range(max_tries):
        try:
            _get_state()
            return True
        except Exception:
            time.sleep(1)
    return False


def _start_server() -> subprocess.Popen:
    env = os.environ.copy()
    env["ALPHA_SIGNAL_STRATEGY"] = "reversal"
    env["ALPHA_INSECURE_SSL"] = "1"
    env["ALPHA_LIVE_TRADING"] = "0"
    env["ALPHA_PAPER_MODE"] = "1"
    env["ALPHA_AUTO_SELECT_ENABLED"] = "0"
    env["ALPHA_BASE_SIGNAL_CHANCE"] = "1.0"
    env["ALPHA_MIN_PROFIT_EDGE_PCT"] = "0.0"
    env["ALPHA_MIN_TRADE_MOVE_PCT"] = "0.0"
    env["ALPHA_MOMENTUM_OVERRIDE_THRESHOLD_PCT"] = "0.005"
    env["ALPHA_HOLD_STREAK_MOMENTUM_OVERRIDE_ENABLED"] = "1"
    env["ALPHA_HOLD_STREAK_MOMENTUM_OVERRIDE_MIN_STREAK"] = "1"

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


def run_one_desk(desk: str) -> dict:
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Reversal on {desk.upper()} only")
    proc = _start_server()
    samples = []
    try:
        if not _wait_ready():
            return {"desk": desk, "error": "server_not_ready"}

        _post_json(PAPER_MODE_URL, {"enabled": True})
        _post_json(CLEAR_URL, {})
        _post_json(SELECT_URL, {"model": MODELS[desk], "desk": desk})

        loops = DURATION_SECONDS // CHECK_SECONDS
        missed_state_reads = 0
        for i in range(loops):
            if proc.poll() is not None:
                return {
                    "desk": desk,
                    "model": MODELS[desk],
                    "duration_seconds": DURATION_SECONDS,
                    "error": "server_exited_early",
                    "samples_collected": len(samples),
                }

            state = _get_state_with_retry(max_tries=4, sleep_seconds=0.8)
            if state is None:
                missed_state_reads += 1
                print(f"  t={i * CHECK_SECONDS:>3}s state_read_failed")
                time.sleep(CHECK_SECONDS)
                continue

            desk_pnl = float((state.get("desk_pnl") or {}).get(desk, 0.0) or 0.0)
            ps = state.get("paper_summary") or {}
            print(
                f"  t={i * CHECK_SECONDS:>3}s desk_pnl={desk_pnl:+.2f} "
                f"trades={int(ps.get('trades', 0) or 0)} open={int(ps.get('open_positions', 0) or 0)}"
            )
            samples.append(desk_pnl)
            time.sleep(CHECK_SECONDS)

        before_clear = _get_state_with_retry(max_tries=6, sleep_seconds=1.0) or {}
        pre_clear = float((before_clear.get("desk_pnl") or {}).get(desk, 0.0) or 0.0)

        _post_json(CLEAR_URL, {})
        time.sleep(2)

        after_clear = _get_state_with_retry(max_tries=6, sleep_seconds=1.0) or {}
        post_clear = float((after_clear.get("desk_pnl") or {}).get(desk, 0.0) or 0.0)

        return {
            "desk": desk,
            "model": MODELS[desk],
            "duration_seconds": DURATION_SECONDS,
            "min_desk_pnl": min(samples) if samples else 0.0,
            "max_desk_pnl": max(samples) if samples else 0.0,
            "samples_collected": len(samples),
            "state_read_failures": missed_state_reads,
            "pre_clear_desk_pnl": pre_clear,
            "post_clear_desk_pnl": post_clear,
            "paper_summary_after_clear": after_clear.get("paper_summary", {}),
        }
    finally:
        _stop_server(proc)
        time.sleep(2)


def main() -> None:
    results = [run_one_desk("btc"), run_one_desk("basket")]
    print("\nFINAL_RESULTS")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
