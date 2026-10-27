"""Send a written report to the OWNER's Telegram chat through the existing JARVIS bot.

Uses the bot's own DPAPI config loader (Runtime.load_config) in memory only: the token is never
printed, logged or returned, and the message goes only to the saved owner id. Text only.
usage: python -m developer_core.jarvis_local.telegram_report --file report.txt
"""
from __future__ import annotations
import argparse
from pathlib import Path
import sys

DEV_ROOT = Path(__file__).resolve().parents[2]
TELEGRAM_ROOT = DEV_ROOT / '.telegram-dev'
LIMIT = 3900  # Telegram message limit is 4096 characters


def chunks(text, size=LIMIT):
    parts, cur = [], ''
    for para in text.split('\n'):
        if len(cur) + len(para) + 1 > size and cur:
            parts.append(cur)
            cur = ''
        cur += para + '\n'
    if cur.strip():
        parts.append(cur)
    return [p[:size] for p in parts]


def send(text, root=TELEGRAM_ROOT):
    from ..core import Core
    from ..telegram_bridge import TelegramBridge
    from ..telegram_runtime import Runtime
    runtime = Runtime(Core(root).root)
    token, owner = runtime.load_config()
    bridge = TelegramBridge(None, token, owner)
    sent = 0
    for part in chunks(text):
        bridge.api('sendMessage', chat_id=owner, text=part, disable_web_page_preview=True)
        sent += 1
    return {'status': 'PASS', 'messages': sent}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--file', type=Path, required=True)
    a = ap.parse_args()
    try:
        print(send(a.file.read_text(encoding='utf-8')))
        return 0
    except Exception as exc:  # constant-ish diagnostics only, never the token
        print('TELEGRAM_REPORT_FAILED:', type(exc).__name__, getattr(exc, 'code', ''), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
