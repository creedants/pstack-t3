"""Reviewed, ordered Git integration. Configuration belongs to the local run owner.

Each queue item moves pending -> preparing -> checking -> checked -> landed.
Candidate/base SHAs survive crashes; a kernel lock is released when a process dies.
Conflicts and check failures stop that item and require explicit resubmission.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

from pstack_mesh.board import Board
from check_slot import capacity_lock


SCHEMA = """
CREATE TABLE IF NOT EXISTS submissions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL REFERENCES tasks(id),
 author TEXT NOT NULL REFERENCES peers(id), commit_sha TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', base_sha TEXT, candidate_sha TEXT,
 attempts INTEGER NOT NULL DEFAULT 0, evidence TEXT NOT NULL DEFAULT '{}',
 created REAL NOT NULL, task_attempt INTEGER, UNIQUE(task_id,commit_sha)
);
CREATE TABLE IF NOT EXISTS reviews (
 submission_id INTEGER NOT NULL REFERENCES submissions(id),
 reviewer TEXT NOT NULL REFERENCES peers(id), commit_sha TEXT NOT NULL,
 verdict TEXT NOT NULL, evidence TEXT NOT NULL, created REAL NOT NULL,
 PRIMARY KEY(submission_id,reviewer)
);
"""


class GitError(ValueError):
    pass


def git(path, *args):
    result = subprocess.run(['git', '-c', 'core.longpaths=true', '-C', str(path), *args], text=True,
                            capture_output=True)
    if result.returncode:
        raise GitError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def artifact_ref(board_path, task_id, commit):
    board = os.path.normcase(str(Path(board_path).resolve()))
    return 'refs/pstack/mesh-artifacts/' + hashlib.sha256(board.encode()).hexdigest() + '/' + hashlib.sha256(task_id.encode()).hexdigest() + '/' + commit


@contextmanager
def repository_lock(path):
    """Nonblocking cross-process lock, including on Windows; no stale lock lease."""
    with open(path, 'a+b') as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ValueError('Another landing process holds the Git lock') from error
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class Landing:
    def __init__(self, board_path, repository_path):
        self.board = Board(board_path)
        with self.board.connect() as db:
            self.config = {row['key']: json.loads(row['value'])
                           for row in db.execute('SELECT * FROM run_config')}
        required = ('repository', 'baseline', 'integration_worktree',
                    'integration_branch', 'target_branch', 'check_command', 'task_scopes')
        if any(key not in self.config for key in required):
            raise ValueError('Incomplete local run_config')
        self.repository = Path(repository_path).resolve()
        if self.repository != Path(self.config['repository']).resolve():
            raise ValueError('Repository does not match local run_config')
        if Path(git(self.repository, 'rev-parse', '--show-toplevel')).resolve() != self.repository:
            raise ValueError('Expected repository root')
        self.common = Path(git(self.repository, 'rev-parse', '--path-format=absolute', '--git-common-dir')).resolve()
        self.integration = Path(self.config['integration_worktree']).resolve()
        if self.integration == self.repository or self.repository in self.integration.parents:
            raise ValueError('Integration worktree must be outside repository')
        for branch in ('target_branch', 'integration_branch'):
            git(self.repository, 'check-ref-format', '--branch', self.config[branch])
        if self.config['target_branch'] == self.config['integration_branch']:
            raise ValueError('Integration and target branches must differ')
        self._commit(self.config['baseline'])
        self._command(self.config['check_command'])
        with self.board.transaction() as db:
            for statement in SCHEMA.split(';'):
                if statement.strip():
                    db.execute(statement)
            if 'task_attempt' not in {row['name'] for row in db.execute('PRAGMA table_info(submissions)')}:
                db.execute('ALTER TABLE submissions ADD COLUMN task_attempt INTEGER')

    @staticmethod
    def _command(command):
        if not isinstance(command, list) or not command or any(
                not isinstance(arg, str) or not arg or '\0' in arg for arg in command):
            raise ValueError('Check command must be a nonempty argv list')

    def _commit(self, commit):
        if not isinstance(commit, str) or not re.fullmatch('[0-9a-f]{40}', commit):
            raise ValueError('Supply an exact full commit SHA')
        if git(self.repository, 'rev-parse', commit + '^{commit}') != commit:
            raise ValueError('Expected a commit object')

    def _peer(self, db, peer):
        row = db.execute('SELECT * FROM peers WHERE id=?', (peer,)).fetchone()
        if row is None:
            raise ValueError('Peer is not registered')
        return row

    def _authority(self, db):
        config = Board.require_running(db)
        project_database = config.get('project_database')
        marker = self.common / 'pstack-project.json'
        if marker.exists():
            owner = json.loads(marker.read_text(encoding='utf-8'))
            if not project_database or os.path.normcase(str(Path(owner['project_database']).resolve())) != os.path.normcase(str(Path(project_database).resolve())):
                raise ValueError('Queue does not own the canonical project database')
        elif project_database:
            raise ValueError('Canonical project marker is missing')
        name = self.common.parent.name if self.common.name == '.git' else self.common.name.removesuffix('.git')
        key = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-') + '-' + hashlib.sha1(str(self.common).encode()).hexdigest()[:8]
        legacy = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'pstack-t3/landing' / key / 'land.db'
        if legacy.exists() or git(self.repository, 'for-each-ref', '--format=%(refname)', 'refs/landing/'):
            raise ValueError('Repository already has a competing pstack landing authority')
        return config

    @staticmethod
    def _task(db, task_id, author, commit, attempt=None, completed=False):
        task = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
        if task is None or task['owner'] != author or task['status'] not in ('claimed', 'done') or (attempt is not None and task['attempts'] != attempt):
            raise ValueError('Submission task attempt expired, superseded, or owned by another peer')
        if task['status'] == 'claimed':
            if completed:
                raise ValueError('Project submission requires a completed PASS task result')
            now = time.time()
            if task['expires'] is None or task['expires'] <= now or (task['deadline'] is not None and task['deadline'] <= now):
                raise ValueError('Author claim has expired')
        else:
            result = json.loads(task['result'] or '{}')
            exact = result.get('commit') or result.get('commit_sha')
            if result.get('status') != 'PASS' or (exact is not None and exact != commit) or (completed and exact != commit):
                raise ValueError('Submission must match the completed PASS task result commit')
        return task

    def _submission(self, db, row):
        config = self._authority(db)
        current = db.execute('SELECT * FROM submissions WHERE id=?', (row['id'],)).fetchone()
        if not current or current['status'] in ('superseded', 'landed') or current['task_attempt'] is None:
            raise ValueError('Submission is inactive or lacks a fenced task attempt')
        self._task(db, current['task_id'], current['author'], current['commit_sha'], current['task_attempt'], bool(config.get('project_database')))
        return current

    def _artifact(self, task_id, commit):
        ref = artifact_ref(self.board.path, task_id, commit)
        old = git(self.repository, 'for-each-ref', '--format=%(objectname)', ref)
        if old:
            if old != commit:
                raise ValueError('Authored artifact ref differs from exact commit')
        else:
            git(self.repository, 'update-ref', ref, commit, '0' * 40)

    def submit(self, task_id, author, commit):
        self._commit(commit)
        parents = git(self.repository, 'show', '-s', '--format=%P', commit).split()
        if parents != [self.config['baseline']]:
            raise ValueError('Submission must have exactly the baseline as its parent')
        files = git(self.repository, 'diff-tree', '--no-commit-id', '--name-only', '-r', commit).splitlines()
        allowed = self.config['task_scopes'].get(task_id)
        if not files or not allowed or any(file not in allowed for file in files):
            raise ValueError('Submission changes files outside its task scope')
        with repository_lock(self.common / 'mesh-landing.lock'), self.board.transaction() as db:
            config = self._authority(db)
            peer = self._peer(db, author)
            task = self._task(db, task_id, author, commit, completed=bool(config.get('project_database')))
            workspace = Path(peer['workspace'])
            if Path(git(workspace, 'rev-parse', '--path-format=absolute', '--git-common-dir')).resolve() != self.common:
                raise ValueError('Author worktree belongs to another repository')
            if git(workspace, 'rev-parse', 'HEAD') != commit or git(workspace, 'status', '--porcelain'):
                raise ValueError('Author worktree must be clean at the submitted commit')
            self._artifact(task_id, commit)
            old = db.execute('SELECT * FROM submissions WHERE task_id=? AND commit_sha=?', (task_id, commit)).fetchone()
            if old:
                if old['author'] != author:
                    raise ValueError('Submission belongs to another author')
                if old['status'] == 'superseded':
                    raise ValueError('Submission was superseded; review the current commit')
                if old['task_attempt'] is None:
                    if old['status'] != 'pending':
                        raise ValueError('Historical submission has no fenced attempt; submit a new commit')
                    db.execute('UPDATE submissions SET task_attempt=? WHERE id=?', (task['attempts'], old['id']))
                    db.execute('DELETE FROM reviews WHERE submission_id=?', (old['id'],))
                elif old['task_attempt'] != task['attempts']:
                    raise ValueError('Submission belongs to a superseded task attempt')
                if old['status'] in ('failed', 'conflict') and old['attempts'] < 3:
                    db.execute("UPDATE submissions SET status='pending',base_sha=NULL,candidate_sha=NULL WHERE id=?", (old['id'],))
                return dict(db.execute('SELECT * FROM submissions WHERE id=?', (old['id'],)).fetchone())
            if db.execute("SELECT 1 FROM submissions WHERE task_id=? AND status IN ('preparing','checking','checked','landed')", (task_id,)).fetchone():
                raise ValueError('Task already integrating or landed')
            db.execute("UPDATE submissions SET status='superseded' WHERE task_id=? AND status IN ('pending','failed','conflict')", (task_id,))
            cursor = db.execute('INSERT INTO submissions(task_id,author,commit_sha,created,task_attempt) VALUES(?,?,?,?,?)', (task_id, author, commit, time.time(), task['attempts']))
            Board.event(db, 'submitted', author, task_id, {'submission_id': cursor.lastrowid, 'commit': commit, 'files': files})
            return dict(db.execute('SELECT * FROM submissions WHERE id=?', (cursor.lastrowid,)).fetchone())

    def review(self, task_id, reviewer, commit, verdict, evidence):
        self._commit(commit)
        if verdict not in ('approve', 'reject') or not evidence:
            raise ValueError('Review requires approve/reject and evidence')
        with self.board.transaction() as db:
            self._authority(db)
            self._peer(db, reviewer)
            row = db.execute('SELECT * FROM submissions WHERE task_id=? AND commit_sha=?', (task_id, commit)).fetchone()
            if row is None or row['status'] in ('superseded', 'landed'):
                raise ValueError('No active submission for that exact commit')
            if row['author'] == reviewer:
                raise ValueError('Author cannot review own submission')
            self._submission(db, row)
            db.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?) ON CONFLICT(submission_id,reviewer) DO UPDATE SET verdict=excluded.verdict,evidence=excluded.evidence,created=excluded.created',
                       (row['id'], reviewer, commit, verdict, json.dumps(evidence), time.time()))
            Board.event(db, 'review', reviewer, task_id, {'submission_id': row['id'], 'commit': commit, 'verdict': verdict, 'evidence': evidence})
        return {'task_id': task_id, 'commit': commit, 'verdict': verdict}

    def status(self):
        with self.board.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM submissions ORDER BY id')]

    def _approvals(self, db, row):
        reviews = [dict(r) for r in db.execute('SELECT * FROM reviews WHERE submission_id=? AND commit_sha=? AND reviewer!=? ORDER BY reviewer', (row['id'], row['commit_sha'], row['author']))]
        if not reviews or any(r['verdict'] == 'reject' for r in reviews):
            return None
        return reviews

    def _save(self, row, status, **fields):
        with self.board.transaction() as db:
            self._submission(db, row)
            db.execute('UPDATE submissions SET status=?' + ''.join(',' + key + '=?' for key in fields) + ' WHERE id=?',
                       [status, *fields.values(), row['id']])
            Board.event(db, 'landing_' + status, row['author'], row['task_id'],
                        {'submission_id': row['id'], 'commit': row['commit_sha'],
                         'candidate': row.get('candidate_sha'), 'base': row.get('base_sha'), **fields})

    def _clean(self, path):
        if git(path, 'status', '--porcelain', '--untracked-files=all'):
            raise ValueError('Checkout is dirty: ' + str(path))

    def _integration(self):
        if not self.integration.exists():
            branches = git(self.repository, 'for-each-ref', '--format=%(refname:short)', 'refs/heads').splitlines()
            if self.config['integration_branch'] in branches:
                git(self.repository, 'worktree', 'add', str(self.integration), self.config['integration_branch'])
            else:
                git(self.repository, 'worktree', 'add', '-b', self.config['integration_branch'],
                    str(self.integration), self.config['baseline'])
        if Path(git(self.integration, 'rev-parse', '--show-toplevel')).resolve() != self.integration:
            raise ValueError('Integration path is not the registered checkout')
        if Path(git(self.integration, 'rev-parse', '--path-format=absolute', '--git-common-dir')).resolve() != self.common:
            raise ValueError('Integration checkout belongs to another repository')
        if git(self.integration, 'symbolic-ref', '--short', 'HEAD') != self.config['integration_branch']:
            raise ValueError('Integration branch does not match configuration')

    @contextmanager
    def _target_transaction(self, candidate, expected):
        process = subprocess.Popen(['git', '-c', 'core.longpaths=true', '-C', str(self.repository),
                                    'update-ref', '--stdin'], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        def command(value):
            try:
                process.stdin.write((value + '\n').encode('ascii'))
                process.stdin.flush()
            except (OSError, BrokenPipeError) as error:
                raise GitError(process.stderr.read().decode(errors='replace').strip()) from error

        def acknowledged(value):
            if process.stdout.readline().strip() != (value + ': ok').encode('ascii'):
                raise GitError(process.stderr.read().decode(errors='replace').strip() or
                               'Git ref transaction did not acknowledge ' + value)

        try:
            command('start')
            acknowledged('start')
            command('update HEAD ' + candidate + ' ' + expected)
            command('prepare')
            acknowledged('prepare')
            target = 'refs/heads/' + self.config['target_branch']
            if git(self.repository, 'symbolic-ref', 'HEAD') != target:
                raise ValueError('Target branch changed before advancement')
            if git(self.repository, 'rev-parse', '--verify', target) != expected:
                raise ValueError('Target changed after checks')
            yield
            command('commit')
            acknowledged('commit')
        finally:
            # Closing stdin aborts any prepared transaction that did not commit.
            process.stdin.close()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            process.stdout.close()
            process.stderr.close()

    def _checked_checkout(self, row):
        if not git(self.repository, 'status', '--porcelain', '--untracked-files=all'):
            return
        if (row['status'] != 'checked' or
                git(self.repository, 'rev-parse', 'HEAD') != row['base_sha'] or
                git(self.repository, 'ls-files', '--others', '--exclude-standard') or
                git(self.repository, 'write-tree') != git(self.repository, 'rev-parse', row['candidate_sha'] + '^{tree}')):
            raise ValueError('Checkout is dirty: ' + str(self.repository))
        try:
            git(self.repository, 'diff-files', '--quiet')
        except GitError as error:
            raise ValueError('Checkout is dirty: ' + str(self.repository)) from error

    def _advance(self, candidate):
        base = git(self.repository, 'show', '-s', '--format=%P', candidate)
        with self._target_transaction(candidate, base):
            self._checked_checkout({'status': 'checked', 'base_sha': base, 'candidate_sha': candidate})
            git(self.repository, 'read-tree', '-m', '-u', base, candidate)

    def _land_checked(self, row, recovered=False):
        with self.board.transaction() as db:
            current = self._submission(db, row)
            if current['status'] != 'checked' or current['candidate_sha'] != row['candidate_sha'] or current['base_sha'] != row['base_sha']:
                raise ValueError('Checked submission changed before advancement')
            if self._approvals(db, current) is None:
                return {'status': 'awaiting_review', 'submission_id': row['id']}
            self._checked_checkout(row)
            expected = row['candidate_sha'] if recovered else row['base_sha']
            if git(self.repository, 'rev-parse', 'HEAD') != expected:
                raise ValueError('Target changed after checks')
            self._submission(db, current)
            if recovered:
                with self._target_transaction(row['candidate_sha'], row['candidate_sha']):
                    self._clean(self.repository)
            else:
                self._advance(row['candidate_sha'])
            db.execute("UPDATE submissions SET status='landed' WHERE id=?", (row['id'],))
            Board.event(db, 'landing_landed', row['author'], row['task_id'],
                        {'submission_id': row['id'], 'commit': row['commit_sha'],
                         'candidate': row['candidate_sha'], 'base': row['base_sha']})
        result = {'status': 'landed', 'submission_id': row['id'], 'commit': row['commit_sha'], 'candidate': row['candidate_sha']}
        if recovered:
            result['recovered'] = True
        return result

    def land(self, check_command=None):
        command = self.config['check_command'] if check_command is None else check_command
        self._command(command)
        if command != self.config['check_command']:
            raise ValueError('Check command differs from local run_config')
        with repository_lock(self.common / 'mesh-landing.lock'):
            with self.board.connect() as db:
                self._authority(db)
            self._integration()
            if git(self.repository, 'symbolic-ref', '--short', 'HEAD') != self.config['target_branch']:
                raise ValueError('Registered repository must have target branch checked out')
            head = git(self.repository, 'rev-parse', 'HEAD')
            git(self.repository, 'merge-base', '--is-ancestor', self.config['baseline'], head)
            with self.board.connect() as db:
                row = db.execute("SELECT * FROM submissions WHERE status IN ('pending','preparing','checking','checked') ORDER BY id LIMIT 1").fetchone()
                if row is None:
                    self._clean(self.repository)
                    return {'status': 'idle'}
                row = dict(row)
                self._submission(db, row)
                approvals = self._approvals(db, row)
            if row['status'] == 'checked':
                evidence = json.loads(row['evidence'])
                if (evidence.get('command') != command or evidence.get('returncode') != 0 or
                        evidence.get('base') != row['base_sha'] or
                        evidence.get('candidate') != row['candidate_sha']):
                    raise ValueError('Persisted checks do not match candidate and local command')
            self._checked_checkout(row)
            if row['status'] == 'checked' and head == row['candidate_sha']:
                self._clean(self.integration)
                if git(self.integration, 'rev-parse', 'HEAD') != head:
                    raise ValueError('Recovered integration checkout differs from candidate')
                return self._land_checked(row, recovered=True)
            if approvals is None:
                return {'status': 'awaiting_review', 'submission_id': row['id']}
            if row['status'] != 'pending' and row['base_sha'] != head:
                raise ValueError('Target changed during interrupted landing')
            if row['status'] == 'pending':
                self._clean(self.integration)
                self._save(row, 'preparing', base_sha=head, attempts=row['attempts'] + 1)
                row['base_sha'] = head
            if row['status'] in ('pending', 'preparing'):
                if git(self.integration, 'status', '--porcelain'):
                    if git(self.integration, 'ls-files', '--others', '--exclude-standard'):
                        raise ValueError('Untracked files in interrupted integration')
                    git(self.integration, 'reset', '--hard', head)
                    try:
                        git(self.integration, 'cherry-pick', '--quit')
                    except GitError:
                        pass
                self._clean(self.integration)
                git(self.integration, 'reset', '--hard', head)
                try:
                    git(self.integration, 'cherry-pick', '--no-commit', row['commit_sha'])
                except GitError as error:
                    # --no-commit can leave conflicts without CHERRY_PICK_HEAD.
                    git(self.integration, 'reset', '--hard', head)
                    try:
                        git(self.integration, 'cherry-pick', '--quit')
                    except GitError:
                        pass
                    self._save(row, 'conflict', evidence=json.dumps({'error': str(error)}))
                    return {'status': 'conflict', 'submission_id': row['id'], 'error': str(error)}
                tree = git(self.integration, 'write-tree')
                candidate = git(self.integration, 'commit-tree', tree, '-p', head, '-m',
                                'Mesh submission ' + str(row['id']) + ': ' + row['commit_sha'])
                self._save(row, 'checking', candidate_sha=candidate)
                row['candidate_sha'] = candidate
                row['status'] = 'checking'
                git(self.integration, 'reset', '--hard', candidate)
            if row['status'] == 'checking':
                if git(self.integration, 'rev-parse', 'HEAD') != row['candidate_sha']:
                    if git(self.integration, 'ls-files', '--others', '--exclude-standard'):
                        raise ValueError('Untracked files in interrupted integration')
                    git(self.integration, 'reset', '--hard', row['candidate_sha'])
                self._clean(self.integration)
                try:
                    with capacity_lock():
                        check = subprocess.run(command, cwd=self.integration, text=True,
                                               capture_output=True, timeout=300, shell=False)
                    evidence = {'command': command, 'returncode': check.returncode,
                                'stdout': check.stdout, 'stderr': check.stderr,
                                'base': head, 'candidate': row['candidate_sha'],
                                'reviews': approvals}
                except (OSError, subprocess.TimeoutExpired) as error:
                    evidence = {'command': command, 'error': str(error),
                                'base': head, 'candidate': row['candidate_sha']}
                    self._save(row, 'failed', evidence=json.dumps(evidence))
                    return {'status': 'failed', 'submission_id': row['id'], 'evidence': evidence}
                if check.returncode or git(self.integration, 'status', '--porcelain'):
                    evidence['dirty'] = git(self.integration, 'status', '--porcelain')
                    self._save(row, 'failed', evidence=json.dumps(evidence))
                    return {'status': 'failed', 'submission_id': row['id'], 'evidence': evidence}
                self._save(row, 'checked', evidence=json.dumps(evidence))
                row['status'] = 'checked'
            self._clean(self.integration)
            if git(self.integration, 'rev-parse', 'HEAD') != row['candidate_sha']:
                raise ValueError('Integration checkout no longer matches checked candidate')
            return self._land_checked(row)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', required=True)
    parser.add_argument('--repository', required=True)
    parser.add_argument('action', choices=('submit', 'review', 'land', 'status'))
    parser.add_argument('--data', default='{}')
    args = parser.parse_args()
    try:
        raw = Path(args.data[1:]).read_text(encoding='utf-8-sig') if args.data.startswith('@') else args.data
        result = getattr(Landing(args.db, args.repository), args.action)(**json.loads(raw))
    except (ValueError, TypeError) as error:
        parser.exit(2, str(error) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
