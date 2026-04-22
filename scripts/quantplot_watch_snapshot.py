#!/opt/homebrew/bin/python3
import json
import os
import time
import urllib.request
from datetime import datetime
from pathlib import Path

BASE_DIR = Path('/Users/chetantemkar/development/alphaarena')
LOG_DIR = BASE_DIR / 'logs'
OUT_FILE = LOG_DIR / 'quantplot_watch_48h.jsonl'
MAX_SAMPLES = 48


def _read_existing_samples(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        with path.open('r', encoding='utf-8') as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def _fetch_state() -> dict:
    with urllib.request.urlopen('http://127.0.0.1:8000/api/state', timeout=8) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _snapshot(state: dict) -> dict:
    status = state.get('status', {}) or {}
    desk_pnl = state.get('desk_pnl', {}) or {}
    models = state.get('models', {}) or {}
    selected_btc = 0
    selected_basket = 0
    for m in models.values():
        ds = m.get('desk_state', {}) if isinstance(m, dict) else {}
        if isinstance(ds.get('btc'), dict) and ds['btc'].get('selected'):
            selected_btc += 1
        if isinstance(ds.get('basket'), dict) and ds['basket'].get('selected'):
            selected_basket += 1
    return {
        'ts': datetime.now().isoformat(),
        'app_total_pnl_usd': float(state.get('app_total_pnl_usd', 0.0) or 0.0),
        'desk_pnl_btc_usd': float(desk_pnl.get('btc', 0.0) or 0.0),
        'desk_pnl_basket_usd': float(desk_pnl.get('basket', 0.0) or 0.0),
        'selected_btc_models': selected_btc,
        'selected_basket_models': selected_basket,
        'mode': status.get('mode', ''),
        'feed': status.get('feed', ''),
        'auto_select_top_n': int(status.get('auto_select_top_n', 0) or 0),
        'live_blocked': bool(status.get('live_blocked', False)),
        'live_blocked_reason': status.get('live_blocked_reason', ''),
    }


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if _read_existing_samples(OUT_FILE) >= MAX_SAMPLES:
        return 0
    rec = {'ts': datetime.now().isoformat(), 'ok': False}
    try:
        state = _fetch_state()
        rec.update(_snapshot(state))
        rec['ok'] = True
    except Exception as exc:  # noqa: BLE001
        rec['error'] = str(exc)
    with OUT_FILE.open('a', encoding='utf-8') as fh:
        fh.write(json.dumps(rec, separators=(',', ':')) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
