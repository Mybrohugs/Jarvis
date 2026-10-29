"""Explorer operator on temporary stand-ins for Downloads/Documents (never the real folders)."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from developer_core.jarvis_local.explorer import Explorer
from developer_core.jarvis_local.intent import understand
from developer_core.jarvis_local.plan_runner import run_plan
from developer_core.jarvis_local.router import UnifiedTaskRouter
from developer_core.jarvis_local.session import Denied, TrustedLocalSession


class ExplorerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        self.dl, self.docs, ws = base / 'Downloads', base / 'Documents', base / 'ws'
        for d in (self.dl, self.docs, ws):
            d.mkdir()
        self.approve = True
        self.asked = []

        def confirmer(summary, challenge):
            self.asked.append(summary)
            return self.approve
        self.router = UnifiedTaskRouter(TrustedLocalSession.for_tests(confirmer), ws)
        self.router._explorer = Explorer(self.router.ws, roots={'Downloads': self.dl, 'Documents': self.docs},
                                         now=lambda: time.time() + 3600)

    def put(self, folder, name, data=b'x'):
        p = folder / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def plan(self, phrase):
        return understand(phrase)['steps']

    def test_russian_phrases(self):
        self.assertEqual(self.plan('разложи загрузки по типам'),
                         [{'task': 'explorer-sort', 'args': {'folder': 'Downloads'}}])
        self.assertEqual(self.plan('Джарвис, наведи порядок в загрузках')[0]['task'], 'explorer-sort')
        self.assertEqual(self.plan('что лежит в документах')[0], {'task': 'explorer-list', 'args': {'folder': 'Documents'}})
        self.assertEqual(self.plan('найди дубликаты в загрузках')[0]['task'], 'explorer-duplicates')
        self.assertEqual(self.plan('создай папку «Счета 2026» в документах')[0],
                         {'task': 'explorer-mkdir', 'args': {'folder': 'Documents', 'name': 'Счета 2026'}})
        self.assertEqual(self.plan('скопируй отчёт.pdf из загрузок в документы')[0],
                         {'task': 'explorer-copy', 'args': {'src': 'Downloads', 'name': 'отчёт.pdf', 'dst': 'Documents'}})
        self.assertEqual(self.plan('перенеси «Договор» из загрузок в документы')[0]['task'], 'explorer-move')
        steps = self.plan('разложи загрузки по типам и покажи, что получилось')
        self.assertEqual([s['task'] for s in steps], ['explorer-sort', 'explorer-list'])
        self.assertEqual(steps[1]['args']['folder'], 'Downloads')
        self.assertEqual(understand('удали дубликаты в загрузках')['intent'], 'refused')
        self.assertEqual(self.plan('открой папку загрузки')[0]['task'], 'app-open')  # unchanged
        self.assertEqual(self.plan('добавь строку «1;2» в таблицу data.xlsx')[0]['task'], 'office-plan')  # unchanged

    def test_sort_moves_by_type_never_overwrites_and_rolls_back(self):
        self.put(self.dl, 'отчёт.pdf', b'pdf-new')
        self.put(self.dl, 'фото.JPG', b'jpg')
        self.put(self.dl, 'таблица.xlsx', b'xlsx')
        self.put(self.dl, 'странное.xyz', b'?')
        self.put(self.dl, 'качается.crdownload', b'..')
        self.put(self.dl, 'ярлык.lnk', b'lnk')
        old = self.put(self.dl, 'PDF/отчёт.pdf', b'pdf-old')  # existing folder + same name
        fresh = self.put(self.dl, 'свежий.docx', b'new')
        os.utime(fresh, (time.time() + 7200, time.time() + 7200))  # "changed 0 s ago" for the operator clock
        out = self.router.dispatch('explorer-sort', folder='Downloads')
        self.assertEqual(out['status'], 'PASS')
        self.assertEqual(out['moved'], 4)
        self.assertEqual(old.read_bytes(), b'pdf-old')  # untouched
        self.assertEqual((self.dl / 'PDF' / 'отчёт (2).pdf').read_bytes(), b'pdf-new')
        self.assertTrue((self.dl / 'Картинки' / 'фото.JPG').is_file())
        self.assertTrue((self.dl / 'Таблицы' / 'таблица.xlsx').is_file())
        self.assertTrue((self.dl / 'Прочее' / 'странное.xyz').is_file())
        for skipped in ('качается.crdownload', 'ярлык.lnk', 'свежий.docx'):
            self.assertTrue((self.dl / skipped).is_file(), skipped)
        self.assertEqual(self.asked[-1]['files'], 4)
        undo = self.router.dispatch('rollback', entry_id=out['audit_id'])
        self.assertEqual(undo['status'], 'PASS')
        self.assertEqual((self.dl / 'отчёт.pdf').read_bytes(), b'pdf-new')
        self.assertTrue((self.dl / 'фото.JPG').is_file())
        self.assertFalse((self.dl / 'Картинки').exists())  # created by JARVIS, empty again -> removed
        self.assertTrue((self.dl / 'PDF').is_dir())  # existed before -> kept
        with self.assertRaises(Denied):
            self.router.dispatch('rollback', entry_id=out['audit_id'])

    def test_rollback_refuses_when_file_changed(self):
        self.put(self.dl, 'a.txt', b'one')
        self.put(self.dl, 'b.png', b'two')
        out = self.router.dispatch('explorer-sort', folder='Downloads')
        (self.dl / 'Документы' / 'a.txt').write_bytes(b'edited by the owner')
        with self.assertRaises(Denied):
            self.router.dispatch('rollback', entry_id=out['audit_id'])
        self.assertTrue((self.dl / 'Картинки' / 'b.png').is_file())  # nothing half-undone

    def test_declined_changes_nothing(self):
        self.put(self.dl, 'a.txt')
        self.approve = False
        with self.assertRaises(Denied):
            self.router.dispatch('explorer-sort', folder='Downloads')
        self.assertTrue((self.dl / 'a.txt').is_file())
        self.assertEqual(self.router.ws.journal.entries()[-1]['status'], 'DENIED')

    def test_duplicates_are_found_not_deleted(self):
        self.put(self.dl, 'a.bin', b'same' * 100)
        self.put(self.dl, 'a (1).bin', b'same' * 100)
        self.put(self.dl, 'b.bin', b'diff' * 100)
        out = self.router.dispatch('explorer-duplicates', folder='Downloads')
        self.assertEqual(len(out['groups']), 1)
        self.assertEqual(sorted(out['groups'][0]['files']), ['a (1).bin', 'a.bin'])
        self.assertEqual(out['wasted_bytes'], 400)
        self.assertEqual(len(list(self.dl.iterdir())), 3)

    def test_copy_and_move_with_rollback(self):
        self.put(self.dl, 'Договор аренды.docx', b'doc')
        out = self.router.dispatch('explorer-copy', src='Downloads', name='договор', dst='Documents')
        self.assertEqual((self.docs / 'Договор аренды.docx').read_bytes(), b'doc')
        self.router.dispatch('rollback', entry_id=out['audit_id'])
        self.assertFalse((self.docs / 'Договор аренды.docx').exists())
        self.assertTrue(any(self.router.ws.state.joinpath('quarantine').rglob('Договор аренды.docx')))  # not deleted
        out = self.router.dispatch('explorer-move', src='Downloads', name='Договор аренды.docx', dst='Documents')
        self.assertFalse((self.dl / 'Договор аренды.docx').exists())
        self.router.dispatch('rollback', entry_id=out['audit_id'])
        self.assertEqual((self.dl / 'Договор аренды.docx').read_bytes(), b'doc')
        self.put(self.docs, 'x.txt', b'old')
        self.put(self.dl, 'x.txt', b'new')
        out = self.router.dispatch('explorer-copy', src='Downloads', name='x.txt', dst='Documents')
        self.assertEqual(((self.docs / 'x.txt').read_bytes(), (self.docs / 'x (2).txt').read_bytes()), (b'old', b'new'))

    def test_paths_stay_inside_allowed_folders(self):
        for bad in ('..\\evil.txt', 'a/../../b', 'C:\\Windows\\x', 'con<>'):
            with self.assertRaises(Denied):
                self.router.dispatch('explorer-mkdir', folder='Downloads', name=bad)
        with self.assertRaises(Denied):
            self.router.dispatch('explorer-list', folder='Windows')
        with self.assertRaises(Denied):
            self.router.dispatch('explorer-mkdir', folder='Videos', name='x')  # not in this allow-list

    def test_multi_step_plan_and_undo_last(self):
        self.put(self.dl, 'a.mp3')
        self.put(self.dl, 'b.zip')
        result = run_plan(self.router, understand('разложи загрузки по типам и покажи, что получилось'))
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(len(result['undo_available']), 1)
        self.assertEqual(sorted(result['completed'][1]['result']['folders']), ['Архивы', 'Музыка'])
        run_plan(self.router, understand('отмени последнее действие'))
        self.assertEqual(sorted(p.name for p in self.dl.iterdir()), ['a.mp3', 'b.zip'])


if __name__ == '__main__':
    unittest.main()
