"""Behavior checks for the bundled peer mesh."""
import os
from pathlib import Path
import sys

MESH_SCRIPTS = Path(__file__).resolve().parents[1] / 't3/added/mesh/scripts'
sys.path.insert(0, str(MESH_SCRIPTS))
MESH_ENV = {**os.environ, 'PYTHONPATH': str(MESH_SCRIPTS) + os.pathsep + os.environ.get('PYTHONPATH', '')}

import json
from datetime import datetime, timezone
import subprocess
import tempfile
import unittest

from pstack_mesh.board import Board
from pstack_mesh.verify import git, verify


class VerifierTest(unittest.TestCase):
    """Synthetic observer records exercise validation; Git objects are real."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        git(self.repo, 'init', '-b', 'main')
        git(self.repo, 'config', 'user.email', 'mesh@example.test')
        git(self.repo, 'config', 'user.name', 'Mesh test')
        (self.repo / 'baseline').write_text('baseline')
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-m', 'baseline')
        self.baseline = git(self.repo, 'rev-parse', 'HEAD')
        self.db = self.root / 'board.sqlite'
        self.board = Board(self.db)
        self.exports = self.root / 'thread-exports'
        self.exports.mkdir()
        self.raw = {}
        self.command = [sys.executable, '-c', 'print("checked")']
        with self.board.transaction() as db:
            db.executescript('''
                CREATE TABLE run_config(key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE submissions(id INTEGER PRIMARY KEY, task_id TEXT, author TEXT,
                    commit_sha TEXT, status TEXT, base_sha TEXT, candidate_sha TEXT,
                    attempts INTEGER, evidence TEXT, created REAL);
                CREATE TABLE reviews(submission_id INTEGER, reviewer TEXT, commit_sha TEXT,
                    verdict TEXT, evidence TEXT, created REAL);
            ''')
            for key, value in {'repository': str(self.repo), 'baseline': self.baseline,
                               'max_peers': 2, 'check_command': self.command,
                               'target_branch': 'main', 'task_scopes': {'a': ['a.txt'], 'b': ['b.txt']}}.items():
                db.execute('INSERT INTO run_config VALUES(?,?)', (key, json.dumps(value)))
        self.commits = {}
        for peer in ('a', 'b'):
            worktree = self.root / peer
            git(self.repo, 'worktree', 'add', '-b', peer, str(worktree), self.baseline)
            self.board.register(peer, f'thread-{peer}', worktree)
            self.board.record('discovery', peer, peer, {'peer_id': 'b' if peer == 'a' else 'a'})
            self.board.add(peer, 'Task', f'{peer}.txt')
            claim = self.board.claim(peer, task_id=peer)
            (worktree / f'{peer}.txt').write_text(peer)
            git(worktree, 'add', '.')
            git(worktree, 'commit', '-m', peer)
            self.commits[peer] = git(worktree, 'rev-parse', 'HEAD')
            self.board.finish(peer, peer, claim['token'], {'status': 'PASS', 'commit': self.commits[peer]})
            self.raw[peer] = {'thread': {'threadId': f'thread-{peer}', 'parentThreadId': None,
                                       'relationshipToParent': None, 'worktreePath': str(worktree)},
                              'items': [], 'hasMore': False, 'nextPosition': 0, 'recentRuns': []}
        for a, b in (('a', 'b'), ('b', 'a')):
            q = self.send(a, b, 'Which behavior rejects expired tokens?', f'{a}-q')
            self.send(b, a, 'The stale token rejection remains enforced.', f'{a}-answer')
            follow = self.send(a, b, 'You said stale token rejection. Which command proves it?', f'{a}-follow')
            self.board.record('context_reused', b, b, {'message_id': follow, 'prior_message_id': q,
                                                     'fact': 'stale token rejection'})
            self.send(b, a, 'Run the stale token rejection test with unittest.', f'{a}-response')
        base = self.baseline
        for sid, peer in enumerate(('a', 'b'), 1):
            git(self.repo, 'cherry-pick', self.commits[peer])
            candidate = git(self.repo, 'rev-parse', 'HEAD')
            result = subprocess.run(self.command, cwd=self.repo, env=MESH_ENV, capture_output=True, text=True)
            checks = {'command': self.command, 'returncode': result.returncode, 'stdout': result.stdout,
                      'stderr': result.stderr, 'base': base, 'candidate': candidate,
                      'reviews': [{'reviewer': 'b' if peer == 'a' else 'a',
                                   'commit_sha': self.commits[peer], 'verdict': 'approve'}]}
            with self.board.transaction() as db:
                db.execute('INSERT INTO submissions VALUES(?,?,?,?,?,?,?,?,?,?)',
                           (sid, peer, peer, self.commits[peer], 'landed', base, candidate, 1, json.dumps(checks), sid))
                db.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?)',
                           (sid, 'b' if peer == 'a' else 'a', self.commits[peer], 'approve',
                            json.dumps({'command': self.command, 'returncode': 0}), sid))
            self.board.record('landing_checked', peer, peer,
                              {'submission_id': sid, 'commit': self.commits[peer], 'evidence': json.dumps(checks)})
            self.board.record('landing_landed', peer, peer,
                              {'submission_id': sid, 'commit': self.commits[peer], 'candidate': candidate, 'base': base})
            base = candidate
        self.save()

    def send(self, sender, recipient, body, reservation):
        self.board.message(reservation, sender, recipient, body)
        transport = 'transport-' + reservation
        self.board.delivered(reservation, transport)
        self.board.record('message_received', recipient, recipient, {'message_id': transport})
        self.raw[recipient]['items'].append({'type': 'user_message', 'visibility': 'local',
            'sourceThreadId': f'thread-{recipient}', 'messageId': transport, 'text': body,
            'textTruncated': False, 'nextTextOffset': None,
            'updatedAt': datetime.now(timezone.utc).isoformat()})
        return transport

    def save(self):
        for peer, export in self.raw.items():
            (self.exports / f'{peer}.json').write_text(json.dumps(export))

    def check(self, report, name):
        return next(c['status'] for c in report['checks'] if c['name'] == name)

    def test_real_git_and_complete_exchange_pass(self):
        report = verify(self.db)
        self.assertEqual(report['status'], 'PASS', report)
        self.assertEqual(self.check(report, 'a.context_followup'), 'PASS')
        self.assertEqual(self.check(report, 'landing.2'), 'PASS')

    def test_terminal_state_and_self_report_cannot_replace_raw_message(self):
        self.raw['b']['thread']['status'] = 'completed'
        self.raw['b']['items'] = []
        self.save()
        self.board.record('live_verification', 'a', 'a', {'status': 'PASS', 'context_reused': True})
        report = verify(self.db)
        self.assertEqual(report['status'], 'ISSUES')
        self.assertEqual(self.check(report, 'message.a-q'), 'ISSUES')
        self.assertEqual(self.check(report, 'a.context_followup'), 'ISSUES')

    def test_context_assertion_without_specific_fact_fails(self):
        with self.board.transaction() as db:
            db.execute("UPDATE events SET data=? WHERE kind='context_reused' AND peer_id='b'",
                       (json.dumps({'message_id': 'transport-a-follow', 'prior_message_id': 'transport-a-q',
                                    'fact': 'I reused context'}),))
        report = verify(self.db)
        self.assertEqual(self.check(report, 'a.context_followup'), 'ISSUES')
        self.assertEqual(self.check(report, 'b.context_followup'), 'PASS')

    def test_received_event_is_required(self):
        with self.board.transaction() as db:
            db.execute("DELETE FROM events WHERE kind='message_received' AND data LIKE '%transport-a-follow%'")
        self.assertEqual(self.check(verify(self.db), 'a.context_followup'), 'ISSUES')

    def test_truncated_and_paginated_exports_are_issues(self):
        self.raw['a']['hasMore'] = True
        self.raw['a']['items'][0]['textTruncated'] = True
        self.save()
        self.assertEqual(self.check(verify(self.db), 'a.export_complete'), 'ISSUES')

    def test_subagent_and_wrong_workspace_are_rejected(self):
        self.raw['a']['thread']['parentThreadId'] = 'manager'
        self.raw['b']['thread']['worktreePath'] = str(self.repo)
        self.save()
        report = verify(self.db)
        self.assertEqual(self.check(report, 'a.ordinary_thread'), 'ISSUES')
        self.assertEqual(self.check(report, 'b.ordinary_thread'), 'ISSUES')

    def test_stale_approval_self_review_and_failed_check_rejected(self):
        for modification in ("UPDATE reviews SET reviewer='a' WHERE submission_id=1",
                             "UPDATE reviews SET commit_sha='stale' WHERE submission_id=1"):
            with self.subTest(modification=modification):
                with self.board.transaction() as db:
                    db.execute(modification)
                self.assertEqual(self.check(verify(self.db), 'landing.1'), 'ISSUES')
                with self.board.transaction() as db:
                    db.execute('UPDATE reviews SET reviewer=?,commit_sha=? WHERE submission_id=1',
                               ('b', self.commits['a']))
        with self.board.transaction() as db:
            row = db.execute('SELECT evidence FROM submissions WHERE id=1').fetchone()
            checks = json.loads(row[0]); checks['returncode'] = 1
            db.execute('UPDATE submissions SET evidence=? WHERE id=1', (json.dumps(checks),))
        self.assertEqual(self.check(verify(self.db), 'landing.1'), 'ISSUES')

    def test_configured_command_and_actual_main_ancestry_required(self):
        with self.board.transaction() as db:
            db.execute("UPDATE run_config SET value=? WHERE key='check_command'", (json.dumps(['wrong']),))
        self.assertEqual(self.check(verify(self.db), 'landing.1'), 'ISSUES')
        with self.board.transaction() as db:
            db.execute("UPDATE run_config SET value=? WHERE key='check_command'", (json.dumps(self.command),))
        git(self.repo, 'reset', '--hard', self.baseline)
        self.assertEqual(self.check(verify(self.db), 'landing.2'), 'ISSUES')

    def test_wrong_parent_and_scope_rejected(self):
        with self.board.transaction() as db:
            db.execute("UPDATE tasks SET result=? WHERE id='a'",
                       (json.dumps({'status': 'PASS', 'commit': self.baseline}),))
        self.assertEqual(self.check(verify(self.db), 'task.a.authored'), 'ISSUES')
        with self.board.transaction() as db:
            db.execute("UPDATE tasks SET result=? WHERE id='a'",
                       (json.dumps({'status': 'PASS', 'commit': self.commits['b']}),))
        self.assertEqual(self.check(verify(self.db), 'task.a.authored'), 'ISSUES')

    def test_task_result_must_match_exact_landed_sha(self):
        worktree = self.root / 'a'
        (worktree / 'a.txt').write_text('amended result')
        git(worktree, 'add', '.')
        git(worktree, 'commit', '--amend', '-m', 'amended')
        amended = git(worktree, 'rev-parse', 'HEAD')
        with self.board.transaction() as db:
            db.execute("UPDATE tasks SET result=? WHERE id='a'",
                       (json.dumps({'status': 'PASS', 'commit': amended}),))
        report = verify(self.db)
        self.assertEqual(self.check(report, 'task.a.authored'), 'PASS')
        self.assertEqual(self.check(report, 'task.a.landed'), 'ISSUES')

    def test_authored_result_requires_full_sha_and_clean_author_checkout(self):
        output = self.root / 'unexpected-output'
        for invalid in ('a', '--output=' + str(output)):
            with self.subTest(commit=invalid):
                with self.board.transaction() as db:
                    db.execute("UPDATE tasks SET result=? WHERE id='a'",
                               (json.dumps({'status': 'PASS', 'commit': invalid}),))
                self.assertEqual(self.check(verify(self.db), 'task.a.authored'), 'ISSUES')
                self.assertFalse(output.exists())
        with self.board.transaction() as db:
            db.execute("UPDATE tasks SET result=? WHERE id='a'",
                       (json.dumps({'status': 'PASS', 'commit': self.commits['a']}),))
        (self.root / 'a' / 'uncommitted.txt').write_text('unfinished')
        self.assertEqual(self.check(verify(self.db), 'task.a.authored'), 'ISSUES')

    def test_unrelated_candidate_tree_fails_even_with_matching_rows_and_ancestry(self):
        # Substitute an actual ancestor commit with the required parent but wrong tree.
        tree = git(self.repo, 'show', '-s', '--format=%T', self.baseline)
        unrelated = git(self.repo, 'commit-tree', tree, '-p', self.baseline, '-m', 'unrelated candidate')
        git(self.repo, 'reset', '--hard', unrelated)
        with self.board.transaction() as db:
            row = db.execute('SELECT evidence FROM submissions WHERE id=1').fetchone()
            checks = json.loads(row[0]); checks['candidate'] = unrelated
            db.execute('UPDATE submissions SET candidate_sha=?,evidence=? WHERE id=1',
                       (unrelated, json.dumps(checks)))
            for row in db.execute("SELECT seq,kind,data FROM events WHERE kind IN ('landing_landed','landing_checked')").fetchall():
                data = json.loads(row['data'])
                if data['submission_id'] != 1:
                    continue
                if row['kind'] == 'landing_checked':
                    data['evidence'] = json.dumps(checks)
                else:
                    data['candidate'] = unrelated
                db.execute('UPDATE events SET data=? WHERE seq=?', (json.dumps(data), row['seq']))
        self.assertEqual(self.check(verify(self.db), 'landing.1'), 'ISSUES')

    def test_concise_excerpt_in_narrative_is_corroborated(self):
        with self.board.transaction() as db:
            db.execute("UPDATE events SET data=? WHERE kind='context_reused' AND peer_id='b'",
                       (json.dumps({'message_id': 'transport-a-follow', 'prior_message_id': 'transport-a-q',
                                    'fact': 'I reused the earlier explanation of expired claims.',
                                    'fact_excerpt': 'stale token rejection'}),))
        self.assertEqual(self.check(verify(self.db), 'a.context_followup'), 'PASS')

    def test_protocol_deviation_is_an_issue_with_original_evidence(self):
        fact = {'reason': 'message sent without reservation', 'message_id': 'unreserved'}
        self.board.record('protocol_deviation', 'a', 'a', fact)
        report = verify(self.db)
        self.assertEqual(report['status'], 'ISSUES')
        check = next(c for c in report['checks'] if c['name'] == 'messages.protocol')
        self.assertEqual(check['detail'], [fact])

    def test_landing_before_checks_is_rejected(self):
        with self.board.transaction() as db:
            events = db.execute("SELECT seq,kind,data FROM events WHERE task_id='a' AND kind IN ('landing_checked','landing_landed') ORDER BY seq").fetchall()
            first, second = events
            db.execute('UPDATE events SET kind=?,data=? WHERE seq=?',
                       (second['kind'], second['data'], first['seq']))
            db.execute('UPDATE events SET kind=?,data=? WHERE seq=?',
                       (first['kind'], first['data'], second['seq']))
        report = verify(self.db)
        self.assertEqual(report['status'], 'ISSUES')
        self.assertEqual(self.check(report, 'landing.1'), 'ISSUES')

    def test_superseded_rejected_commit_is_preserved_without_requiring_its_landing(self):
        worktree = self.root / 'a'
        (worktree / 'a.txt').write_text('rejected version')
        git(worktree, 'add', '.')
        git(worktree, 'commit', '--amend', '-m', 'rejected')
        rejected = git(worktree, 'rev-parse', 'HEAD')
        git(worktree, 'reset', '--hard', self.commits['a'])
        with self.board.transaction() as db:
            db.execute('INSERT INTO submissions VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (0, 'a', 'a', rejected, 'superseded', None, None, 0, '{}', 0))
            db.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?)',
                       (0, 'b', rejected, 'reject', json.dumps({'finding': 'false PASS'}), 0))
        report = verify(self.db)
        self.assertEqual(report['status'], 'PASS', report)
        historical = next(c for c in report['checks'] if c['name'] == 'landing.0.superseded')
        self.assertEqual(historical['detail']['history'][0]['verdict'], 'reject')
        self.board.record('landing_landed', 'a', 'a', {'submission_id': 0})
        self.assertEqual(self.check(verify(self.db), 'landing.0.superseded'), 'ISSUES')

    def test_protocol_boilerplate_is_not_a_reused_fact(self):
        bodies = {'q': 'Which task are you implementing?', 'answer': 'I am writing tests.',
                  'follow': 'What files are writable?', 'response': 'Only tests/test_board.py.'}
        with self.board.transaction() as db:
            for message in self.board.messages():
                body = bodies[message['id'].split('-', 1)[1]] + ' reply once; no acknowledgment loop'
                db.execute('UPDATE messages SET body=? WHERE id=?', (body, message['id']))
                for export in self.raw.values():
                    for item in export['items']:
                        if item['messageId'] == message['transport_id']:
                            item['text'] = body
            for event in db.execute("SELECT seq,data FROM events WHERE kind='context_reused'").fetchall():
                data = json.loads(event['data']); data['fact'] = 'no acknowledgment loop'
                db.execute('UPDATE events SET data=? WHERE seq=?', (json.dumps(data), event['seq']))
        self.save()
        report = verify(self.db)
        self.assertEqual(report['status'], 'ISSUES')
        self.assertEqual(self.check(report, 'a.context_followup'), 'ISSUES')
        self.assertEqual(self.check(report, 'b.context_followup'), 'ISSUES')

    def test_reservation_after_raw_inbound_is_rejected(self):
        self.raw['b']['items'][0]['updatedAt'] = '2000-01-01T00:00:00Z'
        self.save()
        self.assertEqual(self.check(verify(self.db), 'message.a-q'), 'ISSUES')

    def test_missing_inputs_block_without_creating_database(self):
        missing = self.root / 'missing.sqlite'
        self.assertEqual(verify(missing)['status'], 'BLOCKED')
        self.assertFalse(missing.exists())
        (self.exports / 'a.json').write_text('{broken')
        self.assertEqual(verify(self.db)['status'], 'BLOCKED')

    def test_missing_export_is_issue_and_database_is_read_only(self):
        (self.exports / 'a.json').unlink()
        before = (self.board.tasks(), self.board.events(), self.board.messages())
        report = verify(self.db)
        self.assertEqual(self.check(report, 'a.ordinary_thread'), 'ISSUES')
        self.assertEqual((self.board.tasks(), self.board.events(), self.board.messages()), before)

    def test_cli_json_and_exit_status(self):
        command = [sys.executable, '-m', 'pstack_mesh.verify', '--db', str(self.db)]
        result = subprocess.run(command, env=MESH_ENV, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'PASS')
        self.raw['a']['hasMore'] = True; self.save()
        result = subprocess.run(command, env=MESH_ENV, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)['status'], 'ISSUES')


if __name__ == '__main__':
    unittest.main()
