"""Executes an understood plan step by step through the Unified Task Router.

Every write still goes plan -> approval hash -> local confirmation -> verify -> audit.
The runner stops at the first failure and reports which completed steps can be undone.
"""
from __future__ import annotations
from .intent import validate_plan
from .session import Denied


def _last_reversible(router):
    journal = router.ws.journal
    for entry in reversed(journal.entries()):
        if entry.get('status') == 'PASS' and entry.get('rollback') and not journal.rolled_back(entry['id']) \
                and entry.get('task') != 'rollback':
            return entry
    return None


def _run_step(router, step):
    task, args = step['task'], step['args']
    if task == 'file-plan':
        plan = router.dispatch('file-plan', **args)
        return router.dispatch('file-apply', entries=args['entries'], approval=plan['approval'])
    if task == 'office-plan':
        plan = router.dispatch('office-plan', **args)
        return router.dispatch('office-apply', **args, approval=plan['approval'])
    if task == 'notepad-open-if-needed':
        try:
            found = router.dispatch('notepad-inspect')
        except Denied:
            found = {'status': 'FAIL'}
        return found if found.get('status') == 'PASS' else router.dispatch('notepad-open')
    if task == 'rollback-last':
        entry = _last_reversible(router)
        if entry is None:
            return {'status': 'PASS', 'note': 'Нечего отменять: обратимых действий нет'}
        return router.dispatch('rollback', entry_id=entry['id'])
    return router.dispatch(task, **args)


def run_plan(router, plan):
    validate_plan(plan)
    done, undo = [], []
    for i, step in enumerate(plan['steps']):
        try:
            result = _run_step(router, step)
        except (Denied, OSError, ValueError, KeyError) as exc:
            return {'status': 'STOPPED', 'failed_step': i + 1, 'task': step['task'],
                    'reason': str(exc)[:300], 'completed': done, 'undo_available': undo,
                    'hint': ('Отменить выполненное: rollback <id> для каждого id из undo_available'
                             if undo else 'Изменений не было')}
        ok = result.get('status') in ('PASS', 'PLAN_ONLY', 'STARTED', 'CLOSED', 'ALREADY_CLOSED', 'HOST_ALLOWED_DEV', 'REQUESTED')
        done.append({'step': i + 1, 'task': step['task'], 'status': result.get('status'), 'result': result})
        if result.get('audit_id') and (step['task'] in ('file-plan', 'office-plan') or step['task'].startswith('explorer-')):
            undo.append(result['audit_id'])
        if not ok:
            return {'status': 'STOPPED', 'failed_step': i + 1, 'task': step['task'],
                    'reason': 'step status ' + str(result.get('status')), 'completed': done, 'undo_available': undo}
    return {'status': 'PASS', 'completed': done, 'undo_available': undo}
