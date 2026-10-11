import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / 'tests'))
sys.path.insert(0, str(SOURCE / 't3/added/mesh/scripts'))
import test_mesh_landing as landing_fixtures
import test_project_fencing as project_fixtures
from pstack_mesh.board import Board
from pstack_mesh.landing import Landing, git
import pstack_mesh.landing as landing_module


class TargetQueueFixes(unittest.TestCase):
    setUp = landing_fixtures.LandingTest.setUp
    commit = landing_fixtures.LandingTest.commit
    approved_a = landing_fixtures.LandingTest.approved_a
    def branches_unchanged(self):
        self.assertEqual(git(self.repo, 'rev-parse', 'refs/heads/main'), self.baseline)
        self.assertEqual(git(self.repo, 'rev-parse', 'refs/heads/other'), self.baseline)
        self.assertEqual(self.landing.status()[0]['status'], 'checked')

    def test_switch_before_final_transaction_advances_neither_branch(self):
        self.approved_a()
        git(self.repo, 'branch', 'other', self.baseline)
        original = self.landing._land_checked

        def switched(row, recovered=False):
            git(self.repo, 'checkout', 'other')
            return original(row, recovered)

        with patch.object(self.landing, '_land_checked', side_effect=switched):
            with self.assertRaisesRegex(ValueError, 'Target branch changed'):
                self.landing.land()
        self.branches_unchanged()
        self.assertEqual((self.repo / 'a.txt').read_text(), 'baseline\n')

    def test_switch_after_final_guard_before_prepare_advances_neither_branch(self):
        self.approved_a()
        git(self.repo, 'branch', 'other', self.baseline)
        original = self.landing._advance

        def switched(candidate):
            git(self.repo, 'checkout', 'other')
            return original(candidate)

        with patch.object(self.landing, '_advance', side_effect=switched):
            with self.assertRaisesRegex(ValueError, 'Target branch changed'):
                self.landing.land()
        self.branches_unchanged()
        self.assertFalse((self.landing.common / 'HEAD.lock').exists())
        self.assertFalse((self.landing.common / 'refs/heads/other.lock').exists())

    def test_prepared_locks_block_actual_checkout_through_worktree_write(self):
        self.approved_a()
        git(self.repo, 'branch', 'other', self.baseline)
        original = landing_module.git
        attempts = []

        def guarded(path, *args):
            if Path(path) == self.repo and args[:1] == ('read-tree',):
                for after in (False, True):
                    if after:
                        result = original(path, *args)
                    self.assertTrue((self.landing.common / 'HEAD.lock').exists())
                    self.assertTrue((self.landing.common / 'refs/heads/main.lock').exists())
                    checkout = subprocess.run(['git', '-C', str(self.repo), 'checkout', 'other'], capture_output=True, text=True)
                    self.assertNotEqual(checkout.returncode, 0)
                    self.assertIn('HEAD.lock', checkout.stderr)
                    self.assertEqual(original(path, 'symbolic-ref', 'HEAD'), 'refs/heads/main')
                    self.assertEqual(original(path, 'rev-parse', 'main'), self.baseline)
                    attempts.append(checkout.returncode)
                return result
            return original(path, *args)

        with patch.object(landing_module, 'git', side_effect=guarded):
            result = self.landing.land()
        self.assertEqual(result['status'], 'landed')
        self.assertEqual(len(attempts), 2)
        self.assertEqual(git(self.repo, 'rev-parse', 'main'), result['candidate'])
        self.assertEqual(git(self.repo, 'rev-parse', 'other'), self.baseline)
        self.assertEqual(git(self.repo, 'status', '--porcelain'), '')
        self.assertEqual((self.repo / 'a.txt').read_text(), 'author a\n')

    def test_edit_after_clean_guard_is_preserved_without_ref_advancement(self):
        self.approved_a()
        original = landing_module.git

        def edited(path, *args):
            if Path(path) == self.repo and args[:1] == ('read-tree',):
                (self.repo / 'a.txt').write_text('concurrent edit\n')
            return original(path, *args)

        with patch.object(landing_module, 'git', side_effect=edited):
            with self.assertRaises(ValueError):
                self.landing.land()
        self.assertEqual(git(self.repo, 'rev-parse', 'main'), self.baseline)
        self.assertEqual((self.repo / 'a.txt').read_text(), 'concurrent edit\n')
        self.assertEqual(self.landing.status()[0]['status'], 'checked')
        self.assertFalse((self.landing.common / 'HEAD.lock').exists())
        self.assertFalse((self.landing.common / 'refs/heads/main.lock').exists())

    def interrupted_write(self):
        self.approved_a()
        original = landing_module.git

        def interrupted(path, *args):
            result = original(path, *args)
            if Path(path) == self.repo and args[:1] == ('read-tree',):
                raise SystemExit('interrupted after candidate worktree write')
            return result

        with patch.object(landing_module, 'git', side_effect=interrupted):
            with self.assertRaisesRegex(SystemExit, 'candidate worktree write'):
                self.landing.land()
        row = self.landing.status()[0]
        self.assertEqual(row['status'], 'checked')
        self.assertEqual(git(self.repo, 'rev-parse', 'main'), self.baseline)
        self.assertEqual(git(self.repo, 'write-tree'), git(self.repo, 'rev-parse', row['candidate_sha'] + '^{tree}'))
        self.assertEqual((self.repo / 'a.txt').read_text(), 'author a\n')
        self.assertFalse((self.landing.common / 'HEAD.lock').exists())
        self.assertFalse((self.landing.common / 'refs/heads/main.lock').exists())
        return row

    def test_checked_candidate_recovers_after_interrupted_worktree_write(self):
        row = self.interrupted_write()
        result = Landing(self.board.path, self.repo).land()
        self.assertEqual(result['status'], 'landed')
        self.assertEqual(result['candidate'], row['candidate_sha'])
        self.assertEqual(git(self.repo, 'rev-parse', 'main'), row['candidate_sha'])
        self.assertEqual(git(self.repo, 'status', '--porcelain'), '')
        self.assertEqual(self.landing.land(), {'status': 'idle'})

    def test_process_death_aborts_prepared_refs_and_recovers_candidate_tree(self):
        self.approved_a()
        code = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import pstack_mesh.landing as module
original = module.git
def interrupted(path, *args):
    result = original(path, *args)
    if Path(path) == Path(sys.argv[3]) and args[:1] == ('read-tree',):
        print('candidate written', flush=True)
        sys.stdin.readline()
    return result
module.git = interrupted
module.Landing(sys.argv[2], sys.argv[3]).land()
"""
        with subprocess.Popen([sys.executable, '-B', '-u', '-c', code,
                               str(SOURCE / 't3/added/mesh/scripts'), str(self.board.path), str(self.repo)],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
            try:
                self.assertEqual(process.stdout.readline().strip(), 'candidate written')
                self.assertTrue((self.landing.common / 'HEAD.lock').exists())
                self.assertTrue((self.landing.common / 'refs/heads/main.lock').exists())
            finally:
                process.terminate()
                process.wait(timeout=10)
        deadline = time.monotonic() + 5
        while (self.landing.common / 'HEAD.lock').exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse((self.landing.common / 'HEAD.lock').exists())
        self.assertFalse((self.landing.common / 'refs/heads/main.lock').exists())
        self.assertEqual(git(self.repo, 'rev-parse', 'main'), self.baseline)
        self.assertEqual(self.landing.status()[0]['status'], 'checked')
        self.assertEqual((self.repo / 'a.txt').read_text(), 'author a\n')
        result = self.landing.land()
        self.assertEqual(result['status'], 'landed')
        self.assertEqual(git(self.repo, 'rev-parse', 'main'), result['candidate'])
        self.assertEqual(git(self.repo, 'status', '--porcelain'), '')

    def test_interrupted_candidate_recovery_refuses_unstaged_content(self):
        self.interrupted_write()
        (self.repo / 'a.txt').write_text('unrelated edit\n')
        with self.assertRaisesRegex(ValueError, 'dirty'):
            self.landing.land()
        self.assertEqual(git(self.repo, 'rev-parse', 'main'), self.baseline)
        self.assertEqual((self.repo / 'a.txt').read_text(), 'unrelated edit\n')
        self.assertEqual(self.landing.status()[0]['status'], 'checked')

    def test_interrupted_candidate_recovery_refuses_other_index(self):
        self.interrupted_write()
        (self.repo / 'a.txt').write_text('another staged tree\n')
        git(self.repo, 'add', 'a.txt')
        with self.assertRaisesRegex(ValueError, 'dirty'):
            self.landing.land()
        self.assertEqual(git(self.repo, 'rev-parse', 'main'), self.baseline)
        self.assertEqual((self.repo / 'a.txt').read_text(), 'another staged tree\n')

    def test_interrupted_candidate_recovery_refuses_untracked_files(self):
        self.interrupted_write()
        (self.repo / 'extra').write_text('untracked\n')
        with self.assertRaisesRegex(ValueError, 'dirty'):
            self.landing.land()
        self.assertEqual(git(self.repo, 'rev-parse', 'main'), self.baseline)
        self.assertEqual((self.repo / 'extra').read_text(), 'untracked\n')

    def test_interrupted_candidate_recovery_requires_original_target_branch(self):
        self.interrupted_write()
        git(self.repo, 'branch', 'other', self.baseline)
        git(self.repo, 'checkout', 'other')
        with self.assertRaisesRegex(ValueError, 'target branch checked out'):
            self.landing.land()
        self.branches_unchanged()

    def test_recovery_after_ref_commit_rechecks_branch_under_lock(self):
        self.approved_a()
        original = self.landing._advance

        def interrupted(candidate):
            original(candidate)
            raise SystemExit('interrupted after commit')

        with patch.object(self.landing, '_advance', side_effect=interrupted):
            with self.assertRaises(SystemExit):
                self.landing.land()
        candidate = git(self.repo, 'rev-parse', 'main')
        git(self.repo, 'branch', 'other', candidate)
        original_checked = self.landing._land_checked

        def switched(row, recovered=False):
            self.assertTrue(recovered)
            git(self.repo, 'checkout', 'other')
            return original_checked(row, recovered)

        with patch.object(self.landing, '_land_checked', side_effect=switched):
            with self.assertRaisesRegex(ValueError, 'Target branch changed'):
                self.landing.land()
        self.assertEqual(self.landing.status()[0]['status'], 'checked')
        self.assertEqual(git(self.repo, 'rev-parse', 'main'), candidate)
        self.assertEqual(git(self.repo, 'rev-parse', 'other'), candidate)
        git(self.repo, 'checkout', 'main')
        self.assertTrue(self.landing.land()['recovered'])

    def test_changed_target_oid_before_prepare_fails_compare_and_swap(self):
        self.approved_a()
        original = self.landing._advance
        replacement = git(self.repo, 'commit-tree', git(self.repo, 'rev-parse', 'HEAD^{tree}'), '-p', self.baseline, '-m', 'external commit')

        def changed(candidate):
            git(self.repo, 'update-ref', 'refs/heads/main', replacement, self.baseline)
            return original(candidate)

        with patch.object(self.landing, '_advance', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'expected'):
                self.landing.land()
        self.assertEqual(git(self.repo, 'rev-parse', 'main'), replacement)
        self.assertEqual(self.landing.status()[0]['status'], 'checked')
        self.assertEqual((self.repo / 'a.txt').read_text(), 'baseline\n')


class TargetProjectFixes(unittest.TestCase):
    setUp = project_fixtures.ProjectFixes.setUp
    start = project_fixtures.ProjectFixes.start
    peer = project_fixtures.ProjectFixes.peer
    observe = project_fixtures.ProjectFixes.observe
    author = project_fixtures.ProjectFixes.author
    interrupt_publication = project_fixtures.ProjectFixes.interrupt_publication
    def landed_goal(self):
        run = self.start()
        board = Board(run['board'])
        path = self.peer(board)
        self.peer(board, 'x')
        claim = board.claim('w')
        commit = self.author(board, path, 'a', claim)
        queue = Landing(run['board'], self.repo)
        queue.submit('a', 'w', commit)
        queue.review('a', 'x', commit, 'approve', 'Exact authored commit')
        result = queue.land()
        self.assertEqual(result['status'], 'landed')
        return board, result['candidate']

    def test_unstarted_mesh_completion_keeps_goal_accepted(self):
        with self.assertRaisesRegex(ValueError, 'running goal with a started board'):
            self.store.complete(self.goal['id'], {'status': 'PASS', 'artifact': 'review://fixture'})
        goal = self.store.context()['goals'][0]
        self.assertEqual(goal['state'], 'accepted')
        self.assertIsNone(goal['board'])
        self.assertEqual(git(self.repo, 'rev-parse', 'main'), self.base)

    def test_completion_from_other_branch_checks_frozen_target(self):
        board, candidate = self.landed_goal()
        git(self.repo, 'checkout', '-b', 'other', self.base)
        result = self.store.complete(self.goal['id'], {'status': 'PASS', 'artifact': 'review://candidate'})
        self.assertEqual(result['state'], 'complete')
        self.assertEqual(git(self.repo, 'rev-parse', 'HEAD'), self.base)
        self.assertEqual(git(self.repo, 'rev-parse', 'refs/heads/main'), candidate)

    def test_completion_refuses_candidate_only_on_other_branch(self):
        board, candidate = self.landed_goal()
        git(self.repo, 'checkout', '-b', 'other', candidate)
        git(self.repo, 'update-ref', 'refs/heads/main', self.base, candidate)
        with self.assertRaises(ValueError):
            self.store.complete(self.goal['id'], {'status': 'PASS', 'artifact': 'review://candidate'})
        self.assertEqual(self.store.context()['goals'][0]['state'], 'running')
        self.assertEqual(git(self.repo, 'rev-parse', 'HEAD'), candidate)
        self.assertEqual(git(self.repo, 'rev-parse', 'main'), self.base)
        with board.connect() as db:
            self.assertEqual(json.loads(db.execute("SELECT value FROM run_config WHERE key='goal_state'").fetchone()[0]), 'running')

    def test_mesh_with_board_requires_running_project_lifecycle(self):
        board, candidate = self.landed_goal()
        with self.store.db(True) as db:
            db.execute("UPDATE goals SET state='accepted' WHERE id=?", (self.goal['id'],))
        with self.assertRaisesRegex(ValueError, 'running goal'):
            self.store.complete(self.goal['id'], {'status': 'PASS', 'artifact': 'review://candidate'})
        self.assertEqual(self.store.context()['goals'][0]['state'], 'accepted')

    def test_direct_completion_evidence_contract_is_unchanged(self):
        goal = self.store.accept('direct', 'Direct task', ['Reviewable result'], {'small': True, 'scope_known': True})
        self.assertEqual(json.loads(goal['plan'])['strategy'], 'direct')
        evidence = {'status': 'PASS', 'artifact': 'review://direct'}
        self.assertEqual(self.store.complete(goal['id'], evidence)['state'], 'complete')
        self.assertEqual(self.store.complete(goal['id'], evidence)['state'], 'complete')


def suite():
    tests = unittest.TestSuite()
    for cls in (TargetQueueFixes, TargetProjectFixes):
        tests.addTests(cls(name) for name in cls.__dict__ if name.startswith('test_'))
    tests.addTest(landing_fixtures.LandingTest('test_restart_after_main_advance_recovers_without_second_merge'))
    tests.addTest(landing_fixtures.LandingTest('test_revocation_during_check_prevents_main_advancement'))
    tests.addTest(project_fixtures.ProjectFixes('test_completed_board_recovers_interrupted_project_completion'))
    tests.addTest(project_fixtures.ProjectFixes('test_four_tasks_three_peers_self_prepare_and_preserve_all_commits'))
    return tests


if __name__ == '__main__':
    print(subprocess.check_output(['git', '--version'], text=True).strip(), flush=True)
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite()).wasSuccessful())
