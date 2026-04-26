#!/usr/bin/env python3
import json
import time
import urllib.request
from datetime import datetime
from pathlib import Path

API_BASE = "http://127.0.0.1:8000"
STATE_URL = f"{API_BASE}/api/state"
PAPER_MODE_URL = f"{API_BASE}/api/paper-mode"

LOG_PATH = Path("_pm_safe.log")
STATUS_PATH = Path("paper_monitor_status.json")
INTERVAL_SECONDS = 60


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def write_log(message: str) -> None:
    line = f"[{now()}] {message}"
    print(line, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def get_state():
    try:
        raw = urllib.request.urlopen(STATE_URL, timeout=3).read().decode()
        return json.loads(raw)
    except Exception:
        return None


def post_paper_mode(enabled: bool):
    payload = json.dumps({"enabled": enabled}).encode()
    req = urllib.request.Request(
        PAPER_MODE_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        raw = urllib.request.urlopen(req, timeout=5).read().decode()
        return json.loads(raw)
    except Exception:
        return None


def write_status(status_obj) -> None:
    STATUS_PATH.write_text(json.dumps(status_obj, indent=2), encoding="utf-8")


def main() -> None:
    write_log("SAFE monitor started (paper-only enforcement; no off/on toggles)")
    tick = 0
    while True:
        tick += 1
        state = get_state()

        if not state:
            write_status({
                "ts": now(),
                "api_up": False,
                "mode": "safe-paper-monitor",
                "action": "none",
                "note": "api unreachable"
            })
            write_log("API unreachable")
            time.sleep(INTERVAL_SECONDS)
            continue

        st = state.get("status", {})
        ps = state.get("paper_summary", {})
        pnl = float(state.get("app_total_pnl_usd", 0) or 0)

        action = "none"
        paper_mode = bool(st.get("paper_mode"))
        if not paper_mode:
            # Safety-only fix: force ON, never toggle OFF.
            resp = post_paper_mode(True)
            action = "paper_mode_forced_on" if resp and resp.get("ok") else "paper_mode_enforce_failed"
            write_log(f"Safety action: {action}")

        status_obj = {
            "ts": now(),
            "api_up": True,
            "mode": "safe-paper-monitor",
            "action": action,
            "kill_switch": bool(st.get("kill_switch")),
            "halt_reason": st.get("halt_reason", ""),
            "paper_mode": bool(st.get("paper_mode")),
            "live_trading": st.get("live_trading"),
            "pnl_usd": pnl,
            "paper_summary": {
                "trades": int(ps.get("trades", 0) or 0),
                "wins": int(ps.get("wins", 0) or 0),
                "losses": int(ps.get("losses", 0) or 0),
                "win_rate_pct": float(ps.get("win_rate_pct", 0) or 0),
            },
            "tick": tick,
        }
        write_status(status_obj)

        if tick % 5 == 0:
            write_log(
                f"tick={tick} paper_mode={status_obj['paper_mode']} kill_switch={status_obj['kill_switch']} "
                f"trades={status_obj['paper_summary']['trades']} pnl=${status_obj['pnl_usd']:.2f}"
            )

        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
