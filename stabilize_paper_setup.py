#!/usr/bin/env python3
import json
import urllib.request

BASE = "http://127.0.0.1:8000"


def post(path: str, payload: dict) -> tuple[int, dict]:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        body = resp.read().decode() or "{}"
        return resp.status, json.loads(body)


def get_state() -> dict:
    with urllib.request.urlopen(BASE + "/api/state", timeout=8) as resp:
        return json.loads(resp.read().decode())


def main() -> None:
    steps = [
        ("pause_all_on", "/api/pause", {"desk": "all", "paused": True}),
        ("auto_select_off", "/api/auto-select", {"enabled": False}),
        ("clear_desks", "/api/desks/clear", {}),
        ("paper_mode_on", "/api/paper-mode", {"enabled": True}),
        ("select_btc", "/api/select", {"model": "Qwen-2.5", "desk": "btc"}),
        ("select_basket", "/api/select", {"model": "DeepSeek-R1", "desk": "basket"}),
        ("pause_btc_off", "/api/pause", {"desk": "btc", "paused": False}),
        ("pause_basket_off", "/api/pause", {"desk": "basket", "paused": False}),
        ("pause_all_off", "/api/pause", {"desk": "all", "paused": False}),
    ]

    for label, path, payload in steps:
        try:
            status, body = post(path, payload)
            print(label, status, body)
        except Exception as exc:
            print(label, "ERR", str(exc))

    state = get_state()
    status = state.get("status") or {}
    paper = state.get("paper_summary") or {}
    snapshot = {
        "ts": state.get("ts"),
        "mode": status.get("mode"),
        "feed": status.get("feed"),
        "kill_switch": status.get("kill_switch"),
        "auto_select_enabled": status.get("auto_select_enabled"),
        "pause_all_desks": status.get("pause_all_desks"),
        "pause_btc": status.get("pause_btc"),
        "pause_basket": status.get("pause_basket"),
        "trades": paper.get("trades"),
        "wins": paper.get("wins"),
        "losses": paper.get("losses"),
        "open_positions": paper.get("open_positions"),
        "app_total_pnl_usd": state.get("app_total_pnl_usd"),
        "app_total_pnl_excl_fees_usd": state.get("app_total_pnl_excl_fees_usd"),
        "btc_last": ((state.get("desk_logs", {}).get("btc") or [""])[-1]),
        "basket_last": ((state.get("desk_logs", {}).get("basket") or [""])[-1]),
    }
    print("SNAPSHOT", json.dumps(snapshot, indent=2))


if __name__ == "__main__":
    main()
