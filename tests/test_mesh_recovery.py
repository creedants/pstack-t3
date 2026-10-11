"""Behavior checks for the bundled peer mesh."""
import os
from pathlib import Path
import sys

MESH_SCRIPTS = Path(__file__).resolve().parents[1] / 't3/added/mesh/scripts'
sys.path.insert(0, str(MESH_SCRIPTS))
MESH_ENV = {**os.environ, 'PYTHONPATH': str(MESH_SCRIPTS) + os.pathsep + os.environ.get('PYTHONPATH', '')}

import json
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from pstack_mesh.board import Board


class RecoveryTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'board.sqlite'
        self.board = Board(self.path)
        self.board.register('a', 'thread-a', self.path.parent / 'a')
        self.board.register('b', 'thread-b', self.path.parent / 'b')

    def test_only_pass_result_unlocks_dependency(self):
        for outcome in ('PASS', 'ISSUES', 'BLOCKED', 'pass', None):
            with self.subTest(outcome=outcome):
                parent, child = f'parent-{outcome}', f'child-{outcome}'
                self.board.add(parent, parent, 'file')
                self.board.add(child, child, 'file', dependencies=[parent])
                self.assertIsNone(self.board.claim('b', task_id=child))
                claim = self.board.claim('a', task_id=parent)
                result_data = {} if outcome is None else {'status': outcome}
                self.board.finish(parent, 'a', claim['token'], result_data)
                unlocked = self.board.claim('b', task_id=child)
                if outcome == 'PASS':
                    self.assertEqual(unlocked['id'], child)
                    self.board.finish(child, 'b', unlocked['token'], {'status': 'PASS'})
                else:
                    self.assertIsNone(unlocked)
                result = next(t for t in self.board.tasks() if t['id'] == parent)
                self.assertEqual(result['status'], 'done')
                self.assertEqual(json.loads(result['result']), result_data)
                self.assertEqual(result['owner'], 'a')

    def test_attempt_limits_require_positive_integers(self):
        for limit in (0, -1, 1.5, True, float('inf'), float('nan'), '3'):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.board.add('bad', 'Bad', 'file', max_attempts=limit)
        self.board.add('valid', 'Valid', 'file', max_attempts=2)
        self.assertEqual([(t['id'], t['max_attempts']) for t in self.board.tasks()], [('valid', 2)])

    def test_expired_tokens_reject_before_recovery_and_after_replacement(self):
        with patch('pstack_mesh.board.time.time', return_value=100):
            self.board.add('task', 'Task', 'file', max_attempts=2)
            first = self.board.claim('a', lease_seconds=10)
        with patch('pstack_mesh.board.time.time', return_value=110):
            for operation in ('heartbeat', 'finish'):
                with self.assertRaises(ValueError):
                    if operation == 'finish':
                        self.board.finish('task', 'a', first['token'], {'status': 'PASS'})
                    else:
                        self.board.heartbeat('task', 'a', first['token'])
            self.assertEqual(self.board.recover()[0]['status'], 'ready')
            second = self.board.claim('b', lease_seconds=10)
            self.assertEqual(second['attempts'], 2)
            self.assertNotEqual(second['token'], first['token'])
            with self.assertRaises(ValueError):
                self.board.heartbeat('task', 'a', first['token'])
            with self.assertRaises(ValueError):
                self.board.finish('task', 'a', first['token'], {'status': 'PASS'})
        with patch('pstack_mesh.board.time.time', return_value=120):
            failed = self.board.recover()[0]
            self.assertEqual((failed['status'], failed['owner'], failed['token']), ('failed', None, None))
            self.assertIsNone(self.board.claim('a'))

    def test_deadline_caps_renewal_and_records_recovery(self):
        with patch('pstack_mesh.board.time.time', return_value=100):
            self.board.add('task', 'Task', 'file', deadline=105)
            claim = self.board.claim('a')
            self.assertEqual(claim['expires'], 105)
            self.assertEqual(self.board.heartbeat('task', 'a', claim['token']), {'expires': 105})
        with patch('pstack_mesh.board.time.time', return_value=105):
            with self.assertRaises(ValueError):
                self.board.finish('task', 'a', claim['token'], {'status': 'PASS'})
            self.assertEqual(self.board.recover()[0]['status'], 'failed')
            self.board.recover()
        deadlines = [e for e in self.board.events() if e['kind'] == 'deadline_expired']
        self.assertEqual([(e['task_id'], json.loads(e['data'])) for e in deadlines], [('task', {'status': 'failed'})])

    def test_nonfinite_timing_rejected_and_zero_deadline_expires(self):
        for number in (True, float('nan'), float('inf'), -float('inf'), '10'):
            with self.subTest(number=number):
                with self.assertRaises(ValueError):
                    self.board.claim('a', lease_seconds=number)
                with self.assertRaises(ValueError):
                    self.board.add('bad', 'Bad', 'file', deadline=number)
        self.board.add('zero', 'Zero', 'file', deadline=0)
        self.assertIsNone(self.board.claim('a'))
        self.assertEqual(self.board.tasks()[0]['status'], 'failed')

    def test_claim_lease_starts_after_database_lock_wait(self):
        waiting = threading.Event()

        class SignallingBoard(Board):
            @contextmanager
            def transaction(self):
                waiting.set()
                with super().transaction() as database:
                    yield database

        contender = SignallingBoard(self.path)
        self.board.add('task', 'Task', 'file')
        results, errors = [], []

        def claim():
            try:
                results.append(contender.claim('a', lease_seconds=0.15))
            except BaseException as error:
                errors.append(error)

        with self.board.transaction():
            worker = threading.Thread(target=claim)
            worker.start()
            self.assertTrue(waiting.wait(5), 'claim did not reach the locked transaction')
            time.sleep(0.3)
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 1)
        self.assertGreater(results[0]['expires'], time.time())
        self.assertEqual(self.board.finish('task', 'a', results[0]['token'], {'status': 'PASS'}), {'id': 'task', 'status': 'done'})

    def test_registry_identity_errors_are_atomic(self):
        for id, thread, workspace in (
            ('', 'thread-x', self.path.parent / 'x'),
            ('a', 'thread-x', self.path.parent / 'a'),
            ('x', 'thread-a', self.path.parent / 'x'),
            ('x', 'thread-x', self.path.parent / 'a' / '..' / 'a'),
            ('x', '', self.path.parent / 'x'),
            ('x', 'thread-x', ''),
        ):
            with self.subTest(id=id, thread=thread), self.assertRaises(ValueError):
                self.board.register(id, thread, workspace)
        self.board.register('a', 'thread-a', self.path.parent / 'a', ['review'])
        self.assertEqual([(p['id'], json.loads(p['capabilities'])) for p in self.board.peers()], [('a', ['review']), ('b', [])])

    def test_failed_add_rolls_back_and_connections_close(self):
        with self.assertRaises(ValueError):
            self.board.add('bad', 'Bad', 'file', dependencies=['missing'])
        self.board.add('good', 'Good', 'file')
        self.assertEqual([t['id'] for t in self.board.tasks()], ['good'])
        with self.board.connect() as database:
            self.assertEqual(database.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            database.execute('SELECT 1')
        with self.assertRaises(KeyboardInterrupt):
            with self.board.transaction() as transaction:
                transaction.execute("UPDATE tasks SET title='wrong'")
                raise KeyboardInterrupt()
        self.assertEqual(self.board.tasks()[0]['title'], 'Good')
        with self.assertRaises(sqlite3.ProgrammingError):
            transaction.execute('SELECT 1')

    def test_message_retry_preserves_budget_and_transport(self):
        first = self.board.message('m0', 'a', 'b', 'question')
        for index in range(1, 20):
            self.board.message(f'm{index}', 'a', 'b', 'question')
        self.assertEqual(self.board.message('m0', 'a', 'b', 'question'), first)
        with self.assertRaises(ValueError):
            self.board.message('m20', 'a', 'b', 'question')
        with self.assertRaises(ValueError):
            self.board.message('m0', 'a', 'b', 'different')
        with self.assertRaises(ValueError):
            self.board.delivered('m0', '')
        self.board.delivered('m0', 't3-message-id')
        self.board.delivered('m0', 't3-message-id')
        with self.assertRaises(ValueError):
            self.board.delivered('m0', 'another-id')
        self.assertEqual(self.board.messages()[0]['transport_id'], 't3-message-id')
        delivered = [e for e in self.board.events() if e['kind'] == 'message_delivered']
        self.assertEqual([json.loads(e['data']) for e in delivered], [{'id': 'm0', 'recipient': 'b', 'transport_id': 't3-message-id'}])

    def test_json_cli_rejects_bad_files_and_nonobject_input(self):
        for raw in ('[]', '{broken', '@' + str(self.path.parent / 'missing.json')):
            result = subprocess.run([sys.executable, '-m', 'pstack_mesh', '--db', str(self.path), 'peers', '--data', raw], env=MESH_ENV, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn('Traceback', result.stderr)
        data = self.path.parent / 'add.json'
        data.write_text(json.dumps({'id': 'cli', 'title': 'CLI', 'scope': 'file'}), encoding='utf-8-sig')
        result = subprocess.run([sys.executable, '-m', 'pstack_mesh', '--db', str(self.path), 'add', '--data', '@' + str(data)], env=MESH_ENV, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'id': 'cli'})
        self.assertEqual(self.board.tasks()[0]['title'], 'CLI')


if __name__ == '__main__':
    unittest.main()
