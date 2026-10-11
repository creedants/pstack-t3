"""Persistent project memory and bounded, per-request execution routing."""
import argparse
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'mesh/scripts'))
from pstack_mesh.board import Board
from pstack_mesh.landing import repository_lock

STRATEGIES = ('direct', 'delegate', 'swarm', 'mesh', 'arena', 'brigade')
PLAYBOOKS = {'feature': 'feature', 'bug': 'bug-fix', 'refactor': 'refactoring',
             'investigate': 'investigation', 'review': 'investigation',
             'performance': 'perf-issue', 'design': 'prototype',
             'skill': 'authoring-a-skill', 'ship': 'shipping', 'resume': 'session-pickup'}
for directory in (Path(__file__).resolve().parents[2] / 'poteto-mode/playbooks',
                  Path(__file__).resolve().parents[4] / 'vendor/pstack/skills/poteto-mode/playbooks'):
    if directory.is_dir():
        PLAYBOOKS.update({path.stem: path.stem for path in directory.glob('*.md')})


@dataclass(frozen=True)
class TaskFacts:
    kind: str = 'feature'
    continuing: bool = True
    writes: bool = True
    scope_known: bool = False
    small: bool = False
    independent_units: int = 1
    peer_coordination: bool = False
    retained_expertise: bool = False
    compare_candidates: bool = False
    recurring_intake: bool = False
    brigade_available: bool = os.name != 'nt'
    high_risk: bool = False
    contested: bool = False
    send_backs: int = 0
    available_peers: int = 0
    max_workers: int = 3
    explicit_strategy: str | None = None
    explicit_mode: str | None = None
    opt_out: bool = False

    @classmethod
    def parse(cls, raw):
        if not isinstance(raw, dict):
            raise ValueError('Task facts must be a JSON object')
        facts = cls(**raw)
        for name in ('continuing', 'writes', 'scope_known', 'small', 'peer_coordination',
                     'retained_expertise', 'compare_candidates', 'recurring_intake',
                     'high_risk', 'contested', 'opt_out', 'brigade_available'):
            if type(getattr(facts, name)) is not bool:
                raise ValueError(name + ' must be boolean')
        for name, minimum in (('independent_units', 1), ('send_backs', 0),
                              ('available_peers', 0), ('max_workers', 1)):
            value = getattr(facts, name)
            if type(value) is not int or value < minimum:
                raise ValueError(name + ' must be an integer >= ' + str(minimum))
        if facts.max_workers > 20:
            raise ValueError('max_workers must be <= 20')
        if facts.explicit_strategy is not None and facts.explicit_strategy not in STRATEGIES:
            raise ValueError('Unknown execution strategy')
        if facts.explicit_mode not in (None, 'light', 'full'):
            raise ValueError('Mode must be light or full')
        if facts.kind not in (*PLAYBOOKS, 'answer', 'admin'):
            raise ValueError('Unknown task kind')
        return facts


@dataclass(frozen=True)
class ExecutionPlan:
    context: str
    strategy: str
    mode: str
    playbook: str | None
    workers: int
    reason: str


def choose(facts):
    context = 'project' if facts.continuing else 'goal'
    mode = facts.explicit_mode or ('light' if facts.small and facts.scope_known else 'full')
    if facts.high_risk or facts.contested or facts.send_backs >= 2:
        mode = 'full'
    playbook = PLAYBOOKS.get(facts.kind)
    if facts.opt_out:
        return ExecutionPlan(context, 'direct', mode, None, 0, 'Explicit opt-out; no automatic worker launch')
    if facts.explicit_strategy:
        strategy, reason = facts.explicit_strategy, 'User selected this execution strategy'
        if strategy == 'brigade' and not facts.brigade_available:
            raise ValueError('Brigade requires its available POSIX runtime; use the portable project interface on this host')
    elif facts.kind in ('answer', 'admin') or (facts.small and facts.scope_known and not facts.high_risk and not facts.contested):
        strategy, reason = 'direct', 'The lead can finish this bounded task without another agent'
    elif facts.compare_candidates:
        strategy, reason = 'arena', 'The deliverable requires comparing distinct candidate implementations'
    elif facts.recurring_intake and facts.brigade_available:
        strategy, reason = 'brigade', 'The request explicitly includes standing intake and recurring service'
    elif facts.independent_units >= 2 and (facts.peer_coordination or facts.retained_expertise):
        strategy, reason = 'mesh', 'Independent units benefit from direct peer coordination or retained expertise'
    elif facts.independent_units >= 2:
        strategy, reason = 'swarm', 'Independent finite slices can return one combined report'
    else:
        strategy, reason = 'delegate', 'One bounded worker is sufficient; scope or risk needs a dedicated pass'
    workers = 0 if strategy == 'direct' else 1
    if strategy in ('swarm', 'mesh', 'arena'):
        if facts.max_workers < 2:
            raise ValueError(strategy + ' requires a worker budget of at least two')
        workers = min(max(2, facts.independent_units), facts.max_workers)
    return ExecutionPlan(context, strategy, mode, playbook, workers, reason)


def git(path, *arguments):
    result = subprocess.run(['git', '-c', 'core.longpaths=true', '-C', str(path), *arguments], text=True, capture_output=True)
    if result.returncode:
        raise ValueError(result.stderr.strip() or 'Git command failed')
    return result.stdout.strip()


SCHEMA = """
CREATE TABLE IF NOT EXISTS project (
 id INTEGER PRIMARY KEY CHECK(id=1), repository TEXT NOT NULL,
 common_dir TEXT NOT NULL, project_id TEXT NOT NULL,
 interface_thread TEXT NOT NULL, purpose TEXT NOT NULL, revision INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS facts (
 key TEXT PRIMARY KEY, value TEXT NOT NULL, source TEXT NOT NULL,
 revision INTEGER NOT NULL, updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS goals (
 id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, objective TEXT NOT NULL,
 acceptance TEXT NOT NULL, facts TEXT NOT NULL, plan TEXT NOT NULL,
 state TEXT NOT NULL, board TEXT, baseline TEXT, evidence TEXT, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS peers (
 id TEXT PRIMARY KEY, thread_id TEXT UNIQUE NOT NULL, workspace TEXT UNIQUE NOT NULL,
 branch TEXT NOT NULL, capabilities TEXT NOT NULL, updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
 data TEXT NOT NULL, created REAL NOT NULL
);
"""


class Project:
    def __init__(self, repo, state_root=None):
        supplied = Path(repo).resolve()
        self.common = Path(git(supplied, 'rev-parse', '--path-format=absolute', '--git-common-dir')).resolve()
        first = git(supplied, 'worktree', 'list', '--porcelain').splitlines()[0]
        self.repo = Path(first.removeprefix('worktree ')).resolve()
        identity = hashlib.sha256(os.path.normcase(str(self.common)).encode()).hexdigest()[:24]
        root = Path(state_root or os.environ.get('PSTACK_PROJECT_STATE', Path.home() / '.local/state/pstack-t3/projects')).resolve()
        candidate = root / identity / 'project.sqlite'
        worktrees = [Path(line.removeprefix('worktree ')).resolve() for line in git(supplied, 'worktree', 'list', '--porcelain').splitlines() if line.startswith('worktree ')]
        marker = self.common / 'pstack-project.json'
        with repository_lock(self.common / 'mesh-landing.lock'):
            if marker.exists():
                location = json.loads(marker.read_text(encoding='utf-8'))
                if not isinstance(location, dict):
                    raise ValueError('Canonical project marker must be an object')
                stored = location.get('project_database')
                if not isinstance(stored, str) or not Path(stored).is_absolute():
                    raise ValueError('Canonical project marker needs an absolute database path')
                self.database = Path(stored).resolve()
                if state_root is not None and os.path.normcase(str(candidate)) != os.path.normcase(str(self.database)):
                    raise ValueError('State root differs from the canonical project database; reopen its existing location')
            else:
                self.database = candidate
            self.directory = self.database.parent
            if any(self.directory == path or path in self.directory.parents for path in worktrees):
                raise ValueError('Project state must be outside the repository')
            self.directory.mkdir(parents=True, exist_ok=True)
            if not marker.exists():
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.common,
                                                 prefix='pstack-project-', suffix='.json', delete=False) as temporary:
                    temporary_path = Path(temporary.name)
                    json.dump({'project_database': str(self.database)}, temporary)
                    temporary.flush()
                    os.fsync(temporary.fileno())
                try:
                    os.replace(temporary_path, marker)
                finally:
                    temporary_path.unlink(missing_ok=True)
            with self.db() as db:
                db.execute('PRAGMA journal_mode=WAL')
                db.executescript(SCHEMA)

    @contextmanager
    def db(self, write=False):
        db = sqlite3.connect(self.database, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            if write:
                db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def event(db, kind, data):
        db.execute('INSERT INTO events(kind,data,created) VALUES(?,?,?)', (kind, json.dumps(data), time.time()))

    def open(self, project_id, interface_thread, purpose):
        if any(not isinstance(value, str) or not value.strip() for value in (project_id, interface_thread, purpose)):
            raise ValueError('Project ID, interface thread and purpose are required')
        with self.db(True) as db:
            row = db.execute('SELECT * FROM project').fetchone()
            if row:
                return dict(row)
            db.execute('INSERT INTO project VALUES(1,?,?,?,?,?,1)',
                       (str(self.repo), str(self.common), project_id, interface_thread, purpose))
            self.event(db, 'opened', {'interface_thread': interface_thread})
        return self.context()['project']

    def remember(self, key, value, source, expected_revision=0):
        if not key or not source or type(expected_revision) is not int or expected_revision < 0:
            raise ValueError('A fact needs a key, provenance, and nonnegative expected revision')
        with self.db(True) as db:
            row = db.execute('SELECT revision FROM facts WHERE key=?', (key,)).fetchone()
            current = row['revision'] if row else 0
            if current != expected_revision:
                raise ValueError('Fact changed; read current context before replacing it')
            db.execute('INSERT INTO facts VALUES(?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,source=excluded.source,revision=excluded.revision,updated=excluded.updated',
                       (key, json.dumps(value), source, current + 1, time.time()))
            self.event(db, 'fact_recorded', {'key': key, 'revision': current + 1, 'source': source})
        return {'key': key, 'revision': current + 1}

    def accept(self, request_key, objective, acceptance, facts):
        if not request_key or not objective or not isinstance(acceptance, list) or not acceptance:
            raise ValueError('A request needs a stable key, objective, and acceptance criteria')
        profile = TaskFacts.parse(facts)
        plan = asdict(choose(profile))
        payload = (objective, json.dumps(acceptance), json.dumps(asdict(profile), sort_keys=True))
        goal_id = hashlib.sha256(request_key.encode()).hexdigest()[:24]
        with self.db(True) as db:
            if not db.execute('SELECT 1 FROM project').fetchone():
                raise ValueError('Open the project interface before accepting requests')
            old = db.execute('SELECT * FROM goals WHERE request_key=?', (request_key,)).fetchone()
            if old:
                if (old['objective'], old['acceptance'], old['facts']) != payload:
                    raise ValueError('Request key reused with different intent or facts')
                return dict(old)
            db.execute('INSERT INTO goals VALUES(?,?,?,?,?,?,?,NULL,NULL,NULL,?)',
                       (goal_id, request_key, *payload, json.dumps(plan), 'accepted', time.time()))
            self.event(db, 'request_accepted', {'goal_id': goal_id, 'plan': plan})
            return dict(db.execute('SELECT * FROM goals WHERE id=?', (goal_id,)).fetchone())

    def peer(self, id, thread_id, workspace, capabilities=None):
        if not isinstance(id, str) or not id or not isinstance(thread_id, str) or not thread_id:
            raise ValueError('A peer needs stable, nonempty string identities')
        path = Path(workspace).resolve()
        if path == self.repo or Path(git(path, 'rev-parse', '--path-format=absolute', '--git-common-dir')).resolve() != self.common:
            raise ValueError('A peer needs an independent worktree of this repository')
        if Path(git(path, 'rev-parse', '--show-toplevel')).resolve() != path:
            raise ValueError('Peer workspace must be its actual worktree root')
        branch = git(path, 'symbolic-ref', '--short', 'HEAD')
        with self.db(True) as db:
            old = db.execute('SELECT * FROM peers WHERE id=?', (id,)).fetchone()
            if old and (old['thread_id'], old['workspace'], old['branch']) != (thread_id, str(path), branch):
                raise ValueError('Persistent peer identity cannot be rebound')
            db.execute('INSERT INTO peers VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET capabilities=excluded.capabilities,updated=excluded.updated',
                       (id, thread_id, str(path), branch, json.dumps(capabilities or []), time.time()))
            self.event(db, 'peer_registered', {'id': id, 'thread_id': thread_id})
        return {'id': id, 'thread_id': thread_id, 'workspace': str(path), 'branch': branch}

    def start_mesh(self, goal_id, tasks, check_command, target_branch=None):
        name = self.common.parent.name if self.common.name == '.git' else self.common.name.removesuffix('.git')
        legacy_key = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-') + '-' + hashlib.sha1(str(self.common).encode()).hexdigest()[:8]
        legacy_state = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'pstack-t3/landing' / legacy_key / 'land.db'
        if legacy_state.exists() or git(self.repo, 'for-each-ref', '--format=%(refname)', 'refs/landing/'):
            raise ValueError('Repository already has a pstack landing authority; do not create another queue')
        if not isinstance(tasks, list) or not tasks or not isinstance(check_command, list) or not check_command or any(not isinstance(arg, str) or not arg or '\0' in arg for arg in check_command):
            raise ValueError('Mesh needs tasks and a locally configured fixed check argv')
        if any(not isinstance(task, dict) or not isinstance(task.get('id'), str) or not task['id'].strip() for task in tasks):
            raise ValueError('Every task needs a nonempty string ID')
        ids = [task['id'] for task in tasks]
        if len(ids) != len(set(ids)):
            raise ValueError('Task IDs must be unique')
        paths = set()
        scopes = {}
        for task in tasks:
            if not isinstance(task.get('paths'), list) or not task['paths']:
                raise ValueError('Every task needs explicit relative file paths')
            scopes[task['id']] = []
            for path in task['paths']:
                if not isinstance(path, str) or not path or ':' in path or '\\' in path or path.startswith('/') or '..' in path.split('/'):
                    raise ValueError('Task scope must stay inside the repository')
                canonical = '/'.join(part for part in path.split('/') if part and part != '.')
                if not canonical or (os.name == 'nt' and any(part.endswith(('.', ' ')) for part in canonical.split('/'))):
                    raise ValueError('Task scope has an ambiguous file alias')
                alias = os.path.normcase(str((self.repo / canonical).resolve()))
                if self.repo not in (self.repo / canonical).resolve().parents:
                    raise ValueError('Task scope must stay inside the repository')
                if alias in paths:
                    raise ValueError('Writing tasks must have separate file scopes')
                paths.add(alias)
                scopes[task['id']].append(canonical)
            dependencies = task.get('dependencies', [])
            if dependencies is None:
                dependencies = []
            if not isinstance(dependencies, (list, tuple)) or any(not isinstance(item, str) or not item.strip() for item in dependencies):
                raise ValueError('Task dependencies must be string IDs')
        pending = list(tasks)
        ordered = []
        seen = set()
        while pending:
            eligible = [task for task in pending if set(task.get('dependencies') or []) <= seen]
            if not eligible:
                raise ValueError('Tasks have a dependency cycle or unknown dependency')
            for task in eligible:
                ordered.append(task)
                seen.add(task['id'])
                pending.remove(task)
        target = target_branch or git(self.repo, 'symbolic-ref', '--short', 'HEAD')
        git(self.repo, 'check-ref-format', '--branch', target)
        if git(self.repo, 'symbolic-ref', '--short', 'HEAD') != target:
            raise ValueError('The integration target must be checked out in the primary repository')
        if git(self.repo, 'status', '--porcelain'):
            raise ValueError('Commit or preserve existing changes before freezing a mesh baseline')
        baseline = git(self.repo, 'rev-parse', target + '^{commit}')
        with repository_lock(self.common / 'mesh-landing.lock'), self.db(True) as db:
            if legacy_state.exists() or git(self.repo, 'for-each-ref', '--format=%(refname)', 'refs/landing/'):
                raise ValueError('Repository already has a pstack landing authority; do not create another queue')
            if git(self.repo, 'symbolic-ref', '--short', 'HEAD') != target or git(self.repo, 'status', '--porcelain'):
                raise ValueError('Integration target changed or has uncommitted changes')
            baseline = git(self.repo, 'rev-parse', target + '^{commit}')
            goal = db.execute('SELECT * FROM goals WHERE id=?', (goal_id,)).fetchone()
            if not goal or json.loads(goal['plan'])['strategy'] != 'mesh':
                raise ValueError('Goal is not routed to mesh')
            if goal['state'] not in ('accepted', 'running'):
                raise ValueError('Terminal goal cannot start; accept a follow-up instead')
            spec = {'tasks': tasks, 'check_command': check_command, 'target_branch': target}
            if goal['board']:
                with closing(sqlite3.connect(goal['board'])) as current:
                    current.row_factory = sqlite3.Row
                    Board.require_running(current)
                    value = current.execute("SELECT value FROM run_config WHERE key='mesh_spec'").fetchone()
                    if not value or json.loads(value[0]) != spec:
                        raise ValueError('Mesh configuration is frozen; create a new goal for changed work')
                return {'board': goal['board'], 'baseline': goal['baseline']}
            if db.execute("SELECT 1 FROM goals WHERE state='running' AND board IS NOT NULL AND id!=?", (goal_id,)).fetchone():
                raise ValueError('Another mesh goal is running; accept the follow-up and start it after integration')
            directory = self.directory / 'goals' / goal_id
            board_path = directory / 'board.sqlite'
            config = {'repository': str(self.repo), 'baseline': baseline,
                      'integration_worktree': str(directory / 'integration'),
                      'integration_branch': 'pstack/mesh-' + goal_id,
                      'target_branch': target, 'check_command': check_command, 'task_scopes': scopes,
                      'max_peers': json.loads(goal['plan'])['workers'], 'state': str(directory), 'mesh_spec': spec,
                      'project_database': str(self.database), 'goal_state': 'running'}
            if board_path.exists():
                with closing(sqlite3.connect(board_path)) as current:
                    current.row_factory = sqlite3.Row
                    persisted = {key: json.loads(value) for key, value in current.execute('SELECT key,value FROM run_config')}
                    if persisted != config:
                        raise ValueError('Interrupted mesh configuration or baseline differs; preserve it and accept a new goal')
                    Board.require_running(current)
                config = persisted
            else:
                directory.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=directory, prefix='board-', suffix='.sqlite', delete=False) as temporary:
                    staged = Path(temporary.name)
                try:
                    board = Board(staged)
                    for task in ordered:
                        board.add(task['id'], task.get('title'), '; '.join(scopes[task['id']]),
                                  priority=task.get('priority', 0), dependencies=task.get('dependencies'),
                                  deadline=task.get('deadline', time.time() + 3600), max_attempts=task.get('max_attempts', 3))
                    with board.transaction() as task_db:
                        task_db.execute('CREATE TABLE IF NOT EXISTS run_config(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
                        task_db.executemany('INSERT INTO run_config VALUES(?,?)',
                                            [(key, json.dumps(value)) for key, value in config.items()])
                    with board.connect() as task_db:
                        task_db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                        task_db.execute('PRAGMA journal_mode=DELETE')
                    os.replace(staged, board_path)
                finally:
                    for suffix in ('', '-wal', '-shm', '-journal'):
                        Path(str(staged) + suffix).unlink(missing_ok=True)
            db.execute("UPDATE goals SET state='running',board=?,baseline=? WHERE id=?", (str(board_path), baseline, goal_id))
            self.event(db, 'mesh_started', {'goal_id': goal_id, 'board': str(board_path), 'baseline': baseline})
            return {'board': str(board_path), 'baseline': baseline, 'config': config}

    def prepare(self, peer_id, goal_id, observed_thread):
        if observed_thread.get('status') not in ('idle', 'completed') or observed_thread.get('activeRunId'):
            raise ValueError('Wait for the peer turn to finish before advancing its worktree')
        with repository_lock(self.common / 'mesh-landing.lock'), self.db(True) as db:
            peer = db.execute('SELECT * FROM peers WHERE id=?', (peer_id,)).fetchone()
            goal = db.execute('SELECT * FROM goals WHERE id=?', (goal_id,)).fetchone()
            if not peer or not goal or goal['state'] != 'running' or not goal['board']:
                raise ValueError('Register the peer and start the goal first')
            workspace = Path(peer['workspace'])
            if observed_thread.get('threadId') != peer['thread_id'] or Path(observed_thread.get('worktreePath') or '').resolve() != workspace:
                raise ValueError('Native observation does not match this persistent peer')
            if observed_thread.get('branch') != peer['branch'] or git(workspace, 'symbolic-ref', '--short', 'HEAD') != peer['branch']:
                raise ValueError('Peer branch changed')
            if git(workspace, 'status', '--porcelain'):
                raise ValueError('Preserve uncommitted peer changes before starting another goal')
            with closing(sqlite3.connect(goal['board'])) as board:
                board.row_factory = sqlite3.Row
                Board.require_running(board)
                if board.execute('SELECT 1 FROM tasks WHERE owner=?', (peer_id,)).fetchone():
                    raise ValueError('This peer has already worked in the goal; do not reset its authored commit')
            head = git(workspace, 'rev-parse', 'HEAD')
            if head != goal['baseline']:
                reference = 'refs/pstack/peer-history/' + goal_id + '/' + hashlib.sha256(peer_id.encode()).hexdigest()[:12]
                old = subprocess.run(['git', '-C', str(workspace), 'rev-parse', '--verify', reference], text=True, capture_output=True)
                if old.returncode == 0 and old.stdout.strip() != head:
                    raise ValueError('Peer history reference changed; inspect before resetting')
                git(workspace, 'update-ref', reference, head)
                git(workspace, 'reset', '--hard', goal['baseline'])
            self.event(db, 'peer_prepared', {'peer_id': peer_id, 'goal_id': goal_id, 'baseline': goal['baseline']})
            return {'workspace': str(workspace), 'baseline': goal['baseline'], 'branch': peer['branch']}

    def prepare_task(self, peer_id, goal_id, observed_thread, caller_thread_id=None,
                     task_id=None, claim_token=None):
        from pstack_mesh.landing import Landing, artifact_ref
        with self.db() as db:
            peer = db.execute('SELECT * FROM peers WHERE id=?', (peer_id,)).fetchone()
            goal = db.execute('SELECT * FROM goals WHERE id=?', (goal_id,)).fetchone()
        if not peer or not goal or goal['state'] != 'running' or not goal['board']:
            raise ValueError('Register the peer and start the goal first')
        workspace = Path(peer['workspace'])
        if observed_thread.get('threadId') != peer['thread_id'] or Path(observed_thread.get('worktreePath') or '').resolve() != workspace:
            raise ValueError('Native observation does not match this persistent peer')
        active = observed_thread.get('status') == 'running' and bool(observed_thread.get('activeRunId'))
        if active:
            if caller_thread_id != peer['thread_id'] or not task_id or not claim_token:
                raise ValueError('Only the running peer may prepare its own fenced task')
        elif observed_thread.get('status') not in ('idle', 'completed') or observed_thread.get('activeRunId'):
            raise ValueError('Wait for the peer turn to finish before advancing its worktree')
        if observed_thread.get('branch') != peer['branch'] or git(workspace, 'symbolic-ref', '--short', 'HEAD') != peer['branch']:
            raise ValueError('Peer branch changed')
        if git(workspace, 'status', '--porcelain'):
            raise ValueError('Preserve uncommitted peer changes before starting another task')
        board = Board(goal['board'])
        head = git(workspace, 'rev-parse', 'HEAD')
        with board.connect() as tasks:
            Board.require_running(tasks)
            completed = tasks.execute("SELECT * FROM tasks WHERE owner=? AND status='done'", (peer_id,)).fetchall()
            current = [task for task in completed if json.loads(task['result'] or '{}').get('commit') == head]
        if current:
            for task in current:
                Landing(goal['board'], self.repo).submit(task['id'], peer_id, head)
        with repository_lock(self.common / 'mesh-landing.lock'), self.db(True) as db, board.transaction() as tasks:
            latest = db.execute('SELECT * FROM goals WHERE id=?', (goal_id,)).fetchone()
            if latest['state'] != 'running':
                raise ValueError('Terminal goal cannot prepare another task')
            Board.require_running(tasks)
            claimed = tasks.execute("SELECT * FROM tasks WHERE owner=? AND status='claimed'", (peer_id,)).fetchall()
            if task_id or claim_token:
                if not task_id or not claim_token:
                    raise ValueError('Task preparation needs both task ID and claim token')
                Board._owned(tasks, task_id, peer_id, claim_token)
                if any(task['id'] != task_id for task in claimed):
                    raise ValueError('Peer holds another active claim')
            elif claimed:
                raise ValueError('Peer holds an active claim; supply its task ID and token')
            if git(workspace, 'status', '--porcelain') or git(workspace, 'rev-parse', 'HEAD') != head:
                raise ValueError('Peer checkout changed during task preparation')
            preserved = set()
            for task in tasks.execute("SELECT * FROM tasks WHERE owner=? AND status='done'", (peer_id,)):
                result = json.loads(task['result'] or '{}')
                commit = result.get('commit')
                if result.get('status') != 'PASS' or not isinstance(commit, str) or not re.fullmatch('[0-9a-f]{40}', commit):
                    raise ValueError('Prior authored tasks need finished PASS exact commits')
                reference = artifact_ref(goal['board'], task['id'], commit)
                if git(workspace, 'rev-parse', '--verify', reference) != commit:
                    raise ValueError('Prior authored commit is not preserved by its immutable artifact')
                if not tasks.execute("SELECT 1 FROM submissions WHERE task_id=? AND author=? AND commit_sha=? AND task_attempt=? AND status NOT IN ('cancelled','superseded')",
                                     (task['id'], peer_id, commit, task['attempts'])).fetchone():
                    raise ValueError('Submit prior authored tasks before preparing another unit')
                preserved.add(commit)
            if head != goal['baseline']:
                if head not in preserved:
                    raise ValueError('Current peer HEAD has no preserved finished task; use prepare for a new goal')
                git(workspace, 'reset', '--hard', goal['baseline'])
            self.event(db, 'peer_task_prepared', {'peer_id': peer_id, 'goal_id': goal_id, 'task_id': task_id, 'baseline': goal['baseline']})
        return {'workspace': str(workspace), 'baseline': goal['baseline'], 'branch': peer['branch']}

    def complete(self, goal_id, evidence):
        if not isinstance(evidence, dict) or evidence.get('status') != 'PASS' or not evidence.get('artifact'):
            raise ValueError('Completion needs PASS evidence and a reviewable artifact pointer')
        with repository_lock(self.common / 'mesh-landing.lock'), self.db(True) as db:
            goal = db.execute('SELECT * FROM goals WHERE id=?', (goal_id,)).fetchone()
            if not goal:
                raise ValueError('Unknown goal')
            if goal['state'] == 'cancelled':
                raise ValueError('Cancelled goal cannot complete; accept a follow-up instead')
            if goal['state'] == 'complete':
                if json.loads(goal['evidence']) != evidence:
                    raise ValueError('Completed goal evidence is immutable; create a follow-up')
                return {'id': goal_id, 'state': 'complete'}
            if json.loads(goal['plan'])['strategy'] == 'mesh' and (goal['state'] != 'running' or not goal['board']):
                raise ValueError('Mesh completion requires a running goal with a started board')
            if goal['board']:
                with Board(goal['board']).transaction() as board:
                    terminal = board.execute("SELECT value FROM run_config WHERE key='goal_state'").fetchone()
                    if not terminal or json.loads(terminal['value']) not in ('running', 'complete'):
                        raise ValueError('Cancelled board cannot complete; accept a follow-up instead')
                    if not board.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='submissions'").fetchone():
                        raise ValueError('The mesh landing authority has not been initialized')
                    configured = board.execute("SELECT value FROM run_config WHERE key='target_branch'").fetchone()
                    if not configured:
                        raise ValueError('Mesh target branch is missing from frozen run_config')
                    target = 'refs/heads/' + json.loads(configured['value'])
                    git(self.repo, 'check-ref-format', target)
                    target_commit = git(self.repo, 'rev-parse', '--verify', target + '^{commit}')
                    tasks = board.execute('SELECT * FROM tasks').fetchall()
                    for task in tasks:
                        result = json.loads(task['result'] or '{}')
                        if task['status'] != 'done' or result.get('status') != 'PASS':
                            raise ValueError('Mesh still has unfinished or unsuccessful tasks')
                        landed = board.execute("SELECT candidate_sha FROM submissions WHERE task_id=? AND author=? AND commit_sha=? AND task_attempt=? AND status='landed'",
                                               (task['id'], task['owner'], result.get('commit'), task['attempts'])).fetchone()
                        if not landed:
                            raise ValueError('Exact authored task commit has not landed')
                        git(self.repo, 'merge-base', '--is-ancestor', landed['candidate_sha'], target_commit)
                    board.execute("UPDATE run_config SET value=? WHERE key='goal_state'", (json.dumps('complete'),))
                    Board.event(board, 'goal_completed', data=evidence)
            db.execute("UPDATE goals SET state='complete',evidence=? WHERE id=?", (json.dumps(evidence), goal_id))
            self.event(db, 'goal_completed', {'goal_id': goal_id, 'evidence': evidence})
        return {'id': goal_id, 'state': 'complete'}

    def cancel(self, goal_id, reason):
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('Cancellation needs the user-requested reason')
        with repository_lock(self.common / 'mesh-landing.lock'), self.db(True) as db:
            goal = db.execute('SELECT * FROM goals WHERE id=?', (goal_id,)).fetchone()
            if not goal or goal['state'] == 'complete':
                raise ValueError('Goal is missing or already complete')
            if goal['state'] == 'cancelled':
                return {'id': goal_id, 'state': 'cancelled'}
            board_path = Path(goal['board']) if goal['board'] else self.directory / 'goals' / goal_id / 'board.sqlite'
            if board_path.exists():
                board = Board(board_path)
                with board.transaction() as tasks:
                    terminal = tasks.execute("SELECT value FROM run_config WHERE key='goal_state'").fetchone()
                    if terminal and json.loads(terminal['value']) == 'complete':
                        raise ValueError('Completed board cannot be cancelled')
                    tasks.execute("UPDATE run_config SET value=? WHERE key='goal_state'", (json.dumps('cancelled'),))
                    tasks.execute("UPDATE tasks SET status='cancelled',token=NULL,expires=NULL WHERE status!='done'")
                    if tasks.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='submissions'").fetchone():
                        tasks.execute("UPDATE submissions SET status='cancelled' WHERE status IN ('pending','preparing','checking','checked')")
                    Board.event(tasks, 'goal_cancelled', data={'reason': reason})
            db.execute("UPDATE goals SET state='cancelled',evidence=? WHERE id=?", (json.dumps({'reason': reason}), goal_id))
            self.event(db, 'goal_cancelled', {'goal_id': goal_id, 'reason': reason})
            return {'id': goal_id, 'state': 'cancelled'}

    def context(self, limit=20):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Context limit must be between 1 and 100')
        with self.db() as db:
            row = db.execute('SELECT * FROM project').fetchone()
            return {'project': dict(row) if row else None,
                    'facts': [dict(item) for item in db.execute('SELECT * FROM facts ORDER BY updated DESC LIMIT ?', (limit,))],
                    'goals': [dict(item) for item in db.execute('SELECT * FROM goals ORDER BY created DESC LIMIT ?', (limit,))],
                    'peers': [dict(item) for item in db.execute('SELECT * FROM peers ORDER BY id')],
                    'state': str(self.directory)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', default='.')
    parser.add_argument('--state-root')
    parser.add_argument('action', choices=('route', 'open', 'context', 'remember', 'accept', 'peer', 'start-mesh', 'prepare', 'prepare-task', 'complete', 'cancel'))
    parser.add_argument('--data', default='{}')
    args = parser.parse_args(argv)
    try:
        raw = Path(args.data[1:]).read_text(encoding='utf-8-sig') if args.data.startswith('@') else args.data
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError('Command data must be an object')
        if args.action == 'route':
            result = asdict(choose(TaskFacts.parse(data)))
        else:
            project = Project(args.repo, args.state_root)
            result = getattr(project, args.action.replace('-', '_'))(**data)
    except (ValueError, TypeError, OSError, sqlite3.Error) as error:
        parser.exit(2, str(error) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
