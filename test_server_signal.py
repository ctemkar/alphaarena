#!/usr/bin/env python3
import subprocess, time, os, urllib.request, json

os.system('lsof -ti:8001 | xargs kill -9 2>/dev/null; sleep 2')
env = os.environ.copy()
env.update({
    'ALPHA_PORT': '8001',
    'ALPHA_DESK': 'btc',
    'ALPHA_MODEL': 'Llama-3.2',
    'ALPHA_SIZE_USD': '1000',
    'ALPHA_MOMENTUM_THRESHOLD': '0.010',
    'ALPHA_SIGNAL_STRATEGY': 'deterministic_reversal',
})

proc = subprocess.Popen(['/opt/homebrew/bin/python3', 'quantplot_ai_server.py'], env=env, 
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(6)

try:
    state = json.loads(urllib.request.urlopen('http://localhost:8001/api/state', timeout=5).read())
    print('✓ Server running')
    print('Mode:', state.get('status', {}).get('mode'))
    models = state.get('models', {})
    if 'Llama-3.2' in models:
        llama = models['Llama-3.2']
        desk = llama.get('desk_state', {}).get('btc', {})
        print('Llama-3.2 BTC signal:', desk.get('last_signal'))
        print('Llama-3.2 momentum_threshold env:', os.getenv('ALPHA_MOMENTUM_THRESHOLD'))
except Exception as e:
    print(f'✗ Error: {e}')

proc.terminate()
proc.wait()
os.system('lsof -ti:8001 | xargs kill -9 2>/dev/null')
print('Cleaned up')
