#!/usr/bin/env python3
import json
import time
import urllib.request

BASE = "http://127.0.0.1:8000"


def post(path: str, payload: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        return resp.status, json.loads((resp.read().decode() or "{}"))


def get_state() -> dict:
    with urllib.request.urlopen(BASE + "/api/state", timeout=8) as resp:
        return json.loads(resp.read().decode())


def summarize(tag: str, state: dict) -> None:
    paper = state.get("paper_summary") or {}
    out = {
        "tag": tag,
        "ts": state.get("ts"),
        "pnl": state.get("app_total_pnl_usd"),
        "pnl_ex_fee": state.get("app_total_pnl_excl_fees_usd"),
        "open_positions": paper.get("open_positions"),
        "trades": paper.get("trades"),
        "btc_last": ((state.get("desk_logs", {}).get("btc") or [""])[0]),
    }
    print(json.dumps(out, indent=2))


def main() -> None:
    print("clear-1", post("/api/desks/clear", {}))
    summarize("after-clear-1", get_state())

    print("select-btc", post("/api/select", {"model": "Qwen-2.5", "desk": "btc"}))
    time.sleep(12)
    summarize("after-12s", get_state())

    print("clear-2", post("/api/desks/clear", {}))
    summarize("after-clear-2", get_state())


if __name__ == "__main__":
    main()
