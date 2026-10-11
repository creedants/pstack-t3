import json
import os
from pathlib import Path
import sys
import subprocess
import time
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / 't3/added/mesh/scripts'))

import test_mesh_landing as fixtures
from pstack_mesh.landing import Landing, artifact_ref, git
from pstack_mesh.verify import verify


class QueueFences(unittest.TestCase):
    def setUp(self):
        fixtures.LandingTest.setUp(self)
        env = patch.dict(os.environ, {'PSTACK_MESH_CAPACITY_STATE': str(self.root / 'capacity')})
        env.start()
        self.addCleanup(env.stop)
    commit = fixtures.LandingTest.commit
    approved_a = fixtures.LandingTest.approved_a

    def flag(self, value):
        with self.board.transaction() as db:
            db.execute('INSERT INTO run_config VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', ('goal_state', json.dumps(value)))

    def complete(self, task, owner, commit, status='PASS'):
        current = next(t for t in self.board.tasks() if t['id'] == task)
        return self.board.finish(task, owner, current['token'], {'status': status, 'commit': commit})

    def project(self):
        self.flag('running')
        database = str(self.root / 'project.sqlite')
        with self.board.transaction() as db:
            db.execute('INSERT INTO run_config VALUES(?,?)', ('project_database', json.dumps(database)))
        (self.landing.common / 'pstack-project.json').write_text(json.dumps({'project_database': database}), encoding='utf-8')

    def unchanged(self):
        self.assertEqual(git(self.repo, 'rev-parse', 'HEAD'), self.baseline)
        self.assertEqual((self.repo / 'a.txt').read_text(), 'baseline\n')

    def test_cancelled_finished_task_cannot_submit(self):
        commit = self.commit(self.a, 'a.txt', 'finished\n')
        self.complete('ta', 'a', commit)
        self.flag('cancelled')
        with self.assertRaisesRegex(ValueError, 'not running'):
            self.landing.submit('ta', 'a', commit)
        self.assertEqual(self.landing.status(), [])
        self.unchanged()

    def test_terminal_flag_blocks_claim_heartbeat_finish_and_recovery(self):
        task = next(t for t in self.board.tasks() if t['id'] == 'ta')
        self.flag('complete')
        for call in (lambda: self.board.claim('a'),
                     lambda: self.board.heartbeat('ta', 'a', task['token']),
                     lambda: self.board.finish('ta', 'a', task['token'], {'status': 'PASS'}),
                     self.board.recover):
            with self.assertRaisesRegex(ValueError, 'not running'):
                call()
        self.assertEqual(next(t for t in self.board.tasks() if t['id'] == 'ta')['status'], 'claimed')
        self.unchanged()

    def test_terminal_flag_blocks_review_retry_and_land_with_cached_queue(self):
        commit = self.approved_a()
        self.flag('cancelled')
        for call in (lambda: self.landing.review('ta', 'b', commit, 'approve', 'Exact commit'),
                     lambda: self.landing.submit('ta', 'a', commit), self.landing.land):
            with self.assertRaisesRegex(ValueError, 'not running'):
                call()
        self.assertEqual(self.landing.status()[0]['status'], 'pending')
        self.unchanged()

    def test_expired_claim_cannot_land_before_recovery(self):
        self.approved_a()
        with self.board.transaction() as db:
            db.execute('UPDATE tasks SET expires=? WHERE id=?', (time.time() - 1, 'ta'))
        with self.assertRaisesRegex(ValueError, 'expired'):
            self.landing.land()
        self.assertEqual(self.landing.status()[0]['status'], 'pending')
        self.unchanged()

    def test_reassigned_same_owner_attempt_cannot_land_or_reuse_reviews(self):
        commit = self.approved_a()
        old = next(t for t in self.board.tasks() if t['id'] == 'ta')
        with self.board.transaction() as db:
            db.execute('UPDATE tasks SET expires=? WHERE id=?', (time.time() - 1, 'ta'))
        new = self.board.claim('a', task_id='ta')
        self.assertEqual(new['attempts'], old['attempts'] + 1)
        with self.assertRaisesRegex(ValueError, 'expired'):
            self.board.finish('ta', 'a', old['token'], {'status': 'PASS', 'commit': commit})
        with self.assertRaisesRegex(ValueError, 'superseded'):
            self.landing.land()
        with self.assertRaisesRegex(ValueError, 'superseded'):
            self.landing.submit('ta', 'a', commit)
        self.unchanged()

    def test_reassigned_other_owner_cannot_land(self):
        self.approved_a()
        self.board.register('c', 'thread-c', self.root / 'c')
        with self.board.transaction() as db:
            db.execute('UPDATE tasks SET expires=? WHERE id=?', (time.time() - 1, 'ta'))
        new = self.board.claim('c', task_id='ta')
        self.assertEqual(new['owner'], 'c')
        with self.assertRaisesRegex(ValueError, 'another peer'):
            self.landing.land()
        self.unchanged()

    def test_finished_result_must_pass_and_match_exact_commit(self):
        commit = self.commit(self.a, 'a.txt', 'finished\n')
        self.complete('ta', 'a', commit, status='FAIL')
        with self.assertRaisesRegex(ValueError, 'PASS'):
            self.landing.submit('ta', 'a', commit)
        with self.board.transaction() as db:
            db.execute('UPDATE tasks SET result=? WHERE id=?', (json.dumps({'status': 'PASS', 'commit': self.baseline}), 'ta'))
        with self.assertRaisesRegex(ValueError, 'result commit'):
            self.landing.submit('ta', 'a', commit)
        self.unchanged()

    def test_project_claimed_admission_refused_then_completed_exact_commit_lands(self):
        self.project()
        commit = self.commit(self.a, 'a.txt', 'finished\n')
        with self.assertRaisesRegex(ValueError, 'completed PASS'):
            self.landing.submit('ta', 'a', commit)
        self.complete('ta', 'a', commit)
        item = self.landing.submit('ta', 'a', commit)
        self.assertEqual(item['task_attempt'], 1)
        self.assertEqual(git(self.repo, 'rev-parse', artifact_ref(self.board.path, 'ta', commit)), commit)
        self.landing.review('ta', 'b', commit, 'approve', 'Exact finished commit')
        result = self.landing.land()
        self.assertEqual(result['status'], 'landed')
        self.assertEqual((self.repo / 'a.txt').read_text(), 'finished\n')

    def test_canonical_marker_replacement_blocks_submit_and_land(self):
        self.project()
        commit = self.commit(self.a, 'a.txt', 'finished\n')
        self.complete('ta', 'a', commit)
        self.landing.submit('ta', 'a', commit)
        self.landing.review('ta', 'b', commit, 'approve', 'Exact finished commit')
        (self.landing.common / 'pstack-project.json').write_text(json.dumps({'project_database': str(self.root / 'other.sqlite')}), encoding='utf-8')
        for call in (self.landing.land, lambda: self.landing.submit('ta', 'a', commit)):
            with self.assertRaisesRegex(ValueError, 'canonical'):
                call()
        self.unchanged()

    def test_late_legacy_authority_blocks_submit_and_land(self):
        commit = self.approved_a()
        git(self.repo, 'update-ref', 'refs/landing/main', self.baseline)
        for call in (self.landing.land, lambda: self.landing.submit('ta', 'a', commit)):
            with self.assertRaisesRegex(ValueError, 'competing'):
                call()
        self.unchanged()

    def test_cancellation_between_checks_and_advancement_is_fenced(self):
        self.approved_a()
        original = self.landing._land_checked
        def cancel(row, recovered=False):
            self.flag('cancelled')
            return original(row, recovered)
        with patch.object(self.landing, '_land_checked', cancel):
            with self.assertRaisesRegex(ValueError, 'not running'):
                self.landing.land()
        self.assertEqual(self.landing.status()[0]['status'], 'checked')
        self.unchanged()

    def test_reassignment_between_checks_and_advancement_is_fenced(self):
        self.approved_a()
        original = self.landing._land_checked
        def reassign(row, recovered=False):
            with self.board.transaction() as db:
                db.execute('UPDATE tasks SET attempts=attempts+1 WHERE id=?', ('ta',))
            return original(row, recovered)
        with patch.object(self.landing, '_land_checked', reassign):
            with self.assertRaisesRegex(ValueError, 'superseded'):
                self.landing.land()
        self.assertEqual(self.landing.status()[0]['status'], 'checked')
        self.unchanged()

    def test_competition_between_checks_and_advancement_is_fenced(self):
        self.approved_a()
        original = self.landing._land_checked
        def compete(row, recovered=False):
            git(self.repo, 'update-ref', 'refs/landing/main', self.baseline)
            return original(row, recovered)
        with patch.object(self.landing, '_land_checked', compete):
            with self.assertRaisesRegex(ValueError, 'competing'):
                self.landing.land()
        self.unchanged()

    def test_approval_revocation_between_checks_and_advancement_is_fenced(self):
        commit = self.approved_a()
        original = self.landing._land_checked
        def revoke(row, recovered=False):
            self.landing.review('ta', 'b', commit, 'reject', 'Found a regression')
            return original(row, recovered)
        with patch.object(self.landing, '_land_checked', revoke):
            result = self.landing.land()
        self.assertEqual(result, {'status': 'awaiting_review', 'submission_id': 1})
        self.unchanged()

    def test_old_schema_migrates_without_inheriting_unknown_attempt_approval(self):
        commit = self.approved_a()
        with self.board.transaction() as db:
            db.execute('ALTER TABLE submissions DROP COLUMN task_attempt')
        queue = Landing(self.board.path, self.repo)
        self.assertIsNone(queue.status()[0]['task_attempt'])
        with self.assertRaisesRegex(ValueError, 'fenced'):
            queue.land()
        renewed = queue.submit('ta', 'a', commit)
        self.assertEqual(renewed['task_attempt'], 1)
        self.assertEqual(queue.land(), {'status': 'awaiting_review', 'submission_id': 1})
        queue.review('ta', 'b', commit, 'approve', 'Renewed exact-commit review')
        self.assertEqual(queue.land()['status'], 'landed')
        self.assertEqual((self.repo / 'a.txt').read_text(), 'author a\n')

    def test_one_peer_two_units_uses_retained_artifacts_and_bounded_registry(self):
        first = self.commit(self.a, 'a.txt', 'first\n')
        self.complete('ta', 'a', first)
        self.landing.submit('ta', 'a', first)
        self.landing.review('ta', 'b', first, 'approve', 'First exact commit')
        git(self.a, 'reset', '--hard', self.baseline)
        with self.board.transaction() as db:
            db.execute('UPDATE tasks SET expires=? WHERE id=?', (time.time() - 1, 'tb'))
            db.execute('INSERT INTO run_config VALUES(?,?)', ('max_peers', json.dumps(3)))
        self.board.claim('a', task_id='tb')
        second = self.commit(self.a, 'b.txt', 'second\n')
        self.complete('tb', 'a', second)
        self.landing.submit('tb', 'a', second)
        self.landing.review('tb', 'b', second, 'approve', 'Second exact commit')
        self.assertEqual(self.landing.land()['status'], 'landed')
        self.assertEqual(self.landing.land()['status'], 'landed')
        report = verify(self.board.path, self.root / 'no-exports')
        checks = {c['name']: c['status'] for c in report['checks']}
        self.assertEqual(checks['registry.count'], 'PASS')
        self.assertEqual(checks['task.ta.authored'], 'PASS')
        self.assertEqual(checks['task.tb.authored'], 'PASS')
        self.assertEqual((self.repo / 'a.txt').read_text(), 'first\n')
        self.assertEqual((self.repo / 'b.txt').read_text(), 'second\n')
        git(self.repo, 'update-ref', '-d', artifact_ref(self.board.path, 'ta', first))
        self.project()
        report = verify(self.board.path, self.root / 'no-exports')
        checks = {c['name']: c['status'] for c in report['checks']}
        self.assertEqual(checks['task.ta.authored'], 'ISSUES')
        self.assertEqual(checks['task.tb.authored'], 'PASS')
        with self.board.transaction() as db:
            db.execute('UPDATE tasks SET attempts=attempts+1 WHERE id=?', ('tb',))
        report = verify(self.board.path, self.root / 'no-exports')
        checks = {c['name']: c['status'] for c in report['checks']}
        self.assertEqual(checks['task.ta.landed'], 'PASS')
        self.assertEqual(checks['task.tb.landed'], 'ISSUES')

    def test_capacity_busy_kernel_release_and_child_exit_code(self):
        wrapper = SOURCE / 't3/added/mesh/scripts/check_slot.py'
        root = self.root / 'capacity'
        prefix = [sys.executable, str(wrapper), '--state-root', str(root), '--', sys.executable, '-u', '-c']
        held = subprocess.Popen(prefix + ['import sys; print("held", flush=True); sys.stdin.readline()'],
                                text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: held.poll() is None and held.kill())
        self.assertEqual(held.stdout.readline().strip(), 'held')
        blocked = subprocess.run(prefix + ['print("must not run")'], text=True, capture_output=True)
        self.assertEqual(blocked.returncode, 75)
        self.assertEqual(blocked.stdout, '')
        self.assertIn('slot is busy', blocked.stderr)
        self.approved_a()
        with self.assertRaisesRegex(ValueError, 'holds the Git lock'):
            self.landing.land()
        self.unchanged()
        held.kill()
        held.communicate(timeout=10)
        released = subprocess.run(prefix + ['print("released"); raise SystemExit(7)'], text=True, capture_output=True)
        self.assertEqual(released.returncode, 7)
        self.assertEqual(released.stdout, 'released\n')
        self.assertEqual(self.landing.land()['status'], 'landed')
        self.assertEqual((self.repo / 'a.txt').read_text(), 'author a\n')


if __name__ == '__main__':
    unittest.main(verbosity=2)
