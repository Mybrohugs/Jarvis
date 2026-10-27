"""Trusted Local Session: bound to a live local console and to this process.

It cannot be created from redirected stdio (Telegram/MCP/mobile workers, pipes,
CI) and cannot be pickled or reused by another process. Every write needs a fresh
challenge typed on the local keyboard.
"""
from __future__ import annotations
import json
import os
import secrets
import sys
import time


class Denied(RuntimeError):
    pass


REMOTE_MARKERS = ('SSH_CONNECTION', 'SSH_CLIENT', 'SSH_TTY', 'JARVIS_REMOTE_ORIGIN')
SESSION_SECONDS = 15 * 60


class TrustedLocalSession:
    def __init__(self, *, confirmer, approval_mode, interactive, ttl=SESSION_SECONDS):
        self._confirmer = confirmer
        self.approval_mode = approval_mode
        self.interactive = interactive
        self.pid = os.getpid()
        self.expires = time.monotonic() + ttl
        self.session_id = secrets.token_hex(8)

    @classmethod
    def open_interactive(cls, stdin=None, stdout=None, environ=None):
        stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
        environ = os.environ if environ is None else environ
        marker = next((name for name in REMOTE_MARKERS if environ.get(name)), None)
        if marker:
            raise Denied('Remote session marker present (' + marker + '); local console required')
        try:
            local = stdin.isatty() and stdout.isatty()
        except (AttributeError, ValueError):
            local = False
        if not local:
            raise Denied('redirected console: Trusted Local Session needs an interactive local console')
        return cls(confirmer=cls._keyboard, approval_mode='local-keyboard', interactive=True)

    @classmethod
    def read_only(cls):
        """For redirected/automated shells: reads work, every approval is refused."""
        return cls(confirmer=lambda summary, challenge: False, approval_mode='read-only', interactive=False)

    @classmethod
    def phone_allowlist(cls, tasks):
        """User-approved boundary (27.10.2026): the phone may run ONLY these low-risk tasks.

        Any other approval request (file/office writes, UI actions, rollback...) is refused.
        """
        allowed = frozenset(tasks)
        return cls(confirmer=lambda summary, challenge: summary.get('task') in allowed,
                   approval_mode='phone-allowlist', interactive=False, ttl=365 * 24 * 3600)

    @classmethod
    def for_tests(cls, confirmer, ttl=SESSION_SECONDS):
        """Automated tests only; audit records approval_mode='test-auto', never keyboard."""
        return cls(confirmer=confirmer, approval_mode='test-auto', interactive=False, ttl=ttl)

    @staticmethod
    def _keyboard(summary, challenge):
        print('=== JARVIS LOCAL APPROVAL ===')
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print('Type ' + challenge + ' to approve (anything else cancels): ', end='', flush=True)
        try:
            return input().strip() == challenge
        except EOFError:
            return False

    def check(self):
        if os.getpid() != self.pid:
            raise Denied('Session belongs to another process')
        if time.monotonic() > self.expires:
            raise Denied('Trusted Local Session expired; start a new local session')

    def confirm(self, summary):
        self.check()
        if self.approval_mode == 'read-only':
            raise Denied('Read-only session: run this command in an interactive local console to approve')
        challenge = secrets.token_hex(3).upper()
        if not self._confirmer(summary, challenge):
            raise Denied('Not approved by local user')
        return {'approval_mode': self.approval_mode, 'session_id': self.session_id}

    def __reduce__(self):
        raise Denied('Trusted Local Session cannot be serialized or transferred')
