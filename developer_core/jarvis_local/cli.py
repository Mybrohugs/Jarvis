"""Local CLI for the JARVIS integration block (not a remote tool).

python -m developer_core.jarvis_local.cli --workspace <dir> <command> ...
Interactive console -> Trusted Local Session (keyboard approvals).
Redirected/automated shell -> read-only session (every write is refused).
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import sys
from .llm_planner import understand_smart
from .plan_runner import run_plan
from .router import DEFAULT_V2, DEFAULT_V8, UnifiedTaskRouter
from .session import Denied, TrustedLocalSession


def office_payload(q):
    if q.op == 'docx-append-paragraph':
        return {'text': q.text}
    if q.op == 'xlsx-append-row':
        return {'sheet': q.sheet, 'cells': [c.strip() for c in (q.cells or '').split('|')]}
    return {'sheet': q.sheet, 'cell': q.cell, 'value': q.value}


def parser():
    p = argparse.ArgumentParser(description='JARVIS local integration (local console only)')
    p.add_argument('--workspace', type=Path, required=True)
    p.add_argument('--v8', type=Path, default=DEFAULT_V8)
    p.add_argument('--v2', type=Path, default=DEFAULT_V2)
    p.add_argument('--read-only', action='store_true', help='never ask for approvals')
    sub = p.add_subparsers(dest='command', required=True)
    for name in ('status', 'browser-status', 'browser-selftest', 'windows-check', 'windows-inspect',
                 'windows-test-window', 'windows-close-test-window', 'windows-invoke-test',
                 'notepad-inspect', 'notepad-open', 'notepad-selftest'):
        sub.add_parser(name)
    sub.add_parser('notepad-set-text').add_argument('text')
    s = sub.add_parser('ask', help='команда по-русски: JARVIS строит план и выполняет его')
    s.add_argument('text')
    s.add_argument('--plan-only', action='store_true')
    sub.add_parser('media-status')
    sub.add_parser('media-ps5')
    sub.add_parser('media-scene').add_argument('name', choices=['game', 'movie', 'night'])
    sub.add_parser('media-power').add_argument('state', choices=['on', 'off'])
    sub.add_parser('media-mute').add_argument('state', choices=['on', 'off'])
    for name in ('media-volume', 'media-bass'):
        s = sub.add_parser(name)
        g = s.add_mutually_exclusive_group(required=True)
        g.add_argument('--level', type=int)
        g.add_argument('--delta', type=int)
    sub.add_parser('doctor')
    sub.add_parser('briefing')
    sub.add_parser('briefing-city').add_argument('city')
    sub.add_parser('push-test')
    sub.add_parser('phone-queue')
    s = sub.add_parser('phone-queue-close')
    s.add_argument('item_id')
    s.add_argument('outcome', choices=['done', 'dismissed'])
    sub.add_parser('search').add_argument('pattern')
    sub.add_parser('office-read').add_argument('path')
    for name in ('file-plan', 'file-apply'):
        s = sub.add_parser(name)
        s.add_argument('op', choices=['copy', 'move', 'rename'])
        s.add_argument('src')
        s.add_argument('dst')
        if name == 'file-apply':
            s.add_argument('--approval', required=True)
    for name in ('office-plan', 'office-apply'):
        s = sub.add_parser(name)
        s.add_argument('src')
        s.add_argument('dst')
        s.add_argument('--op', required=True, choices=['docx-append-paragraph', 'xlsx-append-row', 'xlsx-set-cell'])
        s.add_argument('--text')
        s.add_argument('--cells', help='cells separated by |')
        s.add_argument('--cell')
        s.add_argument('--value')
        s.add_argument('--sheet')
        if name == 'office-apply':
            s.add_argument('--approval', required=True)
    sub.add_parser('browser-allow-host').add_argument('host')
    sub.add_parser('browser-read').add_argument('url')
    sub.add_parser('windows-snapshot').add_argument('report')
    s = sub.add_parser('speech', help='текст выступления + презентация (пишет Claude, собирает компьютер)')
    s.add_argument('topic')
    s.add_argument('--minutes', type=int, default=10)
    s.add_argument('--audience', default='')
    s.add_argument('--tone', default='')
    s.add_argument('--notes-file', help='ваши факты и цифры (UTF-8 .txt)')
    s.add_argument('--themes', default='hud,business,creative')
    sub.add_parser('audit').add_argument('--limit', type=int, default=20)
    sub.add_parser('rollback').add_argument('entry_id')
    return p


def kwargs(q):
    c = q.command
    if c in ('file-plan', 'file-apply'):
        kw = {'entries': [{'op': q.op, 'src': q.src, 'dst': q.dst}]}
        return {**kw, 'approval': q.approval} if c == 'file-apply' else kw
    if c in ('office-plan', 'office-apply'):
        kw = {'src': q.src, 'dst': q.dst, 'op': q.op, 'payload': office_payload(q)}
        return {**kw, 'approval': q.approval} if c == 'office-apply' else kw
    return {'search': lambda: {'pattern': q.pattern}, 'office-read': lambda: {'path': q.path},
            'browser-allow-host': lambda: {'host': q.host}, 'notepad-set-text': lambda: {'text': q.text},
            'phone-queue-close': lambda: {'item_id': q.item_id, 'outcome': q.outcome},
            'media-power': lambda: {'on': q.state == 'on'}, 'media-mute': lambda: {'on': q.state == 'on'},
            'media-volume': lambda: {'level': q.level, 'delta': q.delta},
            'media-scene': lambda: {'name': q.name}, 'briefing-city': lambda: {'city': q.city},
            'media-bass': lambda: {'level': q.level, 'delta': q.delta}, 'browser-read': lambda: {'url': q.url},
            'windows-snapshot': lambda: {'report': q.report}, 'audit': lambda: {'limit': q.limit},
            'rollback': lambda: {'entry_id': q.entry_id},
            'speech': lambda: {'topic': q.topic, 'minutes': q.minutes, 'audience': q.audience, 'tone': q.tone,
                               'themes': tuple(t.strip() for t in q.themes.split(',') if t.strip()),
                               'notes': Path(q.notes_file).read_text(encoding='utf-8')[:20000] if q.notes_file else ''},
            }.get(c, dict)()


def open_session(read_only):
    if read_only:
        return TrustedLocalSession.read_only()
    try:
        return TrustedLocalSession.open_interactive()
    except Denied as exc:
        print('NOTE: read-only session (' + str(exc) + ')', file=sys.stderr)
        return TrustedLocalSession.read_only()


def main(argv=None):
    try:
        sys.stdout.reconfigure(errors='backslashreplace')
    except AttributeError:
        pass
    q = parser().parse_args(argv)
    try:
        router = UnifiedTaskRouter(open_session(q.read_only), q.workspace, q.v8, q.v2)
        if q.command == 'ask':
            plan = understand_smart(q.text)
            if plan['intent'] != 'plan' or q.plan_only:
                print(json.dumps(plan, ensure_ascii=False, indent=2))
                return 0 if plan['intent'] == 'plan' else 1
            print('ПЛАН: ' + plan['summary'], file=sys.stderr)
            result = run_plan(router, plan)
        else:
            result = router.dispatch('speech-create' if q.command == 'speech' else q.command, **kwargs(q))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get('status') in ('PASS', 'PLAN_ONLY', 'STARTED', 'CLOSED', 'ALREADY_CLOSED',
                                             'HOST_ALLOWED_DEV', 'WARN') else 1
    except (Denied, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        print('DENIED: ' + type(exc).__name__ + ': ' + str(exc)[:500], file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
