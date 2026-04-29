#!/usr/bin/env python3
"""
Parallel test runner for comparing two competing trading configurations.
Variant A: DeepSeek-R1 at threshold 0.028 (more lenient)
Variant B: Llama-3.2 at threshold 0.035 (faster backend)
"""
import os
import subprocess
import time
import json
import urllib.request
import sys
from pathlib import Path

def wait_for_server(port: int, max_retries: int = 30, delay: float = 1.0) -> bool:
    """Wait for server to be ready on the given port."""
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=2) as resp:
                json.loads(resp.read())
                print(f"✓ Server on port {port} is ready")
                return True
        except Exception:
            pass
        time.sleep(delay)
    print(f"✗ Server on port {port} failed to start")
    return False


def start_server(port: int, basket_model: str, edge_pct: float, name: str) -> subprocess.Popen:
    """Start a trading server with specified configuration."""
    env = os.environ.copy()
    env.update({
        "ALPHA_PORT": str(port),
        "ALPHA_INSECURE_SSL": "1",
        "ALPHA_LIVE_TRADING": "0",
        "ALPHA_PAPER_MODE": "1",
        "ALPHA_AUTO_SELECT_ENABLED": "0",
        "ALPHA_SIGNAL_STRATEGY": "simple_prompt",
        "ALPHA_CANARY_ENABLED": "0",
        "ALPHA_BASE_SIGNAL_CHANCE": "1.0",
        "ALPHA_MIN_PROFIT_EDGE_PCT": "0.03",
        "ALPHA_MIN_PROFIT_EDGE_PCT_BTC": "0.05",
        "ALPHA_MIN_PROFIT_EDGE_PCT_BASKET": str(edge_pct),
        "ALPHA_MIN_TRADE_MOVE_PCT": "0.0",
        "ALPHA_MOMENTUM_OVERRIDE_THRESHOLD_PCT": "0.03",
        "ALPHA_MOMENTUM_OVERRIDE_THRESHOLD_PCT_BTC": "0.04",
        "ALPHA_MOMENTUM_OVERRIDE_THRESHOLD_PCT_BASKET": str(edge_pct),
        "ALPHA_HOLD_STREAK_MOMENTUM_OVERRIDE_ENABLED": "0",
        "ALPHA_PAPER_FORCE_CLOSE_ON_HOLD": "0",
        "ALPHA_DISABLE_BASKET_TIMEOUT_FALLBACK": "1",
        "ALPHA_PAPER_RISK_OFF_MAX_DRAWDOWN_PCT": "0.20",
    })
    
    cmd = ["python3", "quantplot_ai_server.py"]
    proc = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=Path(__file__).parent
    )
    print(f"[{name}] Starting server on port {port} with {basket_model} at {edge_pct*100:.1f}% threshold...")
    return proc


def run_test_harness(port: int, basket_model: str, duration: int, name: str) -> dict:
    """Run the controlled test harness against a server."""
    env = os.environ.copy()
    env.update({
        "ALPHA_CONTROLLED_PORT": str(port),
        "ALPHA_CONTROLLED_DURATION_SECONDS": str(duration),
        "ALPHA_CONTROLLED_POLL_SECONDS": "15",
        "ALPHA_CONTROLLED_ENABLE_BTC": "0",
        "ALPHA_CONTROLLED_ENABLE_BASKET": "1",
        "ALPHA_CONTROLLED_BASKET_MODEL": basket_model,
    })
    
    cmd = ["python3", "run_controlled_paper_session.py"]
    print(f"[{name}] Running {duration}s test session...")
    
    proc = subprocess.run(
        cmd,
        env=env,
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent,
        timeout=duration + 60
    )
    
    output = proc.stdout + proc.stderr
    print(f"[{name}] Test session completed")
    
    # Parse output for DELTA section
    result = {"raw_output": output}
    if "DELTA:" in output:
        lines = output.split('\n')
        for i, line in enumerate(lines):
            if "DELTA:" in line:
                # Collect next ~10 lines
                delta_section = '\n'.join(lines[i:min(i+15, len(lines))])
                result["delta_output"] = delta_section
                break
    
    return result


def main():
    print("="*80)
    print("PARALLEL A/B TEST: DeepSeek-R1 (0.028) vs Llama-3.2 (0.035)")
    print("="*80)
    
    # Kill any existing servers
    os.system("pkill -f quantplot_ai_server.py || true")
    time.sleep(2)
    
    # Start both servers
    print("\n[SETUP] Starting servers...")
    server_a = start_server(port=8001, basket_model="DeepSeek-R1", edge_pct=0.028, name="VARIANT-A")
    time.sleep(2)
    server_b = start_server(port=8002, basket_model="Llama-3.2", edge_pct=0.035, name="VARIANT-B")
    
    # Wait for servers to be ready
    print("\n[SETUP] Waiting for servers to be ready...")
    ready_a = wait_for_server(8001, max_retries=40)
    ready_b = wait_for_server(8002, max_retries=40)
    
    if not (ready_a and ready_b):
        print("\n✗ Failed to start one or both servers")
        server_a.terminate()
        server_b.terminate()
        return
    
    print("\n[TESTS] Both servers ready. Starting parallel 30-minute test sessions...\n")
    
    # Run test harnesses in parallel
    try:
        # Start both tests
        import threading
        result_a = {}
        result_b = {}
        
        def run_a():
            nonlocal result_a
            result_a = run_test_harness(8001, "DeepSeek-R1", 1800, "VARIANT-A")
        
        def run_b():
            nonlocal result_b
            result_b = run_test_harness(8002, "Llama-3.2", 1800, "VARIANT-B")
        
        t_a = threading.Thread(target=run_a)
        t_b = threading.Thread(target=run_b)
        
        t_a.start()
        t_b.start()
        
        t_a.join()
        t_b.join()
        
    finally:
        print("\n[CLEANUP] Stopping servers...")
        server_a.terminate()
        server_b.terminate()
        server_a.wait(timeout=5)
        server_b.wait(timeout=5)
    
    # Display results
    print("\n" + "="*80)
    print("VARIANT A: DeepSeek-R1 at 0.028 threshold")
    print("="*80)
    if "delta_output" in result_a:
        print(result_a["delta_output"])
    else:
        print("[No DELTA output found - showing last 50 lines of raw output]")
        lines = result_a["raw_output"].split('\n')
        print('\n'.join(lines[-50:]))
    
    print("\n" + "="*80)
    print("VARIANT B: Llama-3.2 at 0.035 threshold")
    print("="*80)
    if "delta_output" in result_b:
        print(result_b["delta_output"])
    else:
        print("[No DELTA output found - showing last 50 lines of raw output]")
        lines = result_b["raw_output"].split('\n')
        print('\n'.join(lines[-50:]))
    
    print("\n" + "="*80)
    print("PARALLEL TEST COMPLETE")
    print("="*80)


if __name__ == "__main__":
    main()
