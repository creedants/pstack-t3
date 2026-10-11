"""Behavior checks for the bundled peer mesh."""
import os
from pathlib import Path
import sys

MESH_SCRIPTS = Path(__file__).resolve().parents[1] / 't3/added/mesh/scripts'
sys.path.insert(0, str(MESH_SCRIPTS))
MESH_ENV = {**os.environ, 'PYTHONPATH': str(MESH_SCRIPTS) + os.pathsep + os.environ.get('PYTHONPATH', '')}

import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from pstack_mesh.board import Board
from pstack_mesh.landing import Landing, git, repository_lock


class LandingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.capacity = patch.dict(os.environ, {'PSTACK_MESH_CAPACITY_STATE': str(self.root / 'capacity')})
        self.capacity.start()
        self.addCleanup(self.capacity.stop)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        git(self.repo, 'init', '-b', 'main')
        git(self.repo, 'config', 'user.name', 'Mesh test')
        git(self.repo, 'config', 'user.email', 'mesh@example.invalid')
        (self.repo / '.gitignore').write_text('__pycache__/\n')
        (self.repo / 'a.txt').write_text('baseline\n')
        (self.repo / 'b.txt').write_text('baseline\n')
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-m', 'baseline')
        self.baseline = git(self.repo, 'rev-parse', 'HEAD')
        self.board = Board(self.root / 'board.sqlite')
        self.a = self.root / 'a'
        self.b = self.root / 'b'
        git(self.repo, 'worktree', 'add', '-b', 'author-a', str(self.a))
        git(self.repo, 'worktree', 'add', '-b', 'author-b', str(self.b))
        self.board.register('a', 'thread-a', self.a)
        self.board.register('b', 'thread-b', self.b)
        self.board.add('ta', 'Task a', 'a.txt')
        self.board.add('tb', 'Task b', 'b.txt')
        self.board.claim('a', task_id='ta')
        self.board.claim('b', task_id='tb')
        self.command = [sys.executable, '-c', "from pathlib import Path; assert Path('a.txt').exists(); print('candidate checked')"]
        self.config = dict(repository=str(self.repo), baseline=self.baseline,
                           integration_worktree=str(self.root / 'integration'),
                           integration_branch='integration', target_branch='main',
                           check_command=self.command,
                           task_scopes={'ta': ['a.txt'], 'tb': ['b.txt']})
        with self.board.transaction() as db:
            db.execute('CREATE TABLE run_config(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            db.executemany('INSERT INTO run_config VALUES(?,?)',
                           [(k, json.dumps(v)) for k, v in self.config.items()])
        self.landing = Landing(self.board.path, self.repo)

    def commit(self, workspace, file, content):
        (workspace / file).write_text(content)
        git(workspace, 'add', file)
        git(workspace, 'commit', '-m', content.strip())
        return git(workspace, 'rev-parse', 'HEAD')

    def approved_a(self):
        commit = self.commit(self.a, 'a.txt', 'author a\n')
        self.landing.submit('ta', 'a', commit)
        self.landing.review('ta', 'b', commit, 'approve', {'command': 'review tests', 'result': 'PASS'})
        return commit

    def test_review_and_checks_gate_real_main_tree(self):
        commit = self.commit(self.a, 'a.txt', 'author a\n')
        item = self.landing.submit('ta', 'a', commit)
        self.assertEqual(self.landing.land()['status'], 'awaiting_review')
        self.assertEqual(git(self.repo, 'rev-parse', 'HEAD'), self.baseline)
        with self.assertRaisesRegex(ValueError, 'own submission'):
            self.landing.review('ta', 'a', commit, 'approve', 'self')
        self.landing.review('ta', 'b', commit, 'approve', 'Read exact SHA and tested')
        result = self.landing.land(self.command)
        self.assertEqual(result['status'], 'landed')
        self.assertEqual(result['submission_id'], item['id'])
        self.assertEqual((self.repo / 'a.txt').read_text(), 'author a\n')
        self.assertEqual(git(self.repo, 'show', '-s', '--format=%P', result['candidate']), self.baseline)
        evidence = json.loads(self.landing.status()[0]['evidence'])
        self.assertEqual(evidence['returncode'], 0)
        self.assertEqual(evidence['stdout'], 'candidate checked\n')
        self.assertEqual(evidence['reviews'][0]['commit_sha'], commit)
        self.assertEqual(self.landing.land(), {'status': 'idle'})

    def test_order_is_submission_id_and_head_of_line_review_blocks(self):
        ca = self.commit(self.a, 'a.txt', 'a\n')
        cb = self.commit(self.b, 'b.txt', 'b\n')
        self.landing.submit('ta', 'a', ca)
        self.landing.submit('tb', 'b', cb)
        self.landing.review('tb', 'a', cb, 'approve', 'Reviewed b')
        self.assertEqual(self.landing.land(), {'status': 'awaiting_review', 'submission_id': 1})
        self.landing.review('ta', 'b', ca, 'approve', 'Reviewed a')
        first = self.landing.land()
        second = self.landing.land()
        self.assertEqual([first['submission_id'], second['submission_id']], [1, 2])
        self.assertEqual(git(self.repo, 'show', '-s', '--format=%P', second['candidate']), first['candidate'])
        self.assertEqual((self.repo / 'a.txt').read_text(), 'a\n')
        self.assertEqual((self.repo / 'b.txt').read_text(), 'b\n')

    def test_failed_check_does_not_advance_and_retry_is_bounded(self):
        self.approved_a()
        command = [sys.executable, '-c', 'raise SystemExit(7)']
        with self.board.transaction() as db:
            db.execute('UPDATE run_config SET value=? WHERE key=?', (json.dumps(command), 'check_command'))
        landing = Landing(self.board.path, self.repo)
        for attempt in range(3):
            result = landing.land(command)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['evidence']['returncode'], 7)
            self.assertEqual(git(self.repo, 'rev-parse', 'HEAD'), self.baseline)
            item = landing.status()[0]
            landing.submit('ta', 'a', item['commit_sha'])
        self.assertEqual(landing.status()[0]['attempts'], 3)
        self.assertEqual(landing.land(), {'status': 'idle'})

    def test_wrong_command_dirty_checkout_and_repository_are_rejected(self):
        self.approved_a()
        with self.assertRaisesRegex(ValueError, 'differs'):
            self.landing.land([sys.executable, '-c', 'print(1)'])
        (self.repo / 'untracked.txt').write_text('human work')
        with self.assertRaisesRegex(ValueError, 'dirty'):
            self.landing.land()
        self.assertEqual((self.repo / 'untracked.txt').read_text(), 'human work')
        with self.assertRaisesRegex(ValueError, 'does not match'):
            Landing(self.board.path, self.a)

    def test_amendment_invalidates_exact_sha_review(self):
        old = self.approved_a()
        (self.a / 'a.txt').write_text('amended\n')
        git(self.a, 'add', 'a.txt')
        git(self.a, 'commit', '--amend', '-m', 'amended')
        new = git(self.a, 'rev-parse', 'HEAD')
        self.landing.submit('ta', 'a', new)
        self.assertEqual(self.landing.status()[0]['status'], 'superseded')
        self.assertEqual(self.landing.land()['status'], 'awaiting_review')
        with self.assertRaisesRegex(ValueError, 'No active'):
            self.landing.review('ta', 'b', old, 'approve', 'stale')
        self.landing.review('ta', 'b', new, 'approve', 'reviewed amendment')
        self.assertEqual(self.landing.land()['status'], 'landed')
        self.assertEqual((self.repo / 'a.txt').read_text(), 'amended\n')

    def test_parent_scope_and_clean_author_are_enforced(self):
        wrong = self.commit(self.a, 'b.txt', 'outside scope\n')
        with self.assertRaisesRegex(ValueError, 'scope'):
            self.landing.submit('ta', 'a', wrong)
        second = self.commit(self.a, 'a.txt', 'second commit\n')
        with self.assertRaisesRegex(ValueError, 'baseline'):
            self.landing.submit('ta', 'a', second)
        git(self.a, 'reset', '--hard', self.baseline)
        commit = self.commit(self.a, 'a.txt', 'allowed\n')
        (self.a / 'extra').write_text('dirty')
        with self.assertRaisesRegex(ValueError, 'clean'):
            self.landing.submit('ta', 'a', commit)

    def test_conflict_is_recorded_and_main_preserved(self):
        self.approved_a()
        (self.repo / 'a.txt').write_text('someone else\n')
        git(self.repo, 'add', 'a.txt')
        git(self.repo, 'commit', '-m', 'external advance')
        before = git(self.repo, 'rev-parse', 'HEAD')
        self.assertEqual(self.landing.land()['status'], 'conflict')
        self.assertEqual(git(self.repo, 'rev-parse', 'HEAD'), before)
        self.assertEqual(git(self.root / 'integration', 'status', '--porcelain'), '')

    def test_restart_after_main_advance_recovers_without_second_merge(self):
        self.approved_a()
        original = self.landing._advance
        def crash(candidate):
            original(candidate)
            raise RuntimeError('simulated process death after Git advancement')
        with patch.object(self.landing, '_advance', crash):
            with self.assertRaisesRegex(RuntimeError, 'process death'):
                self.landing.land()
        head = git(self.repo, 'rev-parse', 'HEAD')
        self.assertEqual(self.landing.status()[0]['status'], 'checked')
        result = Landing(self.board.path, self.repo).land()
        self.assertEqual(result['status'], 'landed')
        self.assertEqual(result['recovered'], True)
        self.assertEqual(git(self.repo, 'rev-parse', 'HEAD'), head)

    def test_cross_process_lock_and_process_exit_release(self):
        code = 'from pstack_mesh.landing import repository_lock; from pathlib import Path; import sys;\nwith repository_lock(Path(sys.argv[1])): print("locked", flush=True); sys.stdin.readline()'
        lock = self.landing.common / 'mesh-landing.lock'
        process = subprocess.Popen([sys.executable, '-u', '-c', code, str(lock)],
                                   stdin=subprocess.PIPE, env=MESH_ENV, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        self.assertEqual(process.stdout.readline().strip(), 'locked')
        with self.assertRaisesRegex(ValueError, 'holds the Git lock'):
            self.landing.land()
        process.kill()
        process.communicate(timeout=10)
        with repository_lock(lock):
            with self.assertRaisesRegex(ValueError, 'holds the Git lock'):
                self.landing.land()
        self.assertEqual(self.landing.land(), {'status': 'idle'})

    def test_restart_after_uncommitted_cherry_pick(self):
        self.approved_a()
        def crash(path, *args):
            result = git(path, *args)
            if args[:2] == ('cherry-pick', '--no-commit'):
                raise RuntimeError('process died after cherry-pick')
            return result
        with patch('pstack_mesh.landing.git', crash):
            with self.assertRaisesRegex(RuntimeError, 'process died'):
                self.landing.land()
        self.assertEqual(self.landing.status()[0]['status'], 'preparing')
        self.assertEqual(Landing(self.board.path, self.repo).land()['status'], 'landed')
        self.assertEqual((self.repo / 'a.txt').read_text(), 'author a\n')

    def test_revocation_during_check_prevents_main_advancement(self):
        commit = self.approved_a()
        original = self.landing._save
        def revoke(row, status, **fields):
            original(row, status, **fields)
            if status == 'checked':
                self.landing.review('ta', 'b', commit, 'reject', 'Found defect during checks')
        with patch.object(self.landing, '_save', revoke):
            self.assertEqual(self.landing.land()['status'], 'awaiting_review')
        self.assertEqual(git(self.repo, 'rev-parse', 'HEAD'), self.baseline)
        self.landing.review('ta', 'b', commit, 'approve', 'Defect objection resolved')
        self.assertEqual(self.landing.land()['status'], 'landed')

    def test_wrong_integration_repository_and_dirty_integration_are_rejected(self):
        self.approved_a()
        integration = self.root / 'integration'
        integration.mkdir()
        git(integration, 'init', '-b', 'integration')
        with self.assertRaisesRegex(ValueError, 'another repository'):
            self.landing.land()
        # Remove only this test-created empty repository to use the real registered checkout.
        import shutil
        shutil.rmtree(integration)
        self.landing._integration()
        (integration / 'human.txt').write_text('keep me')
        with self.assertRaisesRegex(ValueError, 'dirty'):
            self.landing.land()
        self.assertEqual((integration / 'human.txt').read_text(), 'keep me')


if __name__ == '__main__':
    unittest.main()
