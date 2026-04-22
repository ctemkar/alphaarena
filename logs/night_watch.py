#!/usr/bin/env python3
import json
import time
import urllib.request
import urllib.error
from datetime import datetime

BASE = 'http://127.0.0.1:8000'
OUT_JSONL = 'logs/night_watch.jsonl'
OUT_TXT = 'logs/night_watch.alerts.log'
INTERVAL_SECONDS = 30


def now_iso():
    return datetime.now().isoformat(timespec='seconds')


def fetch_state():
    with urllib.request.urlopen(BASE + '/api/state', timeout=10) as r:
        return json.loads(r.read().decode())


def append_jsonl(path, rec):
    with open(path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, separators=(',', ':')) + '\n')


def append_text(path, line):
    with open(path, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


last_alert = {
    'kill_switch': None,
    'live_blocked': None,
    'pause_all': None,
    'core_mode': None,
}

append_text(OUT_TXT, f"[{now_iso()}] night watch started; interval={INTERVAL_SECONDS}s")

while True:
    ts = now_iso()
    try:
        s = fetch_state()
        st = s.get('status') or {}
        core = st.get('execution_core') or {}
        pnl = s.get('binance_pnl') or {}

        rec = {
            'ts': ts,
            'mode': st.get('mode'),
            'feed': st.get('feed'),
            'core_mode': core.get('mode'),
            'pause_all': st.get('pause_all_desks'),
            'pause_btc': st.get('pause_btc'),
            'pause_basket': st.get('pause_basket'),
            'kill_switch': st.get('kill_switch'),
            'live_blocked': st.get('live_blocked'),
            'order_queue': st.get('order_queue'),
            'equity_delta_usd': pnl.get('equity_delta_usd'),
            'unrealized_usd': pnl.get('unrealized_usd'),
        }
        append_jsonl(OUT_JSONL, rec)

        checks = {
            'kill_switch': rec['kill_switch'],
            'live_blocked': rec['live_blocked'],
            'pause_all': rec['pause_all'],
            'core_mode': rec['core_mode'],
        }

        for k, v in checks.items():
            if last_alert[k] is None:
                last_alert[k] = v
            elif last_alert[k] != v:
                append_text(OUT_TXT, f"[{ts}] state change: {k} {last_alert[k]} -> {v}")
                last_alert[k] = v

        if rec['kill_switch']:
            append_text(OUT_TXT, f"[{ts}] ALERT: kill_switch active")
        if rec['live_blocked']:
            append_text(OUT_TXT, f"[{ts}] ALERT: live_blocked active")
        if rec['mode'] != 'LIVE':
            append_text(OUT_TXT, f"[{ts}] WARN: mode={rec['mode']}")
        if rec['feed'] != 'LIVE':
            append_text(OUT_TXT, f"[{ts}] WARN: feed={rec['feed']}")
        if rec['core_mode'] != 'cutover':
            append_text(OUT_TXT, f"[{ts}] WARN: core_mode={rec['core_mode']}")

    except urllib.error.URLError as e:
        append_text(OUT_TXT, f"[{ts}] ERROR: cannot reach API ({e})")
    except Exception as e:
        append_text(OUT_TXT, f"[{ts}] ERROR: monitor failure ({e})")

    time.sleep(INTERVAL_SECONDS)
