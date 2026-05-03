#!/usr/bin/env python3
"""
Auto-launcher: waits for the current rev-confirmed sweep to finish,
then immediately launches the V3-fix sweep.
"""
import subprocess
import time
import os
from pathlib import Path

WATCH_LOG = "/tmp/sweep_revconf_seq_2h.log"
WATCH_MARKER = "SEQUENTIAL TEST COMPLETE"
POLL_INTERVAL = 30  # seconds between checks
HERE = Path(__file__).parent

print("=== AUTO-LAUNCHER: waiting for rev-confirmed sweep to finish ===")
print(f"Watching: {WATCH_LOG}")
print(f"Will launch: run_v3fix_sweep.py when '{WATCH_MARKER}' appears\n")

while True:
    try:
        text = open(WATCH_LOG).read()
        if WATCH_MARKER in text:
            print(f"[AUTO] Detected sweep complete. Launching V3-fix sweep now...")
            break
    except FileNotFoundError:
        pass

    # Also check if the sweep process has died (no quantplot on ports 8001-8004)
    # by checking if run_parallel_test.py is still running
    result = subprocess.run(
        ["pgrep", "-f", "run_parallel_test.py"],
        capture_output=True, text=True
    )
    if not result.stdout.strip():
        print(f"[AUTO] run_parallel_test.py process gone. Checking log for completion...")
        try:
            text = open(WATCH_LOG).read()
            if "SEQUENTIAL TEST COMPLETE" in text or "[4/4]" in text:
                print("[AUTO] Sweep confirmed done. Launching V3-fix sweep now...")
                break
        except FileNotFoundError:
            pass
        # Wait one more cycle in case it just restarted
        time.sleep(POLL_INTERVAL)
        result2 = subprocess.run(["pgrep", "-f", "run_parallel_test.py"], capture_output=True, text=True)
        if not result2.stdout.strip():
            print("[AUTO] Still gone. Launching V3-fix sweep now...")
            break

    time.sleep(POLL_INTERVAL)

# Small buffer to let servers fully stop
time.sleep(5)

log_out = "/tmp/sweep_v3fix_auto.log"
print(f"[AUTO] Launching V3-fix sweep → {log_out}")
with open(log_out, "w") as log_f:
    proc = subprocess.Popen(
        ["python3", "-u", str(HERE / "run_v3fix_sweep.py")],
        env={**os.environ, "PYTHONUNBUFFERED": "1", "ALPHA_PARALLEL_DURATION": "7200"},
        stdout=log_f,
        stderr=subprocess.STDOUT,
        cwd=HERE,
    )
    print(f"[AUTO] V3-fix sweep launched (pid {proc.pid}). Log: {log_out}")
    proc.wait()
    print(f"[AUTO] V3-fix sweep finished (exit {proc.returncode})")
