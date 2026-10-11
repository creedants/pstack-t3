import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 't3/added/project/scripts/project.py'
spec = importlib.util.spec_from_file_location('project_runtime', SCRIPT)
project = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = project
spec.loader.exec_module(project)
sys.path.insert(0, str(ROOT / 't3/added/mesh/scripts'))
from pstack_mesh.board import Board
from pstack_mesh.landing import Landing


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True, stderr=subprocess.DEVNULL).strip()


class RoutingTest(unittest.TestCase):
    def route(self, **facts):
        return project.choose(project.TaskFacts.parse(facts))

    def test_small_followup_retains_project_without_workers(self):
        result = self.route(kind='bug', small=True, scope_known=True)
        self.assertEqual((result.context, result.strategy, result.workers, result.mode), ('project', 'direct', 0, 'light'))

    def test_single_unknown_feature_uses_one_worker(self):
        result = self.route()
        self.assertEqual((result.strategy, result.workers, result.mode, result.playbook), ('delegate', 1, 'full', 'feature'))

    def test_finite_coverage_and_contextful_mesh_are_distinct(self):
        a = self.route(continuing=False, independent_units=4)
        b = self.route(independent_units=4, retained_expertise=True)
        self.assertEqual((a.context, a.strategy, a.workers), ('goal', 'swarm', 3))
        self.assertEqual((b.context, b.strategy, b.workers), ('project', 'mesh', 3))

    def test_candidate_comparison_and_recurring_intake_keep_their_modes(self):
        self.assertEqual(self.route(compare_candidates=True).strategy, 'arena')
        self.assertEqual(self.route(recurring_intake=True, brigade_available=True).strategy, 'brigade')
        self.assertEqual(self.route(recurring_intake=True, brigade_available=False).strategy, 'delegate')
        with self.assertRaises(ValueError):
            self.route(explicit_strategy='brigade', brigade_available=False)

    def test_explicit_choice_and_opt_out_win(self):
        result = self.route(explicit_strategy='mesh', explicit_mode='full', small=True, scope_known=True)
        self.assertEqual((result.strategy, result.mode, result.workers), ('mesh', 'full', 2))
        result = self.route(explicit_strategy='mesh', opt_out=True)
        self.assertEqual((result.strategy, result.workers, result.playbook), ('direct', 0, None))

    def test_risk_escalates_rigor_without_silently_creating_a_mesh(self):
        for facts in ({'high_risk': True}, {'contested': True}, {'send_backs': 2}):
            result = self.route(small=True, scope_known=True, explicit_mode='light', **facts)
            self.assertEqual(result.mode, 'full')
            self.assertNotEqual(result.strategy, 'mesh')

    def test_task_facts_reject_ambiguous_types_and_insufficient_budget(self):
        for facts in ({'small': 'yes'}, {'independent_units': True}, {'max_workers': 30},
                      {'explicit_strategy': 'invented'}, {'kind': 'invented'}):
            with self.assertRaises(ValueError):
                self.route(**facts)
        with self.assertRaises(ValueError):
            self.route(explicit_strategy='mesh', max_workers=1)

    def test_existing_playbooks_remain_selectable(self):
        self.assertEqual(self.route(kind='hillclimb').playbook, 'hillclimb')
        self.assertEqual(self.route(kind='autopilot-stack').playbook, 'autopilot-stack')

    def test_real_json_file_cli_requires_no_repository_for_an_answer(self):
        with tempfile.TemporaryDirectory() as directory:
            arguments = Path(directory) / 'facts.json'
            arguments.write_text(json.dumps({'kind': 'answer', 'writes': False, 'small': True, 'scope_known': True}), encoding='utf-8')
            call = subprocess.run([sys.executable, str(SCRIPT), 'route', '--data', '@' + str(arguments)],
                                  cwd=directory, text=True, capture_output=True)
            self.assertEqual(call.returncode, 0, call.stderr)
            result = json.loads(call.stdout)
            self.assertEqual((result['strategy'], result['workers']), ('direct', 0))


class ProjectTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.capacity = patch.dict(os.environ, {'PSTACK_MESH_CAPACITY_STATE': str(self.root / 'capacity')})
        self.capacity.start()
        self.addCleanup(self.capacity.stop)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        git(self.repo, 'init', '-b', 'main')
        git(self.repo, 'config', 'user.name', 'Test')
        git(self.repo, 'config', 'user.email', 'test@example.invalid')
        git(self.repo, 'config', 'commit.gpgsign', 'false')
        git(self.repo, 'config', 'core.autocrlf', 'false')
        (self.repo / 'README.md').write_text('Project\n', encoding='utf-8')
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-m', 'Baseline')
        self.base = git(self.repo, 'rev-parse', 'HEAD')
        self.store = project.Project(self.repo, self.root / 'state')
        self.store.open('native-project', 'interface-thread', 'Maintain this project')

    def goal(self, key='one', **facts):
        return self.store.accept(key, 'Requested change', ['Observed behavior passes'], facts)

    def mesh(self, key='one'):
        goal = self.goal(key, independent_units=2, peer_coordination=True)
        tasks = [{'id': 'work', 'title': 'Write an answer', 'paths': ['answer.txt']}]
        check = [sys.executable, '-c', "from pathlib import Path; assert Path('answer.txt').read_text() == 'hello'"]
        run = self.store.start_mesh(goal['id'], tasks, check)
        return goal, run, tasks, check

    def peer(self, id='writer'):
        workspace = self.root / id
        git(self.repo, 'worktree', 'add', '-b', 'peer/' + id, str(workspace), self.base)
        self.store.peer(id, 'thread-' + id, workspace, ['tests'])
        return workspace

    def test_context_survives_new_instances_and_keeps_one_interface(self):
        self.store.remember('tests', 'Run behavioral checks', 'User request')
        goal = self.goal()
        reopened = project.Project(self.repo, self.root / 'state')
        interface = reopened.open('different-project', 'other-thread', 'Different purpose')
        self.assertEqual(interface['interface_thread'], 'interface-thread')
        self.assertEqual(reopened.context()['project']['purpose'], 'Maintain this project')
        self.assertEqual(json.loads(reopened.context()['facts'][0]['value']), 'Run behavioral checks')
        self.assertEqual(reopened.context()['goals'][0]['id'], goal['id'])

    def test_facts_have_provenance_and_compare_and_set(self):
        self.store.remember('scope', ['source'], 'User', expected_revision=0)
        with self.assertRaises(ValueError):
            self.store.remember('scope', ['other'], 'Peer', expected_revision=0)
        self.store.remember('scope', ['source', 'tests'], 'User correction', expected_revision=1)
        self.assertEqual(self.store.context()['facts'][0]['revision'], 2)

    def test_followups_are_new_goals_in_same_project(self):
        one = self.goal('one')
        self.assertEqual(self.goal('one')['id'], one['id'])
        two = self.goal('two')
        self.assertNotEqual(one['id'], two['id'])
        with self.assertRaises(ValueError):
            self.store.accept('one', 'Changed request', ['Pass'], {})

    def test_worktree_context_uses_same_canonical_project(self):
        workspace = self.peer()
        same = project.Project(workspace, self.root / 'state')
        self.assertEqual(same.database, self.store.database)
        self.assertEqual(same.context()['project']['interface_thread'], 'interface-thread')

    def test_state_inside_repo_and_foreign_or_rebound_peers_fail(self):
        with self.assertRaises(ValueError):
            project.Project(self.repo, self.repo / 'state')
        with self.assertRaises(ValueError):
            self.store.peer('bad', 'thread-bad', self.repo)
        workspace = self.peer()
        with self.assertRaises(ValueError):
            self.store.peer('writer', 'replacement-thread', workspace)

    def test_mesh_configuration_is_fixed_and_only_one_writing_goal_runs(self):
        goal, run, tasks, check = self.mesh()
        again = self.store.start_mesh(goal['id'], tasks, check)
        self.assertEqual(again['board'], run['board'])
        with self.assertRaises(ValueError):
            self.store.start_mesh(goal['id'], tasks, [sys.executable, '-c', 'pass'])
        second = self.goal('two', independent_units=2, peer_coordination=True)
        with self.assertRaises(ValueError):
            self.store.start_mesh(second['id'], tasks, check)

    def test_dependency_order_is_independent_of_input_order(self):
        goal = self.goal(independent_units=2, peer_coordination=True)
        tasks = [{'id': 'child', 'title': 'Child', 'paths': ['child.txt'], 'dependencies': ['parent']},
                 {'id': 'parent', 'title': 'Parent', 'paths': ['parent.txt']}]
        run = self.store.start_mesh(goal['id'], tasks, [sys.executable, '-c', 'pass'])
        board = Board(run['board'])
        workspace = self.peer()
        board.register('writer', 'thread-writer', workspace)
        self.assertEqual(board.claim('writer')['id'], 'parent')

    def test_cyclic_or_overlapping_or_escaping_scopes_are_refused(self):
        goal = self.goal(independent_units=2, peer_coordination=True)
        bad = [[{'id': 'a', 'title': 'A', 'paths': ['../outside']}],
               [{'id': 'a', 'title': 'A', 'paths': ['same']}, {'id': 'b', 'title': 'B', 'paths': ['same']}],
               [{'id': 'a', 'title': 'A', 'paths': ['a'], 'dependencies': ['b']},
                {'id': 'b', 'title': 'B', 'paths': ['b'], 'dependencies': ['a']}]]
        for tasks in bad:
            with self.assertRaises(ValueError):
                self.store.start_mesh(goal['id'], tasks, [sys.executable, '-c', 'pass'])

    def test_an_existing_landing_authority_is_not_bypassed(self):
        git(self.repo, 'update-ref', 'refs/landing/main', self.base)
        with self.assertRaisesRegex(ValueError, 'already has a pstack landing authority'):
            self.mesh()

    def test_empty_initialized_pstack_queue_is_detected_without_git_refs(self):
        state = self.root / 'legacy-state'
        common = self.store.common
        import hashlib
        import re
        name = common.parent.name if common.name == '.git' else common.name.removesuffix('.git')
        key = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-') + '-' + hashlib.sha1(str(common).encode()).hexdigest()[:8]
        database = state / 'pstack-t3/landing' / key / 'land.db'
        database.parent.mkdir(parents=True)
        database.touch()
        with patch.dict('os.environ', {'XDG_STATE_HOME': str(state)}):
            with self.assertRaisesRegex(ValueError, 'already has a pstack landing authority'):
                self.mesh()

    def test_cancellation_fences_old_claims_and_allows_followup(self):
        goal, run, _, _ = self.mesh()
        workspace = self.peer()
        board = Board(run['board'])
        board.register('writer', 'thread-writer', workspace)
        claim = board.claim('writer')
        self.assertEqual(self.store.cancel(goal['id'], 'User cancelled this goal')['state'], 'cancelled')
        with self.assertRaises(ValueError):
            board.finish('work', 'writer', claim['token'], {'status': 'PASS', 'commit': self.base})
        with self.assertRaisesRegex(ValueError, 'not running'):
            board.claim('writer')
        followup = self.goal('two', independent_units=2, peer_coordination=True)
        next_run = self.store.start_mesh(followup['id'], [{'id': 'next', 'title': 'Next', 'paths': ['next.txt']}], [sys.executable, '-c', 'pass'])
        self.assertNotEqual(next_run['board'], run['board'])

    def test_state_inside_any_peer_worktree_is_refused(self):
        workspace = self.peer()
        with self.assertRaises(ValueError):
            project.Project(self.repo, workspace / 'state')

    def test_prepare_refuses_active_dirty_or_already_authored_peer(self):
        goal, run, _, _ = self.mesh()
        workspace = self.peer()
        observed = {'threadId': 'thread-writer', 'status': 'completed', 'activeRunId': None,
                    'branch': 'peer/writer', 'worktreePath': str(workspace)}
        with self.assertRaises(ValueError):
            self.store.prepare('writer', goal['id'], dict(observed, status='running'))
        (workspace / 'uncommitted.txt').write_text('Keep me')
        with self.assertRaises(ValueError):
            self.store.prepare('writer', goal['id'], observed)
        (workspace / 'uncommitted.txt').unlink()
        prepared = self.store.prepare('writer', goal['id'], observed)
        self.assertEqual(prepared['baseline'], self.base)
        board = Board(run['board'])
        board.register('writer', 'thread-writer', workspace)
        board.claim('writer')
        with self.assertRaises(ValueError):
            self.store.prepare('writer', goal['id'], observed)

    def test_real_peer_review_and_checked_landing_complete_goal(self):
        goal, run, _, _ = self.mesh()
        writer, reviewer = self.peer('writer'), self.peer('reviewer')
        board = Board(run['board'])
        board.register('writer', 'thread-writer', writer)
        board.register('reviewer', 'thread-reviewer', reviewer)
        claim = board.claim('writer')
        (writer / 'answer.txt').write_text('hello', encoding='utf-8')
        git(writer, 'add', 'answer.txt')
        git(writer, 'commit', '-m', 'Answer')
        commit = git(writer, 'rev-parse', 'HEAD')
        board.finish('work', 'writer', claim['token'], {'status': 'PASS', 'commit': commit})
        with self.assertRaises(ValueError):
            self.store.complete(goal['id'], {'status': 'PASS', 'artifact': 'answer.txt'})
        landing = Landing(run['board'], self.repo)
        landing.submit('work', 'writer', commit)
        landing.review('work', 'reviewer', commit, 'approve', {'observed': 'Exact answer file contains hello'})
        self.assertEqual(landing.land()['status'], 'landed')
        self.assertEqual((self.repo / 'answer.txt').read_text(), 'hello')
        self.assertEqual(self.store.complete(goal['id'], {'status': 'PASS', 'artifact': 'answer.txt'})['state'], 'complete')
        followup = self.goal('two', independent_units=2, retained_expertise=True)
        next_run = self.store.start_mesh(followup['id'], [{'id': 'next', 'title': 'Follow-up', 'paths': ['next.txt']}],
                                         [sys.executable, '-c', 'pass'])
        self.assertNotEqual(next_run['board'], run['board'])
        observed = {'threadId': 'thread-writer', 'status': 'completed', 'activeRunId': None,
                    'branch': 'peer/writer', 'worktreePath': str(writer)}
        self.store.prepare('writer', followup['id'], observed)
        self.assertEqual(git(writer, 'rev-parse', 'HEAD'), next_run['baseline'])
        self.assertEqual(len(self.store.context()['peers']), 2)
        references = git(writer, 'for-each-ref', '--format=%(objectname)', 'refs/pstack/peer-history')
        self.assertIn(commit, references)


if __name__ == '__main__':
    unittest.main()
