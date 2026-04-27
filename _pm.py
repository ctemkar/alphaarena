import datetime
import json
import re
import subprocess
import time
import urllib.request
from pathlib import Path
from urllib.error import HTTPError, URLError

API_URL = "http://127.0.0.1:8000/api/state"
BASE_URL = "http://127.0.0.1:8000"
STATUS_PATH = Path("paper_monitor_status.json")
POLL_SECONDS = 60
CHECKPOINT_SECONDS = 600
RUN_SECONDS = 5400
STALL_SIGNAL_SECONDS = 300
STALL_TRADE_SECONDS = 600
REPAIR_COOLDOWN_SECONDS = 180
DESKS = ("btc", "basket")


def fetch():
    r = urllib.request.urlopen(API_URL, timeout=5)
    return json.loads(r.read().decode())


def post_json(path, payload):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        BASE_URL + path,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=6) as r:
        return json.loads(r.read().decode())


def now_hms():
    return datetime.datetime.now().strftime("[%H:%M:%S]")


def notify(title, message):
    script = f"display notification {json.dumps(message)} with title {json.dumps(title)}"
    try:
        subprocess.run(["osascript", "-e", script], check=False, capture_output=True)
    except Exception:
        pass


def selected_models_by_desk(data):
    out = {"btc": [], "basket": []}
    models = data.get("models") or {}
    for name, model in models.items():
        desk_state = model.get("desk_state") or {}
        for desk in DESKS:
            if bool((desk_state.get(desk) or {}).get("selected")):
                out[desk].append(name)
    return out


def best_model_for_desk(data, desk, exclude=None):
    exclude = exclude or set()
    models = data.get("models") or {}
    best_name = None
    best_score = None
    for name, model in models.items():
        if name in exclude:
            continue
        slot = (model.get("desk_state") or {}).get(desk) or {}
        score = (
            int(slot.get("directional_signals", 0) or 0),
            int(slot.get("trades", 0) or 0),
            int(slot.get("wins", 0) or 0) - int(slot.get("losses", 0) or 0),
        )
        if best_score is None or score > best_score:
            best_name = name
            best_score = score
    return best_name


def rotate_model_for_desk(data, desk):
    selected = set(selected_models_by_desk(data).get(desk) or [])
    candidate = best_model_for_desk(data, desk, exclude=selected)
    if not candidate:
        candidate = best_model_for_desk(data, desk, exclude=set())
    if not candidate:
        return f"{desk.upper()}: no candidate model available"
    try:
        post_json("/api/select", {"model": candidate, "desk": desk})
        return f"{desk.upper()}: selected {candidate}"
    except Exception as exc:
        return f"{desk.upper()}: select failed ({exc})"


def auto_repair(data, reasons):
    actions = []
    status = data.get("status") or {}
    if not bool(status.get("paper_mode")):
        try:
            resp = post_json("/api/paper-mode", {"enabled": True})
            actions.append(f"paper_mode->ON changed={bool(resp.get('changed'))}")
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            actions.append(f"paper_mode set failed ({exc})")

    selected = selected_models_by_desk(data)
    for desk in DESKS:
        if not selected.get(desk):
            actions.append(rotate_model_for_desk(data, desk))

    if "signal_stall" in reasons or "trade_stall" in reasons:
        for desk in DESKS:
            actions.append(rotate_model_for_desk(data, desk))
    return actions


def summarize_payload(data, signals, elapsed, verdict="", final=False, status="running"):
    app = float(data.get("app_total_pnl_usd", 0) or 0)
    fees = float(data.get("paper_total_fees_usd", 0) or 0)
    # paper_summary is added by the updated server; fall back to daily_summary for
    # compatibility with older server builds that only expose daily_summary.
    summary = data.get("paper_summary") or data.get("daily_summary") or {}
    state = data.get("status") or {}
    halt_reason = (state.get("halt_reason") or "").strip()
    kill_switch = bool(state.get("kill_switch"))
    longs = signals.count("LONG")
    shorts = signals.count("SHORT")
    return {
        "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "elapsed_min": int(elapsed // 60),
        "signals": len(signals),
        "longs": longs,
        "shorts": shorts,
        "net_pnl_usd": round(app, 4),
        "fees_usd": round(fees, 4),
        "gross_pnl_usd": round(app + fees, 4),
        "trades": int(summary.get("trades", 0) or 0),
        "wins": int(summary.get("wins", 0) or 0),
        "losses": int(summary.get("losses", 0) or 0),
        "open_positions": int(summary.get("open_positions", 0) or 0),
        "win_rate_pct": round(float(summary.get("win_rate_pct", 0) or 0), 2),
        "kill_switch": kill_switch,
        "halt_reason": halt_reason,
        "verdict": verdict,
        "final": final,
        "status": status,
    }


def write_status(payload):
    STATUS_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def checkpoint_message(payload):
    bits = [
        f"{payload['elapsed_min']}m",
        f"signals={payload['signals']}",
        f"L={payload['longs']}",
        f"S={payload['shorts']}",
        f"net=${payload['net_pnl_usd']:.4f}",
        f"fees=${payload['fees_usd']:.4f}",
    ]
    if payload.get("kill_switch") and payload.get("halt_reason"):
        bits.append(payload["halt_reason"])
    if payload.get("verdict"):
        bits.append(payload["verdict"])
    return " | ".join(bits)


def final_verdict(payload):
    if payload.get("kill_switch"):
        return "FAIL - paper halted by drawdown guardrail"
    if payload["signals"] < 5:
        return "NOT ENOUGH SIGNALS"
    if payload["net_pnl_usd"] > 0.5:
        return "PASS - safe to go live"
    if payload["net_pnl_usd"] < -1.0:
        return "FAIL - do NOT go live"
    return "BORDERLINE - monitor longer"


start = time.time()
seen = set()
signals = []
last_report = 0
last_directional_ts = time.time()
last_trade_ts = time.time()
last_trade_count = 0
last_repair_ts = 0
repair_count = 0
last_repair_reasons = []
last_repair_actions = []
signal_re = re.compile(r":\s+(LONG|SHORT)\s+@")
print(now_hms(), "Paper monitor started")
write_status(
    {
        "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "elapsed_min": 0,
        "signals": 0,
        "longs": 0,
        "shorts": 0,
        "net_pnl_usd": 0.0,
        "fees_usd": 0.0,
        "gross_pnl_usd": 0.0,
        "trades": 0,
        "wins": 0,
        "losses": 0,
        "open_positions": 0,
        "win_rate_pct": 0.0,
        "kill_switch": False,
        "halt_reason": "",
        "verdict": "",
        "final": False,
        "status": "starting",
        "repair_count": 0,
        "last_repair_reasons": [],
        "last_repair_actions": [],
    }
)
notify("Alpha Arena Paper Monitor", "Paper monitor started")

final_payload = None

while time.time() - start < RUN_SECONDS:
    try:
        data = fetch()

        for line in data.get("logs", []):
            if line in seen:
                continue
            seen.add(line)
            if signal_re.search(line):
                side = "LONG" if " LONG @" in line else "SHORT"
                signals.append(side)
                last_directional_ts = time.time()
                print(now_hms(), "SIGNAL:", line[:100])

        summary = data.get("paper_summary") or {}
        trade_count = int(summary.get("trades", 0) or 0)
        if trade_count > last_trade_count:
            last_trade_count = trade_count
            last_trade_ts = time.time()

        elapsed = int(time.time() - start)
        reasons = []
        state = data.get("status") or {}
        if not bool(state.get("paper_mode")):
            reasons.append("paper_mode_off")
        selected = selected_models_by_desk(data)
        if not selected.get("btc"):
            reasons.append("btc_unselected")
        if not selected.get("basket"):
            reasons.append("basket_unselected")
        if (time.time() - last_directional_ts) >= STALL_SIGNAL_SECONDS:
            reasons.append("signal_stall")
        if (time.time() - last_trade_ts) >= STALL_TRADE_SECONDS:
            reasons.append("trade_stall")

        if reasons and (time.time() - last_repair_ts) >= REPAIR_COOLDOWN_SECONDS:
            last_repair_reasons = list(reasons)
            last_repair_actions = auto_repair(data, reasons)
            last_repair_ts = time.time()
            repair_count += 1
            repair_msg = (
                f"repair#{repair_count} reasons={','.join(last_repair_reasons)} | "
                f"{' ; '.join(last_repair_actions)}"
            )
            print(now_hms(), "SELF-HEAL:", repair_msg)
            notify("Alpha Arena Self-Heal", repair_msg[:220])

        payload = summarize_payload(data, signals, elapsed)
        payload["repair_count"] = repair_count
        payload["last_repair_reasons"] = last_repair_reasons
        payload["last_repair_actions"] = last_repair_actions
        write_status(payload)

        if payload["kill_switch"]:
            payload["verdict"] = final_verdict(payload)
            payload["final"] = True
            payload["status"] = "halted"
            payload["repair_count"] = repair_count
            payload["last_repair_reasons"] = last_repair_reasons
            payload["last_repair_actions"] = last_repair_actions
            write_status(payload)
            print(now_hms(), "HALT:", checkpoint_message(payload))
            notify("Alpha Arena Paper Halted", checkpoint_message(payload))
            final_payload = payload
            break

        if elapsed - last_report >= CHECKPOINT_SECONDS:
            last_report = elapsed
            print(now_hms(), checkpoint_message(payload))
            notify("Alpha Arena Paper Checkpoint", checkpoint_message(payload))

    except Exception as exc:
        print("ERR:", exc)
        write_status(
            {
                "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
                "status": "error",
                "error": str(exc),
                "final": False,
                "repair_count": repair_count,
                "last_repair_reasons": last_repair_reasons,
                "last_repair_actions": last_repair_actions,
            }
        )
    time.sleep(POLL_SECONDS)

if final_payload is None:
    data = fetch()
    final_payload = summarize_payload(data, signals, int(time.time() - start))
    final_payload["verdict"] = final_verdict(final_payload)
    final_payload["final"] = True
    final_payload["status"] = "completed"
    final_payload["repair_count"] = repair_count
    final_payload["last_repair_reasons"] = last_repair_reasons
    final_payload["last_repair_actions"] = last_repair_actions
    write_status(final_payload)

print("\n=== 90-MIN PAPER REPORT ===")
print(
    f"Signals: {final_payload['signals']} | LONG: {final_payload['longs']} | SHORT: {final_payload['shorts']}"
)
print(f"App fills PnL: ${final_payload['net_pnl_usd']:.4f}")
print(f"Fees: ${final_payload['fees_usd']:.4f}")
print("VERDICT:", final_payload["verdict"])
notify("Alpha Arena Paper Verdict", f"{final_payload['verdict']} | net=${final_payload['net_pnl_usd']:.4f}")
