#!/usr/bin/env python3
import json
import time
import urllib.request

STATE_URL = "http://127.0.0.1:8000/api/state"
CYCLES = 30
SLEEP_SECONDS = 20


def _tail_log(state: dict, desk: str) -> str:
    logs = (state.get("desk_logs") or {}).get(desk) or [""]
    return str(logs[0])


def main() -> None:
    prev_trades = None
    prev_kill_switch = None
    prev_btc_log = None
    prev_basket_log = None

    print("[ALPHA MONITOR START]")
    for i in range(CYCLES):
        try:
            state = json.loads(urllib.request.urlopen(STATE_URL, timeout=8).read())
            ts = state.get("ts", "?")
            status = state.get("status") or {}
            kill_switch = bool(status.get("kill_switch", False))
            paper = state.get("paper_summary") or {}
            trades = int(paper.get("trades", 0) or 0)
            pnl = float(state.get("app_total_pnl_usd", 0.0) or 0.0)
            btc_log = _tail_log(state, "btc")
            basket_log = _tail_log(state, "basket")

            changed = []
            if prev_trades is None or trades != prev_trades:
                changed.append(f"trades {prev_trades} -> {trades}")
            if prev_kill_switch is None or kill_switch != prev_kill_switch:
                changed.append(f"kill_switch {prev_kill_switch} -> {kill_switch}")
            if prev_btc_log is None or btc_log != prev_btc_log:
                changed.append("btc_log changed")
            if prev_basket_log is None or basket_log != prev_basket_log:
                changed.append("basket_log changed")

            if changed:
                print(f"[{ts}] PnL=${pnl:+.2f} | " + " | ".join(changed))
                print("  BTC:", btc_log[-120:])
                print("  BSK:", basket_log[-120:])

            prev_trades = trades
            prev_kill_switch = kill_switch
            prev_btc_log = btc_log
            prev_basket_log = basket_log

            if kill_switch:
                print("[ALERT] Kill switch tripped, monitor stopping.")
                break
        except Exception as exc:
            print(f"[MONITOR ERROR] {exc}")

        if i < CYCLES - 1:
            time.sleep(SLEEP_SECONDS)

    print("[ALPHA MONITOR END]")


if __name__ == "__main__":
    main()
