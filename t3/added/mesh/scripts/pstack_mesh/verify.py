"""Read-only checks of mesh evidence, raw T3 exports, and actual Git objects.

Run ``python -m pstack_mesh.verify --db PATH [--exports DIRECTORY]``. Exit codes are
0 for PASS, 1 for ISSUES, and 2 for BLOCKED. This checks recorded evidence;
it cannot attest exporter authenticity or prove a model's internal memory.
"""
import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile

from pstack_mesh.landing import artifact_ref


def git(repository, *args):
    return subprocess.check_output(
        ['git', '-c', 'core.longpaths=true', '-C', str(repository), *args], text=True,
        stderr=subprocess.STDOUT, timeout=30).strip()


def normalize(path):
    return str(Path(path).resolve()).casefold()


def decode(value):
    return json.loads(value) if isinstance(value, str) else value


def words(text):
    return set(re.findall(r'[a-z0-9]+', text.lower()))


def contains_fact(text, fact):
    text = re.sub(r'\breply\s+once\b|\bno\s+acknowledg(?:e)?ment\s+loop\b|\bno\s+reply\s+(?:needed|required)\b',
                  ' ', text, flags=re.IGNORECASE)
    tokens = lambda value: ' ' + ' '.join(re.findall(r'[a-z0-9]+', value.lower())) + ' '
    return tokens(fact) in tokens(text)


def applied_tree(repository, baseline, base, commit):
    """Replay into a temporary index, never touching a peer or integration tree."""
    with tempfile.TemporaryDirectory(prefix='mesh-verify-index-') as directory:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(directory) / 'index'))
        command = ['git', '-c', 'core.longpaths=true', '-C', str(repository)]
        subprocess.run(command + ['read-tree', base], env=env, check=True,
                       capture_output=True, timeout=30)
        patch = subprocess.check_output(command + ['diff', '--binary', '--full-index', baseline, commit], timeout=30)
        subprocess.run(command + ['apply', '--cached', '--3way', '--binary'], input=patch,
                       env=env, check=True, capture_output=True, timeout=30)
        return subprocess.check_output(command + ['write-tree'], env=env, text=True, timeout=30).strip()


def message_id(data):
    return data.get('message_id') or data.get('transport_id') or data.get('inbound_message_id')


class Evidence:
    def __init__(self):
        self.checks = []

    def check(self, name, valid, detail):
        self.checks.append({'name': name, 'status': 'PASS' if valid else 'ISSUES',
                            'detail': detail})

    def report(self):
        return {'status': 'ISSUES' if any(c['status'] != 'PASS' for c in self.checks) else 'PASS',
                'checks': self.checks,
                'limits': ['Raw exports are trusted observer inputs, not cryptographic attestations.',
                           'Transcript reuse demonstrates specific exchanged facts, not internal model memory.',
                           'Historical tests and crash recovery need their own test evidence; terminal states do not prove them.']}


def snapshot(path):
    # A URI in read-only mode also prevents accidentally creating a missing DB.
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN')
        result = {name: [dict(row) for row in db.execute(f'SELECT * FROM {name}')]
                  for name in ('peers', 'tasks', 'events', 'messages', 'run_config')}
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for name in ('submissions', 'reviews'):
            result[name] = [dict(row) for row in db.execute(f'SELECT * FROM {name}')] if name in tables else []
    result['config'] = {row['key']: decode(row['value']) for row in result['run_config']}
    for event in result['events']:
        event['data'] = decode(event['data'])
        if not isinstance(event['data'], dict):
            raise ValueError('Event data must be an object')
    return result


def exports_at(directory):
    exports = {}
    for path in sorted(Path(directory).glob('*.json')):
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        thread = value['thread']
        thread_id = thread['threadId']
        if thread_id in exports:
            raise ValueError(f'Duplicate thread export: {thread_id}')
        exports[thread_id] = value
    return exports


def verify(board_path, exports_path=None):
    try:
        return _verify(board_path, exports_path)
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as error:
        return {'status': 'BLOCKED', 'checks': [], 'error': str(error)}


def _verify(board_path, exports_path=None):
    evidence = Evidence()
    try:
        state = snapshot(board_path)
        exports = exports_at(exports_path or Path(board_path).parent / 'thread-exports')
        config = state['config']
        repository, baseline = config['repository'], config['baseline']
        if not isinstance(baseline, str) or not re.fullmatch('[0-9a-f]{40}', baseline):
            raise ValueError('Configured baseline must be an exact full SHA')
        expected = config.get('max_peers', 5)
        peers = {peer['id']: peer for peer in state['peers']}
        events, messages = state['events'], state['messages']
        if type(expected) is not int or expected < 1:
            raise ValueError('max_peers must be a positive integer')
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as error:
        return {'status': 'BLOCKED', 'checks': [], 'error': str(error)}

    evidence.check('registry.count', 0 < len(peers) <= expected,
                   {'maximum': expected, 'actual': len(peers)})
    evidence.check('registry.distinct',
                   len({p['thread_id'] for p in peers.values()}) == len(peers)
                   and len({normalize(p['workspace']) for p in peers.values()}) == len(peers),
                   'Each peer must own a distinct thread and worktree.')
    try:
        common = normalize(Path(repository) / git(repository, 'rev-parse', '--git-common-dir'))
        git(repository, 'cat-file', '-e', baseline + '^{commit}')
    except (OSError, subprocess.SubprocessError) as error:
        return {'status': 'BLOCKED', 'checks': evidence.checks, 'error': str(error)}

    received = {(event['peer_id'], message_id(event['data']))
                for event in events if event['kind'] == 'message_received'}
    raw_messages = {}
    for peer_id, peer in peers.items():
        export = exports.get(peer['thread_id'], {})
        thread = export.get('thread', {})
        ordinary = (bool(thread) and thread.get('parentThreadId') is None
                    and thread.get('relationshipToParent') is None
                    and normalize(thread.get('worktreePath') or '.') == normalize(peer['workspace']))
        evidence.check(f'{peer_id}.ordinary_thread', ordinary,
                       {'thread_id': peer['thread_id'], 'exported_parent': thread.get('parentThreadId')})
        evidence.check(f'{peer_id}.export_complete', bool(export) and export.get('hasMore') is False
                       and all(not item.get('textTruncated') and item.get('nextTextOffset') is None
                               for item in export.get('items', [])),
                       'Refresh and fully paginate raw t3_thread_read exports.')
        for item in export.get('items', []):
            if (item.get('type') == 'user_message' and item.get('visibility') == 'local'
                    and item.get('sourceThreadId') == peer['thread_id']):
                raw_messages[(peer_id, item.get('messageId'))] = item
        try:
            actual_root = normalize(git(peer['workspace'], 'rev-parse', '--show-toplevel'))
            actual_common = normalize(Path(peer['workspace']) / git(peer['workspace'], 'rev-parse', '--git-common-dir'))
            valid = actual_root == normalize(peer['workspace']) and actual_common == common
        except (OSError, subprocess.SubprocessError):
            valid = False
        evidence.check(f'{peer_id}.git_worktree', valid,
                       {'workspace': peer['workspace'], 'repository': repository})
        registered = [e for e in events if e['kind'] == 'registered' and e['peer_id'] == peer_id]
        claims = [e for e in events if e['kind'] == 'claimed' and e['peer_id'] == peer_id]
        evidence.check(f'{peer_id}.independent_claim', bool(registered and claims)
                       and registered[0]['seq'] < claims[0]['seq'],
                       {'claimed_tasks': [e['task_id'] for e in claims]})
        discoveries = [e['data'] for e in events if e['kind'] == 'discovery' and e['peer_id'] == peer_id]
        named = {d.get('peer_id') or d.get('registered_peer') or d.get('peer') for d in discoveries}
        discovered_threads = {tid for d in discoveries for tid in d.get('discovered_threads', [])}
        discovered_threads.update(tid for d in discoveries for tid in d.get('peer_threads', {}).values())
        evidence.check(f'{peer_id}.discovery', any(
            other_id != peer_id and (other_id in named or other['thread_id'] in discovered_threads)
            for other_id, other in peers.items()), 'Discovery must identify another registered peer or its thread.')

    counts = Counter(m['sender'] for m in messages)
    evidence.check('messages.budget', all(count <= 20 for count in counts.values()), dict(counts))
    violations = [e for e in events if e['kind'] in ('message_protocol_violation', 'protocol_deviation')]
    evidence.check('messages.protocol', not violations, [e['data'] for e in violations])
    transport = {}
    pending = {e['data'].get('id'): e for e in events if e['kind'] == 'message_pending'}
    for message in messages:
        tid = message.get('transport_id')
        item = raw_messages.get((message['recipient'], tid), {})
        reservation = pending.get(message['id'], {})
        inbound_time = datetime.fromisoformat(item['updatedAt'].replace('Z', '+00:00')).timestamp() if item.get('updatedAt') else None
        valid = (message['sender'] in peers and message['recipient'] in peers
                 and message['sender'] != message['recipient'] and tid
                 and message['state'] == 'delivered'
                 and item.get('text') == message['body']
                 and not item.get('textTruncated')
                 and reservation.get('peer_id') == message['sender']
                 and reservation.get('created', float('inf')) <= message['created'] + 0.01
                 and inbound_time is not None and reservation.get('created', float('inf')) <= inbound_time)
        evidence.check(f"message.{message['id']}", bool(valid),
                       {'transport_id': tid, 'recipient': message['recipient'],
                        'raw_inbound_found': bool(item)})
        if valid:
            if tid in transport:
                evidence.check('messages.unique_transport', False, tid)
            transport[tid] = dict(message, observed_time=inbound_time)

    proven = set()
    for event in events:
        if event['kind'] != 'context_reused':
            continue
        data = event['data']
        follow = transport.get(message_id(data))
        prior_id = (data.get('prior_message_id') or data.get('previous_message_id')
                    or data.get('prior_transport_id'))
        prior = transport.get(prior_id)
        fact = (data.get('fact_excerpt') or data.get('fact') or data.get('fact_reused')
                or data.get('reused_fact') or '')
        if not isinstance(fact, str) or len(fact.strip()) < 12 or len(words(fact)) < 2:
            continue
        if not follow or not prior or event['peer_id'] != follow['recipient']:
            continue
        a, b = follow['sender'], follow['recipient']
        questions = [m for m in transport.values() if m['sender'] == a and m['recipient'] == b
                     and m['observed_time'] < follow['observed_time'] and (m == prior or prior['sender'] == b)]
        for question in questions:
            answers = [m for m in transport.values() if m['sender'] == b and m['recipient'] == a
                       and question['observed_time'] < m['observed_time'] < follow['observed_time']]
            responses = [m for m in transport.values() if m['sender'] == b and m['recipient'] == a
                         and m['observed_time'] > follow['observed_time']]
            if ((b, question['transport_id']) not in received or (b, follow['transport_id']) not in received):
                continue
            if not (prior == question or prior in answers):
                continue
            if any(contains_fact(answer['body'], fact) and contains_fact(follow['body'], fact)
                   and contains_fact(response['body'], fact) for answer in answers for response in responses):
                proven.add(a)
    for peer_id in peers:
        evidence.check(f'{peer_id}.context_followup', peer_id in proven,
                       'Requires raw IDs, two received events, and the same concrete fact in both answers and follow-up.')

    for task in state['tasks']:
        try:
            result = decode(task['result']) if task['result'] else {}
            commit = result.get('commit') or result.get('commit_sha')
            exact = isinstance(commit, str) and re.fullmatch('[0-9a-f]{40}', commit)
            if not exact:
                raise ValueError('Task result must name an exact full commit SHA')
            parents = git(repository, 'show', '-s', '--format=%P', commit) if commit else ''
            changed = git(repository, 'diff-tree', '--no-commit-id', '--name-only', '-r', commit).splitlines() if commit else []
            scope = config.get('task_scopes', {}).get(task['id'], [])
            valid = (task['status'] == 'done' and result.get('status') == 'PASS'
                     and bool(exact) and parents == baseline and bool(changed) and all(path in scope for path in changed)
                     and task['owner'] in peers)
            if valid:
                ref = artifact_ref(board_path, task['id'], commit)
                retained = git(repository, 'for-each-ref', '--format=%(objectname)', ref)
                if retained or config.get('project_database'):
                    valid = retained == commit
                else:
                    workspace = peers[task['owner']]['workspace']
                    valid = git(workspace, 'rev-parse', 'HEAD') == commit and not git(workspace, 'status', '--porcelain')
        except (ValueError, AttributeError, OSError, subprocess.SubprocessError):
            valid, commit, changed = False, None, []
        evidence.check(f"task.{task['id']}.authored", valid,
                       {'commit': commit, 'changed_files': changed, 'status': task['status']})
    _landing(evidence, state, repository, baseline)
    return evidence.report()


def _landing(evidence, state, repository, baseline):
    peers = {p['id'] for p in state['peers']}
    submissions = sorted(state['submissions'], key=lambda s: s['id'])
    evidence.check('landing.present', bool(submissions), 'Requires durable landing submissions and check evidence.')
    for task in state['tasks']:
        result = decode(task['result']) if task['result'] else {}
        authored = result.get('commit') or result.get('commit_sha')
        evidence.check(f"task.{task['id']}.landed", bool(authored) and any(
            s['task_id'] == task['id'] and s['status'] == 'landed' and s['commit_sha'] == authored
            and s['author'] == task['owner']
            and s.get('task_attempt', None if state['config'].get('project_database') else task['attempts']) == task['attempts']
            for s in submissions),
            'The landed submission must match the exact authored task result commit, owner and attempt.')
    landed_ids = []
    for submission in submissions:
        sid, author = submission['id'], submission['author']
        commit, candidate, base = submission['commit_sha'], submission['candidate_sha'], submission['base_sha']
        exact_commit = isinstance(commit, str) and re.fullmatch('[0-9a-f]{40}', commit)
        if submission['status'] == 'superseded':
            if not exact_commit:
                evidence.check(f'landing.{sid}.superseded', False, 'Historical commit is not an exact SHA.')
                continue
            later = any(s['id'] > sid and s['task_id'] == submission['task_id'] for s in submissions)
            unexpected_landing = any(e['kind'] == 'landing_landed' and e['data'].get('submission_id') == sid
                                     for e in state['events'])
            files = git(repository, 'diff-tree', '--no-commit-id', '--name-only', '-r', commit).splitlines()
            valid = (later and not unexpected_landing and bool(files)
                     and git(repository, 'show', '-s', '--format=%P', commit) == baseline
                     and all(path in state['config']['task_scopes'].get(submission['task_id'], []) for path in files))
            evidence.check(f'landing.{sid}.superseded', valid,
                           {'commit': commit, 'history': [r for r in state['reviews'] if r['submission_id'] == sid]})
            continue
        exact_reviews = [r for r in state['reviews'] if r['submission_id'] == sid and r['commit_sha'] == commit]
        reviews = [r for r in exact_reviews
                   if r['reviewer'] in peers and r['reviewer'] != author and r['verdict'] == 'approve'
                   and decode(r['evidence'])]
        checks = decode(submission['evidence']) if submission['evidence'] else {}
        checked_reviews = checks.get('reviews', []) if isinstance(checks, dict) else []
        checked_reviewers = {r.get('reviewer') for r in checked_reviews if isinstance(r, dict)
                            and r.get('commit_sha') == commit and r.get('verdict') == 'approve'}
        checked = [e for e in state['events'] if e['kind'] == 'landing_checked'
                   and e['data'].get('submission_id') == sid and e['data'].get('commit') == commit]
        landed = [e for e in state['events'] if e['kind'] == 'landing_landed'
                  and e['data'].get('submission_id') == sid and e['data'].get('commit') == commit
                  and e['data'].get('candidate') == candidate and e['data'].get('base') == base]
        valid = (submission['status'] == 'landed' and author in peers and bool(reviews and checked and landed)
                 and bool(exact_commit)
                 and all(isinstance(sha, str) and re.fullmatch('[0-9a-f]{40}', sha) for sha in (base, candidate))
                 and not any(r['verdict'] == 'reject' for r in exact_reviews)
                 and isinstance(checks, dict) and checks.get('returncode') == 0
                 and checks.get('command') == state['config'].get('check_command')
                 and checks.get('candidate') == candidate and checks.get('base') == base
                 and any(r['reviewer'] in checked_reviewers for r in reviews)
                 and bool(commit and candidate and base))
        if checked:
            valid = valid and decode(checked[-1]['data'].get('evidence', {})) == checks
        if checked and landed:
            valid = valid and checked[-1]['seq'] < landed[-1]['seq']
        try:
            valid = valid and git(repository, 'show', '-s', '--format=%P', commit) == baseline
            valid = valid and git(repository, 'show', '-s', '--format=%P', candidate) == base
            valid = valid and git(repository, 'show', '-s', '--format=%T', candidate) == applied_tree(repository, baseline, base, commit)
            valid = valid and subprocess.run(['git', '-c', 'core.longpaths=true', '-C', repository, 'merge-base', '--is-ancestor', candidate,
                                             state['config'].get('target_branch', 'main')],
                                            capture_output=True, timeout=30).returncode == 0
        except (OSError, subprocess.SubprocessError):
            valid = False
        evidence.check(f'landing.{sid}', bool(valid),
                       {'commit': commit, 'candidate': candidate, 'reviewers': [r['reviewer'] for r in reviews]})
        if landed:
            landed_ids.append((landed[-1]['seq'], sid))
    evidence.check('landing.order', [sid for _, sid in sorted(landed_ids)] == sorted(sid for _, sid in landed_ids),
                   'Landing events must follow deterministic submission order.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True)
    parser.add_argument('--exports')
    args = parser.parse_args()
    result = verify(args.db, args.exports)
    print(json.dumps(result, indent=2))
    return {'PASS': 0, 'ISSUES': 1, 'BLOCKED': 2}[result['status']]


if __name__ == '__main__':
    raise SystemExit(main())
