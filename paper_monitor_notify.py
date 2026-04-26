import json
import subprocess
from datetime import datetime
from pathlib import Path

STATUS_PATH = Path(__file__).with_name("paper_monitor_status.json")


def notify(title: str, message: str) -> None:
    script = f"display notification {json.dumps(message)} with title {json.dumps(title)}"
    subprocess.run(["osascript", "-e", script], check=False, capture_output=True)


def main() -> int:
    if not STATUS_PATH.exists():
        notify("Alpha Arena Paper Monitor", "No paper monitor status file found yet.")
        return 0
    try:
        payload = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        notify("Alpha Arena Paper Monitor", f"Could not read status: {exc}")
        return 1

    updated = payload.get("updated_at", datetime.now().isoformat(timespec="seconds"))
    elapsed = int(payload.get("elapsed_min", 0) or 0)
    signals = int(payload.get("signals", 0) or 0)
    longs = int(payload.get("longs", 0) or 0)
    shorts = int(payload.get("shorts", 0) or 0)
    net = float(payload.get("net_pnl_usd", 0) or 0)
    fees = float(payload.get("fees_usd", 0) or 0)
    verdict = str(payload.get("verdict", "") or "").strip()
    halt_reason = str(payload.get("halt_reason", "") or "").strip()
    status = str(payload.get("status", "running") or "running")

    pieces = [
        f"{elapsed}m",
        f"signals={signals}",
        f"L={longs}",
        f"S={shorts}",
        f"net=${net:.4f}",
        f"fees=${fees:.4f}",
    ]
    if halt_reason:
        pieces.append(halt_reason)
    if verdict:
        pieces.append(verdict)
    pieces.append(f"updated {updated}")

    title = "Alpha Arena Paper Update"
    if status == "halted":
        title = "Alpha Arena Paper Halted"
    elif status == "completed":
        title = "Alpha Arena Paper Verdict"

    notify(title, " | ".join(pieces))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
