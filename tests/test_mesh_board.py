"""Behavior checks for the bundled peer mesh."""
import os
from pathlib import Path
import sys

MESH_SCRIPTS = Path(__file__).resolve().parents[1] / 't3/added/mesh/scripts'
sys.path.insert(0, str(MESH_SCRIPTS))
MESH_ENV = {**os.environ, 'PYTHONPATH': str(MESH_SCRIPTS) + os.pathsep + os.environ.get('PYTHONPATH', '')}

import multiprocessing
import queue
import sqlite3
import tempfile
import time
import unittest

from pstack_mesh.board import Board


def contend(path, peer_id, start, output, operation):
    try:
        board = Board(path)
        output.put(('ready', peer_id, None))
        if not start.wait(15):
            raise TimeoutError('Contender never received start signal')
        if operation == 'claim':
            claim = board.claim(peer_id)
            output.put(('claim', peer_id, claim))
        else:
            accepted = rejected = 0
            for number in range(4):
                try:
                    board.message(f'{peer_id}-{number}', 'sender', 'recipient', 'Question')
                    accepted += 1
                except ValueError:
                    rejected += 1
            output.put(('messages', peer_id, (accepted, rejected)))
    except Exception as error:
        output.put(('error', peer_id, repr(error)))


class BoardTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / 'board.sqlite'
        self.board = Board(self.path)
        for peer_id in ('a', 'b', 'sender', 'recipient'):
            self.register(peer_id)

    def register(self, peer_id):
        return self.board.register(peer_id, f'thread-{peer_id}', self.root / peer_id)

    def wait_expiry(self, expires):
        remaining = expires - time.time()
        self.assertLess(remaining, 5, 'Fixture unexpectedly has a long lease')
        if remaining > 0:
            time.sleep(remaining + 0.025)

    def race(self, peers, operation):
        context = multiprocessing.get_context('spawn')
        start = context.Event()
        output = context.Queue()
        processes = [context.Process(target=contend,
                     args=(str(self.path), peer, start, output, operation))
                     for peer in peers]
        try:
            for process in processes:
                process.start()
            ready = [output.get(timeout=30) for _ in processes]
            self.assertEqual(sorted((item[0], item[1]) for item in ready),
                             [('ready', peer) for peer in sorted(peers)])
            start.set()
            results = []
            for _ in processes:
                try:
                    results.append(output.get(timeout=30))
                except queue.Empty:
                    self.fail('A contender did not return an observable result')
            for process in processes:
                process.join(10)
                self.assertFalse(process.is_alive(), 'Contender did not exit')
                self.assertEqual(process.exitcode, 0)
            self.assertEqual([result for result in results if result[0] == 'error'], [])
            return results
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                if process.pid is not None:
                    process.join(10)
                    process.close()
            output.close()
            output.join_thread()

    def test_separate_processes_claim_one_task_once(self):
        peers = [f'worker-{number}' for number in range(6)]
        for peer in peers:
            self.register(peer)
        self.board.add('only', 'Single task', 'file')
        results = self.race(peers, 'claim')
        winners = [result for result in results if result[2] is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(winners[0][2]['id'], 'only')
        task = self.board.tasks()[0]
        self.assertEqual((task['status'], task['attempts'], task['owner']),
                         ('claimed', 1, winners[0][1]))
        self.assertEqual(len([event for event in self.board.events()
                             if event['kind'] == 'claimed']), 1)

    def test_separate_processes_claim_distinct_tasks(self):
        peers = [f'worker-{number}' for number in range(6)]
        for number, peer in enumerate(peers):
            self.register(peer)
            self.board.add(f'task-{number}', 'Task', 'file')
        results = self.race(peers, 'claim')
        self.assertEqual(sorted(result[2]['id'] for result in results),
                         ['task-0', 'task-1', 'task-2', 'task-3', 'task-4', 'task-5'])
        self.assertEqual(sorted(task['attempts'] for task in self.board.tasks()),
                         [1, 1, 1, 1, 1, 1])

    def test_priority_and_requested_task_selection(self):
        self.board.add('low', 'Low', 'file', priority=1)
        self.board.add('high', 'High', 'file', priority=2)
        self.assertEqual(self.board.claim('a')['id'], 'high')
        self.assertEqual(self.board.claim('b', task_id='low')['id'], 'low')

    def test_unregistered_peer_and_second_claim_rejected(self):
        self.board.add('first', 'First', 'file')
        self.board.add('second', 'Second', 'file')
        with self.assertRaises(ValueError):
            self.board.claim('unknown')
        self.assertEqual(self.board.claim('a')['id'], 'first')
        with self.assertRaises(ValueError):
            self.board.claim('a')
        self.assertEqual(self.board.claim('b')['id'], 'second')

    def test_dependency_blocks_until_finished(self):
        self.board.add('parent', 'Parent', 'file')
        self.board.add('child', 'Child', 'file', dependencies=['parent'], priority=10)
        self.assertIsNone(self.board.claim('a', task_id='child'))
        parent = self.board.claim('a')
        self.assertEqual(parent['id'], 'parent')
        self.assertIsNone(self.board.claim('b'))
        self.board.finish('parent', 'a', parent['token'], {'status': 'PASS'})
        self.assertEqual(self.board.claim('b')['id'], 'child')

    def test_missing_dependency_rolls_back_entire_add(self):
        with self.assertRaises((ValueError, sqlite3.IntegrityError)):
            self.board.add('bad', 'Bad', 'file', dependencies=['missing'])
        self.assertEqual(self.board.tasks(), [])
        self.board.add('good', 'Good', 'file')
        self.assertEqual(self.board.claim('a')['id'], 'good')

    def test_non_pass_authored_outcomes_do_not_unlock_dependencies(self):
        for number, result in enumerate(({'status': 'ISSUES'}, {'status': 'BLOCKED'},
                                         {'status': 'pass'}, {})):
            with self.subTest(result=result):
                parent_id, child_id = f'parent-{number}', f'child-{number}'
                self.board.add(parent_id, 'Parent', 'file')
                self.board.add(child_id, 'Child', 'file', dependencies=[parent_id])
                parent = self.board.claim('a', task_id=parent_id)
                self.board.finish(parent_id, 'a', parent['token'], result)
                consumer = f'consumer-{number}'
                self.register(consumer)
                self.assertIsNone(self.board.claim(consumer, task_id=child_id))
                tasks = {task['id']: task for task in self.board.tasks()}
                self.assertEqual((tasks[parent_id]['status'], tasks[child_id]['status']),
                                 ('done', 'ready'))

    def test_attempt_limit_requires_positive_integer(self):
        for number, limit in enumerate((0, -1, 1.5, True)):
            with self.subTest(limit=limit):
                with self.assertRaises(ValueError):
                    self.board.add(f'bad-{number}', 'Bad limit', 'file', max_attempts=limit)
        self.assertEqual(self.board.tasks(), [])
        self.board.add('valid', 'Valid limit', 'file', max_attempts=1)
        self.assertEqual(self.board.claim('a')['attempts'], 1)

    def test_expired_lease_reclaimed_and_old_writes_fenced(self):
        self.board.add('task', 'Task', 'file')
        old = self.board.claim('a', lease_seconds=0.1)
        self.wait_expiry(old['expires'])
        for method, extra in ((self.board.heartbeat, {}),
                              (self.board.finish, {'result': {'status': 'PASS'}})):
            with self.assertRaises(ValueError):
                method('task', 'a', old['token'], **extra)
        replacement = self.board.claim('b')
        self.assertEqual((replacement['owner'], replacement['attempts']), ('b', 2))
        self.assertNotEqual(old['token'], replacement['token'])
        with self.assertRaises(ValueError):
            self.board.finish('task', 'a', old['token'], {'status': 'PASS'})
        self.board.finish('task', 'b', replacement['token'], {'status': 'PASS'})
        self.assertEqual(self.board.tasks()[0]['status'], 'done')

    def test_same_peer_reclaim_still_rejects_old_token(self):
        self.board.add('task', 'Task', 'file')
        old = self.board.claim('a', lease_seconds=0.1)
        self.wait_expiry(old['expires'])
        replacement = self.board.claim('a')
        self.assertEqual(replacement['attempts'], 2)
        with self.assertRaises(ValueError):
            self.board.heartbeat('task', 'a', old['token'])
        self.board.finish('task', 'a', replacement['token'], {'status': 'PASS'})
        self.assertEqual(self.board.tasks()[0]['status'], 'done')

    def test_heartbeat_keeps_claim_alive_and_wrong_owner_is_fenced(self):
        self.board.add('task', 'Task', 'file')
        claim = self.board.claim('a', lease_seconds=0.2)
        renewal = self.board.heartbeat('task', 'a', claim['token'], lease_seconds=1)
        self.assertGreater(renewal['expires'], claim['expires'])
        self.wait_expiry(claim['expires'])
        self.assertIsNone(self.board.claim('b'))
        with self.assertRaises(ValueError):
            self.board.finish('task', 'b', claim['token'], {})
        self.board.finish('task', 'a', claim['token'], {'status': 'PASS'})
        self.assertEqual(self.board.tasks()[0]['status'], 'done')

    def test_attempt_limit_stops_repeated_recovery(self):
        self.board.add('task', 'Task', 'file', max_attempts=2)
        for expected_attempt in (1, 2):
            claim = self.board.claim('a', lease_seconds=0.1)
            self.assertEqual(claim['attempts'], expected_attempt)
            self.wait_expiry(claim['expires'])
            self.board.recover()
        task = self.board.tasks()[0]
        self.assertEqual((task['status'], task['attempts'], task['owner'], task['token']),
                         ('failed', 2, None, None))
        self.assertIsNone(self.board.claim('b'))

    def test_expired_deadline_is_never_claimed(self):
        self.board.add('past', 'Past', 'file', deadline=time.time() - 1)
        self.assertIsNone(self.board.claim('a'))
        self.assertEqual((self.board.tasks()[0]['status'], self.board.tasks()[0]['attempts']),
                         ('failed', 0))

    def test_deadline_caps_lease_and_prevents_completion(self):
        deadline = time.time() + 0.2
        self.board.add('task', 'Task', 'file', deadline=deadline)
        claim = self.board.claim('a')
        self.assertEqual(claim['expires'], deadline)
        self.assertEqual(self.board.heartbeat('task', 'a', claim['token'])['expires'], deadline)
        self.wait_expiry(deadline)
        with self.assertRaises(ValueError):
            self.board.finish('task', 'a', claim['token'], {'status': 'PASS'})
        self.board.recover()
        self.assertEqual(self.board.tasks()[0]['status'], 'failed')

    def test_failed_dependency_never_unlocks_child(self):
        self.board.add('parent', 'Parent', 'file', deadline=time.time() - 1)
        self.board.add('child', 'Child', 'file', dependencies=['parent'])
        self.assertIsNone(self.board.claim('a'))
        self.assertEqual([(task['id'], task['status']) for task in self.board.tasks()],
                         [('parent', 'failed'), ('child', 'ready')])

    def test_registry_identity_persists_and_cannot_be_rebound(self):
        original = self.register('a')
        self.assertEqual(Board(self.path).register('a', 'thread-a', self.root / 'a', ['review']),
                         original)
        for thread, workspace in [('other-thread', self.root / 'a'),
                                  ('thread-a', self.root / 'other')]:
            with self.assertRaises(ValueError):
                self.board.register('a', thread, workspace)
        peers = {peer['id']: peer for peer in Board(self.path).peers()}
        self.assertEqual((peers['a']['thread_id'], peers['a']['workspace']),
                         ('thread-a', str((self.root / 'a').resolve())))
        self.assertEqual(len(peers), 4)

    def test_two_peer_ids_cannot_share_thread_or_workspace(self):
        for thread, workspace in [('thread-a', self.root / 'other'),
                                  ('thread-other', self.root / 'a')]:
            with self.assertRaises((ValueError, sqlite3.IntegrityError)):
                self.board.register('other', thread, workspace)
        self.assertEqual(len(self.board.peers()), 4)
        self.assertEqual(self.register('unique')['thread_id'], 'thread-unique')

    def test_nonfinite_times_rejected_without_claiming(self):
        for number, value in enumerate((float('nan'), float('inf'), -float('inf'))):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.board.add(f'bad-{number}', 'Bad deadline', 'file', deadline=value)
        self.assertEqual(self.board.tasks(), [])
        self.board.add('valid', 'Valid', 'file')
        for value in (float('nan'), float('inf'), -float('inf')):
            with self.subTest(lease=value):
                with self.assertRaises(ValueError):
                    self.board.claim('a', lease_seconds=value)
        self.assertEqual(self.board.claim('a')['attempts'], 1)

    def test_message_reservation_is_idempotent_and_content_immutable(self):
        first = self.board.message('question', 'sender', 'recipient', 'Which file?')
        self.assertEqual(self.board.message('question', 'sender', 'recipient', 'Which file?'), first)
        with self.assertRaises(ValueError):
            self.board.message('question', 'sender', 'recipient', 'Different question')
        self.board.delivered('question', 'transport-1')
        self.board.delivered('question', 'transport-1')
        with self.assertRaises(ValueError):
            self.board.delivered('question', 'transport-2')
        messages = self.board.messages()
        self.assertEqual(len(messages), 1)
        self.assertEqual((messages[0]['state'], messages[0]['transport_id'], messages[0]['body']),
                         ('delivered', 'transport-1', 'Which file?'))

    def test_twenty_message_budget_preserves_retry(self):
        for number in range(20):
            self.board.message(f'q-{number}', 'sender', 'recipient', 'Question')
        with self.assertRaises(ValueError):
            self.board.message('over-budget', 'sender', 'recipient', 'Question')
        self.assertEqual(self.board.message('q-0', 'sender', 'recipient', 'Question')['id'], 'q-0')
        self.assertEqual(len(self.board.messages()), 20)
        self.assertEqual(self.board.message('reverse', 'recipient', 'sender', 'Answer')['sender'],
                         'recipient')

    def test_separate_processes_cannot_exceed_sender_budget(self):
        results = self.race([f'worker-{number}' for number in range(6)], 'messages')
        self.assertEqual((sum(result[2][0] for result in results),
                          sum(result[2][1] for result in results)), (20, 4))
        self.assertEqual(len(self.board.messages()), 20)

    def test_invalid_message_and_lease_inputs_leave_valid_operations_available(self):
        for body in ('', 'x' * 8001):
            with self.assertRaises(ValueError):
                self.board.message('bad', 'sender', 'recipient', body)
        with self.assertRaises(ValueError):
            self.board.message('self', 'sender', 'sender', 'Question')
        self.board.add('task', 'Task', 'file')
        for seconds in (0, -1, 3601):
            with self.assertRaises(ValueError):
                self.board.claim('a', lease_seconds=seconds)
        claim = self.board.claim('a')
        self.assertEqual(claim['attempts'], 1)
        self.assertEqual(self.board.message('valid', 'sender', 'recipient', 'Question')['state'],
                         'pending')

    def test_restart_retains_claim_result_and_transport_evidence(self):
        self.board.add('task', 'Task', 'file')
        claim = self.board.claim('a')
        restarted = Board(self.path)
        restarted.finish('task', 'a', claim['token'], {'status': 'PASS', 'evidence': 'ran tests'})
        restarted.message('question', 'sender', 'recipient', 'Question')
        restarted.delivered('question', 'actual-message-id')
        reopened = Board(self.path)
        self.assertEqual(reopened.tasks()[0]['status'], 'done')
        self.assertEqual(reopened.messages()[0]['transport_id'], 'actual-message-id')
        finished = [event for event in reopened.events() if event['kind'] == 'finished']
        self.assertEqual(len(finished), 1)
        with self.assertRaises(ValueError):
            reopened.finish('task', 'a', claim['token'], {})


if __name__ == '__main__':
    unittest.main()
