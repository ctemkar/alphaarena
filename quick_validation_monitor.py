#!/usr/bin/env python3
import json
import time
import urllib.request

URL = "http://127.0.0.1:8000/api/state"


def tail(state: dict, desk: str) -> str:
    return ((state.get("desk_logs") or {}).get(desk) or [""])[0]


def main() -> None:
    prev = {"trades": None, "btc": None, "basket": None}
    print("[VALIDATION START]")
    for _ in range(6):
        state = json.loads(urllib.request.urlopen(URL, timeout=8).read())
        status = state.get("status") or {}
        paper = state.get("paper_summary") or {}
        trades = int(paper.get("trades", 0) or 0)
        pnl = float(state.get("app_total_pnl_usd", 0.0) or 0.0)
        ex_fee = float(state.get("app_total_pnl_excl_fees_usd", 0.0) or 0.0)
        btc = tail(state, "btc")
        basket = tail(state, "basket")

        changed = []
        if prev["trades"] is None or trades != prev["trades"]:
            changed.append(f"trades {prev['trades']} -> {trades}")
        if prev["btc"] is None or btc != prev["btc"]:
            changed.append("btc log changed")
        if prev["basket"] is None or basket != prev["basket"]:
            changed.append("basket log changed")

        print(
            f"[{state.get('ts','?')}] mode={status.get('mode')} feed={status.get('feed')} "
            f"KS={status.get('kill_switch')} PnL={pnl:+.2f} ExFee={ex_fee:+.2f} trades={trades}"
        )
        if changed:
            print("  changes:", ", ".join(changed))
            print("  BTC:", btc[-130:])
            print("  BSK:", basket[-130:])

        prev = {"trades": trades, "btc": btc, "basket": basket}
        time.sleep(10)

    print("[VALIDATION END]")


if __name__ == "__main__":
    main()
