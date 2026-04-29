#!/usr/bin/env python3
import json
import os
import time
import urllib.request

BASE = "http://127.0.0.1:8000"
DURATION_SECONDS = int(os.getenv("ALPHA_CONTROLLED_DURATION_SECONDS", "240"))
POLL_SECONDS = int(os.getenv("ALPHA_CONTROLLED_POLL_SECONDS", "10"))


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


def tail(state: dict, desk: str) -> str:
    return ((state.get("desk_logs") or {}).get(desk) or [""])[0]


def summarize(state: dict) -> dict:
    ps = state.get("paper_summary") or {}
    return {
        "ts": state.get("ts"),
        "net_pnl": float(state.get("app_total_pnl_usd", 0.0) or 0.0),
        "ex_fee_pnl": float(state.get("app_total_pnl_excl_fees_usd", 0.0) or 0.0),
        "fee_drag": float((state.get("app_total_pnl_excl_fees_usd", 0.0) or 0.0) - (state.get("app_total_pnl_usd", 0.0) or 0.0)),
        "trades": int(ps.get("trades", 0) or 0),
        "wins": int(ps.get("wins", 0) or 0),
        "losses": int(ps.get("losses", 0) or 0),
        "open_positions": int(ps.get("open_positions", 0) or 0),
        "win_rate_pct": float(ps.get("win_rate_pct", 0.0) or 0.0),
        "btc_last": tail(state, "btc"),
        "basket_last": tail(state, "basket"),
    }


def main() -> None:
    # Stabilize before measurement and force-reset prior paper carryover.
    print("prep", post("/api/paper-mode", {"enabled": False}))
    print("prep", post("/api/paper-mode", {"enabled": True}))
    print("prep", post("/api/auto-select", {"enabled": False}))
    print("prep", post("/api/desks/clear", {}))
    print("prep", post("/api/select", {"model": "Qwen-2.5", "desk": "btc"}))
    print("prep", post("/api/select", {"model": "DeepSeek-R1", "desk": "basket"}))

    start_state = get_state()
    start = summarize(start_state)
    print("START", json.dumps(start, indent=2))

    prev_trades = start["trades"]
    prev_btc = start["btc_last"]
    prev_basket = start["basket_last"]

    polls = max(1, DURATION_SECONDS // POLL_SECONDS)
    for i in range(polls):
        time.sleep(POLL_SECONDS)
        st = get_state()
        cur = summarize(st)
        changes = []
        if cur["trades"] != prev_trades:
            changes.append(f"trades {prev_trades}->{cur['trades']}")
        if cur["btc_last"] != prev_btc:
            changes.append("btc_log")
        if cur["basket_last"] != prev_basket:
            changes.append("basket_log")
        if changes:
            print(f"[{cur['ts']}] net={cur['net_pnl']:+.2f} ex_fee={cur['ex_fee_pnl']:+.2f} "
                  f"trades={cur['trades']} changes={','.join(changes)}")
            print("  BTC:", cur["btc_last"][-120:])
            print("  BSK:", cur["basket_last"][-120:])
        prev_trades = cur["trades"]
        prev_btc = cur["btc_last"]
        prev_basket = cur["basket_last"]

    end_state = get_state()
    end = summarize(end_state)
    print("END", json.dumps(end, indent=2))

    delta = {
        "delta_net_pnl": end["net_pnl"] - start["net_pnl"],
        "delta_ex_fee_pnl": end["ex_fee_pnl"] - start["ex_fee_pnl"],
        "delta_trades": end["trades"] - start["trades"],
        "delta_wins": end["wins"] - start["wins"],
        "delta_losses": end["losses"] - start["losses"],
    }
    print("DELTA", json.dumps(delta, indent=2))


if __name__ == "__main__":
    main()
