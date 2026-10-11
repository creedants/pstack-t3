import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('project_fixed', SOURCE / 't3/added/project/scripts/project.py')
project = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = project
spec.loader.exec_module(project)
from pstack_mesh.board import Board
from pstack_mesh.landing import Landing, artifact_ref
from pstack_mesh.verify import verify


def git(repo, *args):
    return subprocess.check_output(['git', '-c', 'core.longpaths=true', '-C', str(repo), *args], text=True, stderr=subprocess.PIPE).strip()


class ProjectFixes(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='project-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.capacity = patch.dict(os.environ, {'PSTACK_MESH_CAPACITY_STATE': str(self.root / 'capacity')})
        self.capacity.start()
        self.addCleanup(self.capacity.stop)
        self.repo = self.root / 'r'
        self.repo.mkdir()
        git(self.repo, 'init', '-b', 'main')
        for key, value in [('user.name', 'Test'), ('user.email', 'test@example.invalid'),
                           ('commit.gpgsign', 'false'), ('core.autocrlf', 'false')]:
            git(self.repo, 'config', key, value)
        (self.repo / 'base').write_text('base\n')
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-m', 'Baseline')
        self.base = git(self.repo, 'rev-parse', 'HEAD')
        self.store = project.Project(self.repo, self.root / 's')
        self.store.open('project', 'interface', 'Finite behavioral tests')
        self.goal = self.store.accept('one', 'Authored files', ['Files land'],
                                      {'independent_units': 4, 'retained_expertise': True})
        self.tasks = [{'id': 'a', 'title': 'A', 'paths': ['a.txt']}]
        self.check = [sys.executable, '-c', 'pass']

    def start(self, tasks=None):
        return self.store.start_mesh(self.goal['id'], tasks or self.tasks, self.check)

    def peer(self, board, name='w'):
        path = self.root / name
        git(self.repo, 'worktree', 'add', '-b', 'peer/' + name, str(path), self.base)
        self.store.peer(name, 'thread-' + name, path)
        board.register(name, 'thread-' + name, path)
        return path

    def observe(self, path, name='w', active=False):
        return {'threadId': 'thread-' + name, 'status': 'running' if active else 'completed',
                'activeRunId': 'native-run-' + name if active else None,
                'worktreePath': str(path), 'branch': 'peer/' + name}

    def author(self, board, path, task, claim, name='w'):
        (path / (task + '.txt')).write_text(task + '\n')
        git(path, 'add', task + '.txt')
        git(path, 'commit', '-m', 'Author ' + task)
        commit = git(path, 'rev-parse', 'HEAD')
        board.finish(task, name, claim['token'], {'status': 'PASS', 'commit': commit})
        return commit

    def test_cancelled_unstarted_goal_stays_terminal(self):
        self.store.cancel(self.goal['id'], 'User cancelled')
        with self.assertRaisesRegex(ValueError, 'Terminal goal'):
            self.start()
        self.assertEqual(self.store.context()['goals'][0]['state'], 'cancelled')
        self.assertEqual(git(self.repo, 'rev-parse', 'HEAD'), self.base)

    def test_second_explicit_root_refuses_and_default_restores_marker(self):
        with self.assertRaisesRegex(ValueError, 'canonical project database'):
            project.Project(self.repo, self.root / 'other')
        with patch.dict(os.environ, {'PSTACK_PROJECT_STATE': str(self.root / 'other')}):
            reopened = project.Project(self.repo)
        self.assertEqual(reopened.database, self.store.database)
        self.assertEqual(reopened.context()['project']['interface_thread'], 'interface')
        self.assertFalse((self.root / 'other').exists())
        marker = json.loads((self.store.common / 'pstack-project.json').read_text())
        self.assertEqual(marker, {'project_database': str(self.store.database)})

    def test_invalid_later_task_never_publishes_partial_board(self):
        bad = [{'id': 'orphan', 'title': 'Orphan', 'paths': ['orphan.txt']},
               {'id': 'invalid', 'title': 'Invalid', 'paths': ['invalid.txt'], 'max_attempts': 0}]
        with self.assertRaisesRegex(ValueError, 'max_attempts'):
            self.start(bad)
        directory = self.store.directory / 'goals' / self.goal['id']
        self.assertFalse((directory / 'board.sqlite').exists())
        self.assertEqual(list(directory.glob('board-*.sqlite*')), [])
        run = self.start()
        board = Board(run['board'])
        path = self.peer(board)
        self.assertEqual([task['id'] for task in board.tasks()], ['a'])
        self.assertEqual(board.claim('w')['id'], 'a')
        self.assertEqual(git(path, 'rev-parse', 'HEAD'), self.base)

    def interrupt_publication(self):
        original = self.store.event
        def event(db, kind, data):
            if kind == 'mesh_started':
                raise SystemExit('interrupted after board publication')
            original(db, kind, data)
        with patch.object(self.store, 'event', side_effect=event):
            with self.assertRaises(SystemExit):
                self.start()
        self.assertEqual(self.store.context()['goals'][0]['state'], 'accepted')

    def test_published_board_recovers_only_identical_specification(self):
        self.interrupt_publication()
        with self.assertRaisesRegex(ValueError, 'Interrupted mesh'):
            self.start([{'id': 'b', 'title': 'B', 'paths': ['b.txt']}])
        run = self.start()
        self.assertEqual([task['id'] for task in Board(run['board']).tasks()], ['a'])
        self.assertEqual(self.store.context()['goals'][0]['state'], 'running')
        self.assertEqual(self.start()['board'], run['board'])

    def test_published_board_refuses_changed_baseline(self):
        self.interrupt_publication()
        (self.repo / 'later').write_text('later\n')
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-m', 'Later')
        with self.assertRaisesRegex(ValueError, 'Interrupted mesh'):
            self.start()
        self.assertEqual(self.store.context()['goals'][0]['state'], 'accepted')

    def test_cancellation_fences_board_published_before_project_commit(self):
        self.interrupt_publication()
        board = Board(self.store.directory / 'goals' / self.goal['id'] / 'board.sqlite')
        self.peer(board)
        self.store.cancel(self.goal['id'], 'Cancel interrupted setup')
        with self.assertRaisesRegex(ValueError, 'not running'):
            board.claim('w')
        with self.assertRaisesRegex(ValueError, 'Terminal goal'):
            self.start()
        self.assertEqual(git(self.repo, 'rev-parse', 'HEAD'), self.base)

    def test_normalized_duplicate_paths_refuse_publication(self):
        tasks = [{'id': 'a', 'title': 'A', 'paths': ['a.txt']},
                 {'id': 'b', 'title': 'B', 'paths': ['./a.txt']}]
        with self.assertRaisesRegex(ValueError, 'separate file scopes'):
            self.start(tasks)
        run = self.start([{'id': 'a', 'title': 'A', 'paths': ['./a.txt']}])
        with Board(run['board']).connect() as db:
            scopes = json.loads(db.execute("SELECT value FROM run_config WHERE key='task_scopes'").fetchone()[0])
        self.assertEqual(scopes, {'a': ['a.txt']})

    @unittest.skipUnless(os.name == 'nt', 'Native Windows case aliases')
    def test_windows_case_aliases_refuse_publication(self):
        tasks = [{'id': 'a', 'title': 'A', 'paths': ['Answer.txt']},
                 {'id': 'b', 'title': 'B', 'paths': ['answer.txt']}]
        with self.assertRaisesRegex(ValueError, 'separate file scopes'):
            self.start(tasks)
        self.assertFalse((self.store.directory / 'goals' / self.goal['id'] / 'board.sqlite').exists())

    def test_prepare_task_refuses_foreign_active_dirty_and_stale_claim(self):
        run = self.start()
        board = Board(run['board'])
        path = self.peer(board)
        claim = board.claim('w')
        active = self.observe(path, active=True)
        with self.assertRaisesRegex(ValueError, 'Only the running peer'):
            self.store.prepare_task('w', self.goal['id'], active, caller_thread_id='foreign', task_id='a', claim_token=claim['token'])
        with self.assertRaisesRegex(ValueError, 'Claim expired'):
            self.store.prepare_task('w', self.goal['id'], active, caller_thread_id='thread-w', task_id='a', claim_token='stale')
        (path / 'dirty').write_text('preserve\n')
        with self.assertRaisesRegex(ValueError, 'uncommitted'):
            self.store.prepare_task('w', self.goal['id'], active, caller_thread_id='thread-w', task_id='a', claim_token=claim['token'])
        self.assertEqual((path / 'dirty').read_text(), 'preserve\n')
        self.assertEqual(git(path, 'rev-parse', 'HEAD'), self.base)
        with self.assertRaises(ValueError):
            self.store.prepare('w', self.goal['id'], active)

    def test_four_tasks_three_peers_self_prepare_and_preserve_all_commits(self):
        run = self.start([{'id': name, 'title': name.upper(), 'paths': [name + '.txt']} for name in 'abcd'])
        board = Board(run['board'])
        peers = {name: self.peer(board, name) for name in ('w', 'x', 'y')}
        queue = Landing(run['board'], self.repo)
        commits = {}
        for task, name in zip('abcd', ('w', 'x', 'y', 'w')):
            claim = board.claim(name, task_id=task)
            self.store.prepare_task(name, self.goal['id'], self.observe(peers[name], name, active=True),
                                    caller_thread_id='thread-' + name, task_id=task, claim_token=claim['token'])
            commits[task] = self.author(board, peers[name], task, claim, name)
            queue.submit(task, name, commits[task])
            reviewer = 'x' if name != 'x' else 'y'
            queue.review(task, reviewer, commits[task], 'approve', {'observed': task + '.txt'})
            self.assertEqual(queue.land()['status'], 'landed')
        for task, commit in commits.items():
            self.assertEqual(git(self.repo, 'rev-parse', artifact_ref(run['board'], task, commit)), commit)
            self.assertEqual(git(self.repo, 'show', '-s', '--format=%P', commit), self.base)
            self.assertEqual((self.repo / (task + '.txt')).read_text(), task + '\n')
        report = verify(run['board'])
        checks = {item['name']: item['status'] for item in report['checks']}
        self.assertEqual({key: checks[key] for key in ('task.a.authored', 'task.b.authored', 'task.c.authored', 'task.d.authored')},
                         {'task.a.authored': 'PASS', 'task.b.authored': 'PASS', 'task.c.authored': 'PASS', 'task.d.authored': 'PASS'})
        self.assertEqual(self.store.complete(self.goal['id'], {'status': 'PASS', 'artifact': str(self.repo)})['state'], 'complete')
        with self.assertRaisesRegex(ValueError, 'not running'):
            board.claim('w')
        self.assertEqual(len(self.store.context()['peers']), 3)

    def test_self_preparation_submits_finished_head_before_reset(self):
        run = self.start([{'id': name, 'title': name, 'paths': [name + '.txt']} for name in 'ab'])
        board = Board(run['board'])
        path = self.peer(board)
        self.peer(board, 'x')
        first = board.claim('w', task_id='a')
        commit = self.author(board, path, 'a', first)
        next_claim = board.claim('w', task_id='b')
        self.store.prepare_task('w', self.goal['id'], self.observe(path, active=True),
                                caller_thread_id='thread-w', task_id='b', claim_token=next_claim['token'])
        self.assertEqual(git(path, 'rev-parse', 'HEAD'), self.base)
        self.assertEqual(git(self.repo, 'rev-parse', artifact_ref(run['board'], 'a', commit)), commit)
        queue = Landing(run['board'], self.repo)
        self.assertEqual([(row['task_id'], row['commit_sha'], row['status']) for row in queue.status()], [('a', commit, 'pending')])
        queue.review('a', 'x', commit, 'approve', {'observed': 'Immutable authored a.txt'})
        self.assertEqual(queue.land()['status'], 'landed')
        self.assertEqual((self.repo / 'a.txt').read_text(), 'a\n')
        self.assertEqual(board.tasks()[1]['token'], next_claim['token'])

    def test_completed_board_recovers_interrupted_project_completion(self):
        run = self.start()
        board = Board(run['board'])
        path = self.peer(board)
        self.peer(board, 'x')
        claim = board.claim('w')
        commit = self.author(board, path, 'a', claim)
        queue = Landing(run['board'], self.repo)
        queue.submit('a', 'w', commit)
        queue.review('a', 'x', commit, 'approve', {'observed': 'a.txt'})
        self.assertEqual(queue.land()['status'], 'landed')
        original = self.store.event
        def event(db, kind, data):
            if kind == 'goal_completed':
                raise SystemExit('interrupted after board completion')
            original(db, kind, data)
        evidence = {'status': 'PASS', 'artifact': str(self.repo / 'a.txt')}
        with patch.object(self.store, 'event', side_effect=event):
            with self.assertRaises(SystemExit):
                self.store.complete(self.goal['id'], evidence)
        self.assertEqual(self.store.context()['goals'][0]['state'], 'running')
        with self.assertRaisesRegex(ValueError, 'not running'):
            board.claim('w')
        with self.assertRaisesRegex(ValueError, 'Completed board'):
            self.store.cancel(self.goal['id'], 'Late cancellation')
        self.assertEqual(self.store.complete(self.goal['id'], evidence)['state'], 'complete')
        self.assertEqual(self.store.complete(self.goal['id'], evidence)['state'], 'complete')

    def test_completion_refuses_ambiguous_historical_submission_attempt(self):
        run = self.start()
        board = Board(run['board'])
        path = self.peer(board)
        self.peer(board, 'x')
        claim = board.claim('w')
        commit = self.author(board, path, 'a', claim)
        queue = Landing(run['board'], self.repo)
        queue.submit('a', 'w', commit)
        queue.review('a', 'x', commit, 'approve', {'observed': 'a.txt'})
        self.assertEqual(queue.land()['status'], 'landed')
        with board.transaction() as db:
            db.execute('UPDATE submissions SET task_attempt=NULL')
        with self.assertRaisesRegex(ValueError, 'Exact authored task commit'):
            self.store.complete(self.goal['id'], {'status': 'PASS', 'artifact': str(self.repo / 'a.txt')})
        self.assertEqual(self.store.context()['goals'][0]['state'], 'running')
        with board.transaction() as db:
            db.execute('UPDATE submissions SET task_attempt=1')
        self.assertEqual(self.store.complete(self.goal['id'], {'status': 'PASS', 'artifact': str(self.repo / 'a.txt')})['state'], 'complete')


if __name__ == '__main__':
    unittest.main(verbosity=2)
