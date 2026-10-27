"""Browser Agent: reuses the existing verified BrowserSnapshot (Playwright, offline
sanitized Chromium, Job Object limits) and the existing allowlisted HTTPS client.

State (host allowlist, reports) lives in its own DEV root under the workspace state
folder, separate from any Workbench root. No interactive page control, forms,
credentials or user browser profiles.
"""
from __future__ import annotations
from pathlib import Path
from .session import Denied


class _NoCore(Exception):
    """Stand-in until the optional engine is loaded."""

SELFTEST_HTML = ('<html><body><h1>JARVIS local browser selftest</h1><p id="x">offline-render-ok</p>'
                 '<script>document.body.innerHTML="SCRIPT-RAN"</script></body></html>')


class BrowserAgent:
    def __init__(self, ws):
        self.ws = ws
        self._snapshot = None
        self._core_denied = _NoCore

    def _engine(self):
        if self._snapshot is None:
            try:  # optional: BrowserSnapshot and its store come from the full JARVIS DEV tree
                from ..browser_snapshot import BrowserSnapshot
                from ..core import Core, Denied as CoreDenied
                from ..workbench_network import Network
                from ..workbench_storage import Store
            except ImportError:
                raise Denied('Browser engine (developer_core.browser_snapshot) is not installed') from None
            self._core_denied = CoreDenied
            try:
                store = Store(Core(Path(self.ws.state) / 'browser'))
            except CoreDenied as exc:
                raise Denied(str(exc)) from None
            self._snapshot = BrowserSnapshot(store, Network(store))
        return self._snapshot

    def _wrap(self, fn, *args):
        try:
            return fn(*args)
        except self._core_denied as exc:
            raise Denied(str(exc)) from None

    def status(self):
        engine = self._engine()
        status = engine.status()
        hosts = [r['host'] for r in engine.store.rows('SELECT host FROM hosts ORDER BY host')]
        return {**status, 'allowed_hosts': hosts, 'interactive_control': False}

    def selftest(self):
        """Real Chromium launch on fixed HTML, offline; proves rendering and that JS is off."""
        result = self._wrap(self._engine().render, SELFTEST_HTML)
        if result.get('status') != 'PASS':
            return {'status': result.get('status', 'FAIL'), 'launch_verified': False, **result}
        text = result.get('text', '')
        ok = 'offline-render-ok' in text and 'SCRIPT-RAN' not in text
        return {'status': 'PASS' if ok else 'FAIL', 'launch_verified': ok, 'javascript_executed': 'SCRIPT-RAN' in text,
                'png_bytes': len(result.get('png', '')) * 3 // 4}

    def allow_host(self, host, session):
        session.confirm({'task': 'browser-allow-host', 'host': host, 'scope': 'public HTTPS read-only snapshots'})
        result = self._wrap(self._engine().network.allow_host, host)
        self.ws.journal.record('browser-allow-host', 'PASS', host=result['host'], rollback=[],
                               approval_mode=session.approval_mode)
        return result

    def read(self, url):
        result = self._wrap(self._engine().snapshot, url)
        self.ws.journal.record('browser-read', result.get('status', 'FAIL'), url=url,
                               source_sha256=result.get('source_sha256'), report=result.get('report'))
        return result
